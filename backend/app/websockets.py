"""WebSocket signalling for callers and officer consoles."""

from __future__ import annotations

import base64
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app import triage
from backend.app.config import MAX_TRIAGE_BYTES, OFFICER_ID_RE
from backend.app.security import verify_call_token, websocket_officer
from backend.app.state import call_manager

logger = logging.getLogger("cers.ws")
router = APIRouter()


def _valid_call_id(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 4
    except (ValueError, AttributeError, TypeError):
        return False


@router.websocket("/ws/caller/{call_id}")
async def caller_websocket(websocket: WebSocket, call_id: str):
    token = websocket.query_params.get("token")
    if not _valid_call_id(call_id) or call_manager.get_call(call_id) is None:
        await websocket.close(code=1008, reason="Unknown call session.")
        return
    # The call_id travels through URLs; the token proves the dialer owns it.
    if not verify_call_token(call_id, token):
        await websocket.close(code=1008, reason="Invalid call token.")
        return

    await websocket.accept()
    if not await call_manager.register_caller(call_id, websocket):
        await websocket.close(code=1008, reason="Call session is no longer active.")
        return
    logger.info("Caller connected: %s", call_id)

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            action = data.get("action")

            if action == "PING":
                await websocket.send_json({"event": "PONG"})
                continue
            if action == "IVR_STARTED":
                await call_manager.update_call_status(call_id, "IVR")
            elif action == "IVR_FINISHED":
                # Greeting ended: the caller is now recording their statement.
                # The session must become claimable straight away; leaving it
                # in IVR used to strand it behind a disabled claim button.
                await call_manager.update_call_status(call_id, "WAITING")
            elif action == "TRIAGE_AUDIO":
                audio_b64 = data.get("audio", "")
                mime_type = data.get("mime", "audio/webm")
                if not isinstance(audio_b64, str) or not audio_b64:
                    continue
                try:
                    audio_bytes = base64.b64decode(audio_b64, validate=True)
                except Exception as err:
                    logger.warning("Bad triage audio for %s: %s", call_id, err)
                    continue
                if len(audio_bytes) > MAX_TRIAGE_BYTES:
                    logger.warning("Triage audio too large for %s", call_id)
                    continue
                if not triage.looks_like_wav(audio_bytes):
                    logger.warning("Triage audio is not WAV for %s", call_id)
                    continue
                await triage.schedule_triage(call_id, audio_bytes, mime_type)
            elif action == "WEBRTC_SIGNAL":
                call = call_manager.get_call(call_id)
                if call and call.officer_id:
                    await call_manager.relay_webrtc(
                        "officer", call.officer_id, data.get("payload")
                    )
            elif action == "END_CALL":
                await call_manager.end_call(call_id, actor="caller")
                break

    except WebSocketDisconnect:
        logger.info("Caller disconnected: %s", call_id)
    except Exception as exc:
        logger.warning("Error in caller socket %s: %s", call_id, exc)
    finally:
        await call_manager.unregister_caller(call_id, websocket)


@router.websocket("/ws/officer/{officer_id}")
async def officer_websocket(websocket: WebSocket, officer_id: str):
    session_officer = websocket_officer(websocket)
    if (
        not session_officer
        or session_officer != officer_id
        or not OFFICER_ID_RE.fullmatch(officer_id)
    ):
        await websocket.close(code=1008, reason="Officer authentication required.")
        return

    await websocket.accept()
    await call_manager.register_officer(officer_id, websocket)
    logger.info("Officer connected: %s", officer_id)

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            action = data.get("action")
            call_id = data.get("call_id")

            if action == "PING":
                await websocket.send_json({"event": "PONG"})
                continue
            if action == "CLAIM_CALL":
                if not isinstance(call_id, str):
                    await websocket.send_json(
                        {
                            "event": "CLAIM_RESULT",
                            "call_id": call_id,
                            "success": False,
                            "reason": "Missing call id.",
                        }
                    )
                    continue
                success, reason = await call_manager.claim_call(call_id, officer_id)
                await websocket.send_json(
                    {
                        "event": "CLAIM_RESULT",
                        "call_id": call_id,
                        "success": success,
                        "reason": reason,
                    }
                )
            elif action == "RELEASE_CALL":
                if isinstance(call_id, str):
                    success, reason = await call_manager.release_call(
                        call_id, officer_id, reason="officer_released"
                    )
                    if not success:
                        await websocket.send_json(
                            {"event": "ERROR", "call_id": call_id, "message": reason}
                        )
            elif action == "WEBRTC_SIGNAL":
                if isinstance(call_id, str):
                    call = call_manager.get_call(call_id)
                    if not call or call.officer_id != officer_id:
                        await websocket.send_json(
                            {
                                "event": "ERROR",
                                "call_id": call_id,
                                "message": "You are not assigned to this call.",
                            }
                        )
                        continue
                    await call_manager.relay_webrtc(
                        "caller", call_id, data.get("payload")
                    )
            elif action == "END_CALL":
                if isinstance(call_id, str):
                    success, reason = await call_manager.end_call(
                        call_id, officer_id=officer_id, actor="officer"
                    )
                    if not success:
                        await websocket.send_json(
                            {"event": "ERROR", "call_id": call_id, "message": reason}
                        )
            elif action == "SYNC_QUEUE":
                await call_manager.send_queue_snapshot(officer_id)

    except WebSocketDisconnect:
        logger.info("Officer disconnected: %s", officer_id)
    except Exception as exc:
        logger.warning("Error in officer socket %s: %s", officer_id, exc)
    finally:
        await call_manager.unregister_officer(officer_id, websocket)
