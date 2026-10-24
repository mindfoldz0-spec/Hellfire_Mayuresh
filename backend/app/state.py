from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket


class CapacityError(RuntimeError):
    """Raised when the server is at its active-call limit."""


class CallState:
    def __init__(self, dialed_number: str):
        # The session ID is server-generated and unguessable. The dialed
        # number is only display metadata; it is never used as a key.
        self.call_id = str(uuid.uuid4())
        self.dialed_number = dialed_number
        self.status = "RINGING"  # RINGING, IVR, WAITING, ENGAGED, ENDED
        self.officer_id: Optional[str] = None
        self.created_at = time.time()
        self.engaged_at: Optional[float] = None
        self.ended_at: Optional[float] = None
        self.recording_url: Optional[str] = None
        self.notes: str = ""
        # Fused AI Triage and Speech Emotion Recognition
        self.triage_priority_score: int = 10
        self.urgency_level: str = "ROUTINE"
        self.urgency_badge: str = "🟢 ROUTINE"
        self.incident_category: str = "PENDING_TRIAGE"
        self.incident_summary: str = "Awaiting caller statement..."
        self.transcript: str = ""
        self.emotions: Dict[str, Any] = {
            "fear": 0.0,
            "distress": 0.0,
            "anger": 0.0,
            "nervousness": 0.0,
            "neutral": 1.0,
            "dominant": "neutral",
            "vocal_distress_score": 0.0,
        }
        self.responsible_ai_advisory: str = (
            "Responsible AI Advisory: Emotion predictions are indicative signals for "
            "call triage prioritization only, not legal evidence of honesty, guilt, or intent."
        )

    def to_dict(self) -> Dict[str, Any]:
        started = self.engaged_at or self.created_at
        finished = self.ended_at or time.time()
        return {
            "call_id": self.call_id,
            "dialed_number": self.dialed_number,
            "status": self.status,
            "officer_id": self.officer_id,
            "created_at": self.created_at,
            "engaged_at": self.engaged_at,
            "ended_at": self.ended_at,
            "duration": round(max(0.0, finished - started), 1),
            "recording_url": self.recording_url,
            "notes": self.notes,
            "triage_priority_score": self.triage_priority_score,
            "urgency_level": self.urgency_level,
            "urgency_badge": self.urgency_badge,
            "incident_category": self.incident_category,
            "incident_summary": self.incident_summary,
            "transcript": self.transcript,
            "emotions": self.emotions,
            "responsible_ai_advisory": self.responsible_ai_advisory,
        }


class CallManager:
    def __init__(self) -> None:
        self.calls: Dict[str, CallState] = {}
        self.caller_sockets: Dict[str, WebSocket] = {}
        self.officer_sockets: Dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------
    async def create_call(self, dialed_number: str, max_active: int = 500) -> CallState:
        async with self.lock:
            active = sum(1 for c in self.calls.values() if c.status != "ENDED")
            if active >= max_active:
                raise CapacityError("The dispatch gateway is at capacity.")
            call = CallState(dialed_number)
            self.calls[call.call_id] = call

        # Notify the queue as soon as the session exists. The caller socket
        # normally attaches immediately afterwards.
        await self.broadcast_to_officers({"event": "NEW_CALL", "call": call.to_dict()})
        return call

    def get_call(self, call_id: str) -> Optional[CallState]:
        return self.calls.get(call_id)

    def active_call_count(self) -> int:
        return sum(1 for c in self.calls.values() if c.status != "ENDED")

    async def attach_recording(self, call_id: str, recording_url: str, notes: str = "") -> None:
        async with self.lock:
            call = self.calls.get(call_id)
            if call:
                call.recording_url = recording_url
                if notes:
                    call.notes = notes

    # ------------------------------------------------------------------
    # Caller sockets
    # ------------------------------------------------------------------
    async def register_caller(self, call_id: str, websocket: WebSocket) -> bool:
        """Attach a caller socket to an existing session.

        A caller may reconnect, but the call state is never reset and a
        second caller cannot take over an engaged call. Cleanup is keyed
        by socket identity so an old socket cannot remove a new one.
        """
        async with self.lock:
            call = self.calls.get(call_id)
            if not call or call.status == "ENDED":
                return False
            previous = self.caller_sockets.get(call_id)
            self.caller_sockets[call_id] = websocket

        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4001)
            except Exception:
                pass

        return True

    async def unregister_caller(self, call_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            # Only the current socket may clean up the session.
            if self.caller_sockets.get(call_id) is not websocket:
                return
            del self.caller_sockets[call_id]
            call = self.calls.get(call_id)
            should_end = call is not None and call.status != "ENDED"

        if should_end:
            await self.end_call(call_id, actor="caller")

    # ------------------------------------------------------------------
    # Officer sockets
    # ------------------------------------------------------------------
    async def register_officer(self, officer_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            previous = self.officer_sockets.get(officer_id)
            self.officer_sockets[officer_id] = websocket
            snapshot = self._snapshot_locked()

        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4001)
            except Exception:
                pass

        try:
            await websocket.send_json({"event": "QUEUE_SNAPSHOT", "calls": snapshot})
        except Exception:
            pass

    async def unregister_officer(self, officer_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            if self.officer_sockets.get(officer_id) is not websocket:
                return
            del self.officer_sockets[officer_id]
            owned = [
                c.call_id
                for c in self.calls.values()
                if c.status == "ENGAGED" and c.officer_id == officer_id
            ]

        # A disconnected officer must not leave callers locked out.
        for call_id in owned:
            await self.release_call(call_id, officer_id, reason="officer_disconnected")

    async def send_queue_snapshot(self, officer_id: str) -> None:
        async with self.lock:
            websocket = self.officer_sockets.get(officer_id)
            snapshot = self._snapshot_locked()
        if websocket:
            try:
                await websocket.send_json({"event": "QUEUE_SNAPSHOT", "calls": snapshot})
            except Exception:
                pass

    def _snapshot_locked(self) -> list[Dict[str, Any]]:
        active = [c for c in self.calls.values() if c.status != "ENDED"]
        # Sort by urgency/priority score descending, then created_at ascending
        active.sort(
            key=lambda c: (
                -getattr(c, "triage_priority_score", 10),
                c.created_at,
            )
        )
        return [c.to_dict() for c in active]

    async def update_call_triage(self, call_id: str, triage_data: Dict[str, Any]) -> None:
        async with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return
            call.triage_priority_score = triage_data.get("triage_priority_score", 10)
            call.urgency_level = triage_data.get("urgency_level", "ROUTINE")
            call.urgency_badge = triage_data.get("urgency_badge", "🟢 ROUTINE")
            call.incident_category = triage_data.get("incident_category", "CYBER REPORT")
            call.incident_summary = triage_data.get("incident_summary", "")
            call.transcript = triage_data.get("transcript", "")
            call.emotions = triage_data.get("emotions", call.emotions)
            call.status = "WAITING"  # Triage finished, now ready for officer dispatch!
            call.responsible_ai_advisory = triage_data.get(
                "responsible_ai_advisory", call.responsible_ai_advisory
            )

        # Broadcast real-time priority update to all officer consoles
        await self.broadcast_to_officers(
            {
                "event": "CALL_TRIAGED",
                "call": call.to_dict(),
            }
        )
        await self.broadcast_to_officers(
            {"event": "STATUS_CHANGED", "call_id": call_id, "status": "WAITING"}
        )

    # ------------------------------------------------------------------
    # Status and claims
    # ------------------------------------------------------------------
    async def update_call_status(self, call_id: str, status: str) -> None:
        if status not in {"RINGING", "IVR", "WAITING"}:
            return
        async with self.lock:
            call = self.calls.get(call_id)
            if not call or call.status in {"ENDED", "ENGAGED"}:
                return
            call.status = status

        await self.broadcast_to_officers(
            {"event": "STATUS_CHANGED", "call_id": call_id, "status": status}
        )

    async def claim_call(self, call_id: str, officer_id: str) -> tuple[bool, str]:
        async with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return False, "Call not found."
            if call.status == "ENDED":
                return False, "Call has already ended."
            if call.status == "ENGAGED":
                if call.officer_id == officer_id:
                    return False, "You are already on this call."
                return False, f"Call already claimed by {call.officer_id}."

            # One active call per officer prevents overwritten sessions and
            # lost recordings.
            for other in self.calls.values():
                if (
                    other.call_id != call_id
                    and other.status == "ENGAGED"
                    and other.officer_id == officer_id
                ):
                    return False, "You are already engaged on another call."

            call.status = "ENGAGED"
            call.officer_id = officer_id
            call.engaged_at = time.time()
            caller_ws = self.caller_sockets.get(call_id)

        await self.broadcast_to_officers(
            {
                "event": "CALL_CLAIMED",
                "call_id": call_id,
                "officer_id": officer_id,
                "status": "ENGAGED",
            }
        )

        if caller_ws:
            try:
                await caller_ws.send_json(
                    {
                        "event": "OFFICER_ASSIGNED",
                        "call_id": call_id,
                        "officer_id": officer_id,
                    }
                )
            except Exception:
                pass

        return True, "Call claimed successfully."

    async def release_call(
        self, call_id: str, officer_id: str, reason: str = "released"
    ) -> tuple[bool, str]:
        async with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return False, "Call not found."
            if call.status == "ENDED":
                return False, "Call has already ended."
            if call.officer_id != officer_id:
                return False, "You are not assigned to this call."

            call.status = "WAITING"
            call.officer_id = None
            call.engaged_at = None
            caller_ws = self.caller_sockets.get(call_id)

        await self.broadcast_to_officers(
            {
                "event": "CALL_RELEASED",
                "call_id": call_id,
                "officer_id": officer_id,
                "reason": reason,
                "status": "WAITING",
            }
        )
        if caller_ws:
            try:
                await caller_ws.send_json(
                    {
                        "event": "CALL_RELEASED",
                        "call_id": call_id,
                        "reason": reason,
                    }
                )
            except Exception:
                pass

        return True, "Call released back to the queue."

    async def end_call(
        self,
        call_id: str,
        *,
        officer_id: Optional[str] = None,
        actor: str = "officer",
    ) -> tuple[bool, str]:
        async with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return False, "Call not found."
            if call.status == "ENDED":
                # Idempotent: do not broadcast a second CALL_ENDED.
                return True, "Call already ended."

            if actor == "officer":
                if not call.officer_id or call.officer_id != officer_id:
                    return False, "You are not assigned to this call."

            call.status = "ENDED"
            call.ended_at = time.time()
            caller_ws = self.caller_sockets.get(call_id)

        await self.broadcast_to_officers({"event": "CALL_ENDED", "call_id": call_id})
        if caller_ws:
            try:
                await caller_ws.send_json({"event": "CALL_ENDED", "call_id": call_id})
            except Exception:
                pass

        return True, "Call ended."

    # ------------------------------------------------------------------
    # WebRTC relay and broadcasts
    # ------------------------------------------------------------------
    async def relay_webrtc(
        self, recipient_type: str, target_id: str, payload: Optional[Dict[str, Any]]
    ) -> bool:
        if not isinstance(payload, dict):
            return False
        if recipient_type == "caller":
            websocket = self.caller_sockets.get(target_id)
        else:
            websocket = self.officer_sockets.get(target_id)
        if not websocket:
            return False
        try:
            await websocket.send_json({"event": "WEBRTC_SIGNAL", "payload": payload})
            return True
        except Exception:
            return False

    async def broadcast_to_officers(self, message: Dict[str, Any]) -> None:
        async with self.lock:
            sockets = list(self.officer_sockets.items())

        if not sockets:
            return

        async def send(websocket: WebSocket) -> bool:
            try:
                await asyncio.wait_for(websocket.send_json(message), timeout=2.0)
                return True
            except Exception:
                return False

        results = await asyncio.gather(*(send(ws) for _, ws in sockets))
        async with self.lock:
            for (officer_id, websocket), ok in zip(sockets, results):
                if not ok and self.officer_sockets.get(officer_id) is websocket:
                    self.officer_sockets.pop(officer_id, None)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    async def cleanup_stale_calls(
        self, ended_ttl: float = 3600.0, abandoned_ttl: float = 300.0
    ) -> list[str]:
        now = time.time()
        removed: list[str] = []
        async with self.lock:
            for call_id, call in list(self.calls.items()):
                if call.status == "ENDED":
                    if call.ended_at is not None and now - call.ended_at > ended_ttl:
                        removed.append(call_id)
                elif call_id not in self.caller_sockets and now - call.created_at > abandoned_ttl:
                    removed.append(call_id)
                elif (
                    call.status == "ENGAGED"
                    and call.officer_id not in self.officer_sockets
                    and now - (call.engaged_at or call.created_at) > abandoned_ttl
                ):
                    removed.append(call_id)

            for call_id in removed:
                self.calls.pop(call_id, None)
                self.caller_sockets.pop(call_id, None)

        # Let officer consoles drop cards for calls that were purged so their
        # queue does not keep showing a session that no longer exists.
        for call_id in removed:
            await self.broadcast_to_officers({"event": "CALL_ENDED", "call_id": call_id})
        return removed


# Global singleton instance
call_manager = CallManager()
