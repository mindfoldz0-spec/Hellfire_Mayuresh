"""WebSocket endpoints for caller and officer real-time signaling."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.config import OFFICER_ID_RE
from backend.app.security import websocket_officer
from backend.app.state import call_manager

router = APIRouter()


def _valid_call_id(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 4
    except (ValueError, AttributeError):
        return False


@router.websocket("/ws/caller/{call_id}")
async def caller_websocket(websocket: WebSocket, call_id: str):
    if not _valid_call_id(call_id) or call_manager.get_call(call_id) is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    if not await call_manager.register_caller(call_id, websocket):
        await websocket.close(code=1008)
        return
    print(f"[WS] Caller connected: {call_id}")

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            action = data.get("action")

            if action == "IVR_STARTED":
                await call_manager.update_call_status(call_id, "IVR")
            elif action == "IVR_FINISHED":
                await call_manager.update_call_status(call_id, "WAITING")
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
        print(f"[WS] Caller disconnected: {call_id}")
    except Exception as exc:
        print(f"[WS] Error in caller socket {call_id}: {exc}")
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
        await websocket.close(code=1008)
        return

    await websocket.accept()
    await call_manager.register_officer(officer_id, websocket)
    print(f"[WS] Officer connected: {officer_id}")

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            action = data.get("action")
            call_id = data.get("call_id")

            if action == "CLAIM_CALL":
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
                if call_id:
                    success, reason = await call_manager.release_call(
                        call_id, officer_id, reason="officer_released"
                    )
                    if not success:
                        await websocket.send_json(
                            {"event": "ERROR", "call_id": call_id, "message": reason}
                        )
            elif action == "WEBRTC_SIGNAL":
                if call_id:
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
                if call_id:
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
        print(f"[WS] Officer disconnected: {officer_id}")
    except Exception as exc:
        print(f"[WS] Error in officer socket {officer_id}: {exc}")
    finally:
        await call_manager.unregister_officer(officer_id, websocket)
