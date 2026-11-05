from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from backend.app.config import CALLER_DISCONNECT_GRACE_SECONDS, MAX_ACTIVE_CALLS
from backend.app.metrics import metrics

# Statuses during which an officer may still claim the session.
_CLAIMABLE = {"RINGING", "IVR", "WAITING"}


def _coerce_str_list(value: Any) -> List[str]:
    """LLM output is untrusted: never let a bare string become a list of chars."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


class CapacityError(RuntimeError):
    """Raised when the server is at its active-call limit."""


class CallState:
    def __init__(self, dialed_number: str, caller_number: str = ""):
        # The session ID is server-generated and unguessable. The dialed
        # number is only display metadata; it is never used as a key.
        self.call_id = str(uuid.uuid4())
        self.dialed_number = dialed_number
        # Optional real caller identity (MSISDN/ANI) when telephony provides
        # it; used for repeat-caller detection in the vault.
        self.caller_number = caller_number
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
        self.scam_indicators: List[str] = []
        self.officer_sop: List[str] = []
        # Escalation: set once when a high-priority call ages past its SLA.
        self.escalated_at: Optional[float] = None
        self.escalation_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        started = self.engaged_at or self.created_at
        finished = self.ended_at or time.time()
        return {
            "call_id": self.call_id,
            "dialed_number": self.dialed_number,
            "caller_number": self.caller_number,
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
            "emotions": dict(self.emotions),
            "scam_indicators": list(self.scam_indicators),
            "officer_sop": list(self.officer_sop),
            "responsible_ai_advisory": self.responsible_ai_advisory,
            "escalated_at": self.escalated_at,
            "escalation_count": self.escalation_count,
        }


class CallManager:
    def __init__(self, caller_disconnect_grace: float = 0.0) -> None:
        self.calls: Dict[str, CallState] = {}
        self.caller_sockets: Dict[str, WebSocket] = {}
        self.officer_sockets: Dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()
        self.caller_disconnect_grace = caller_disconnect_grace
        self._disconnect_tasks: Dict[str, asyncio.Task] = {}
        self._send_locks: Dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Socket send serialization
    # ------------------------------------------------------------------
    def _send_lock(self, websocket: WebSocket) -> asyncio.Lock:
        """One lock per socket: ASGI sends must not interleave."""
        lock = self._send_locks.get(id(websocket))
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[id(websocket)] = lock
        return lock

    async def _send(self, websocket: WebSocket, message: Dict[str, Any]) -> bool:
        try:
            async with self._send_lock(websocket):
                await asyncio.wait_for(websocket.send_json(message), timeout=6.0)
            return True
        except Exception:
            return False

    def _forget_socket(self, websocket: WebSocket) -> None:
        self._send_locks.pop(id(websocket), None)

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------
    async def create_call(
        self,
        dialed_number: str,
        max_active: Optional[int] = None,
        caller_number: str = "",
    ) -> CallState:
        limit = MAX_ACTIVE_CALLS if max_active is None else max_active
        async with self.lock:
            active = sum(1 for c in self.calls.values() if c.status != "ENDED")
            if active >= limit:
                raise CapacityError("The dispatch gateway is at capacity.")
            call = CallState(dialed_number, caller_number)
            self.calls[call.call_id] = call
            metrics.incr("calls_created")

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
    def _cancel_disconnect_task(self, call_id: str) -> None:
        task = self._disconnect_tasks.pop(call_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def register_caller(self, call_id: str, websocket: WebSocket) -> bool:
        """Attach a caller socket to an existing session.

        The caller must present the per-call capability token (verified by the
        WebSocket layer) before reaching here, so a reconnect replaces the
        previous socket only for the legitimate caller. Cleanup is keyed by
        socket identity so an old socket cannot remove a new one.
        """
        async with self.lock:
            call = self.calls.get(call_id)
            if not call or call.status == "ENDED":
                return False
            previous = self.caller_sockets.get(call_id)
            self.caller_sockets[call_id] = websocket
        self._cancel_disconnect_task(call_id)

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
            self._forget_socket(websocket)
            call = self.calls.get(call_id)
            should_end = call is not None and call.status != "ENDED"

        if not should_end:
            return

        # A transient network blip must not destroy the session. Wait out a
        # short grace period in which the caller can re-attach the socket.
        if self.caller_disconnect_grace > 0:
            self._cancel_disconnect_task(call_id)
            self._disconnect_tasks[call_id] = asyncio.create_task(
                self._end_after_grace(call_id)
            )
            return

        await self.end_call(call_id, actor="caller")

    async def _end_after_grace(self, call_id: str) -> None:
        try:
            await asyncio.sleep(self.caller_disconnect_grace)
        except asyncio.CancelledError:
            return
        self._disconnect_tasks.pop(call_id, None)
        async with self.lock:
            # Someone reconnected while we were waiting.
            if call_id in self.caller_sockets:
                return
            call = self.calls.get(call_id)
            if call is None or call.status == "ENDED":
                return
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

        await self._send(
            websocket,
            {"event": "QUEUE_SNAPSHOT", "calls": snapshot, "server_time": time.time()},
        )

    async def unregister_officer(self, officer_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            if self.officer_sockets.get(officer_id) is not websocket:
                return
            del self.officer_sockets[officer_id]
            self._forget_socket(websocket)
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
            await self._send(
                websocket,
                {
                    "event": "QUEUE_SNAPSHOT",
                    "calls": snapshot,
                    "server_time": time.time(),
                },
            )

    def _snapshot_locked(self) -> List[Dict[str, Any]]:
        active = [c for c in self.calls.values() if c.status != "ENDED"]
        # Sort by urgency/priority score descending, then created_at ascending
        active.sort(
            key=lambda c: (
                -getattr(c, "triage_priority_score", 10),
                c.created_at,
            )
        )
        return [c.to_dict() for c in active]

    async def update_call_triage(
        self, call_id: str, triage_data: Dict[str, Any], *, partial: bool = False
    ) -> None:
        """Merge AI triage data without ever regressing the call lifecycle.

        Triage runs asynchronously and can land *after* an officer has already
        claimed the call (or after it ended). Only the data is merged in that
        case — the status transition is restricted to pre-engagement states.

        ``partial=True`` is used by live in-call windows: it refreshes the
        numbers (and broadcast as ``CALL_TRIAGE_UPDATED``) but never touches
        status, so an engaged call stays with its officer.
        """
        status_changed = False
        async with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return
            if call.status == "ENDED":
                # Never resurrect an archived session.
                return

            # Merge, don't reset: a live window may carry only some fields.
            for field_name in (
                "triage_priority_score",
                "urgency_level",
                "urgency_badge",
                "incident_category",
                "incident_summary",
                "transcript",
                "emotions",
                "responsible_ai_advisory",
            ):
                if field_name in triage_data and triage_data[field_name] is not None:
                    setattr(call, field_name, triage_data[field_name])
            if "scam_indicators" in triage_data:
                call.scam_indicators = _coerce_str_list(triage_data["scam_indicators"])
            if "officer_sop" in triage_data:
                call.officer_sop = _coerce_str_list(triage_data["officer_sop"])
            if triage_data.get("caller_number"):
                call.caller_number = str(triage_data["caller_number"])

            if not partial and call.status in _CLAIMABLE:
                call.status = "WAITING"  # triage finished, now ready for dispatch
                status_changed = True

            snapshot = call.to_dict()

        if partial:
            await self.broadcast_to_officers(
                {"event": "CALL_TRIAGE_UPDATED", "call": snapshot}
            )
            return

        await self.broadcast_to_officers({"event": "CALL_TRIAGED", "call": snapshot})
        metrics.observe_category(snapshot.get("incident_category", ""))
        if status_changed:
            await self.broadcast_to_officers(
                {"event": "STATUS_CHANGED", "call_id": call_id, "status": "WAITING"}
            )

    async def escalate_stale_calls(
        self, *, priority_min: int = 85, older_than: float = 30.0
    ) -> List[Dict[str, Any]]:
        """Flag high-priority calls that nobody has picked up yet.

        A 95/100 call waiting like a 10/100 call defeats the whole scoring
        model, so the sweeper turns the score into an actual alarm.
        """
        now = time.time()
        escalated: List[Dict[str, Any]] = []
        async with self.lock:
            for call in self.calls.values():
                if call.status not in _CLAIMABLE:
                    continue
                if call.triage_priority_score < priority_min:
                    continue
                if call.escalated_at is not None:
                    continue  # already alerted; do not spam
                if now - call.created_at < older_than:
                    continue
                call.escalated_at = now
                call.escalation_count += 1
                escalated.append(call.to_dict())

        for payload in escalated:
            metrics.incr("escalations")
            await self.broadcast_to_officers(
                {"event": "CALL_ESCALATED", "call": payload}
            )
        return escalated


    # ------------------------------------------------------------------
    # Status and claims
    # ------------------------------------------------------------------
    async def update_call_status(self, call_id: str, status: str) -> None:
        if status not in _CLAIMABLE:
            return
        changed = False
        async with self.lock:
            call = self.calls.get(call_id)
            if not call or call.status in {"ENDED", "ENGAGED"}:
                return
            if call.status != status:
                call.status = status
                changed = True
        if not changed:
            return

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
            metrics.observe_claim(
                call.triage_priority_score, call.engaged_at - call.created_at
            )
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
            await self._send(
                caller_ws,
                {
                    "event": "OFFICER_ASSIGNED",
                    "call_id": call_id,
                    "officer_id": officer_id,
                },
            )

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
            await self._send(
                caller_ws,
                {
                    "event": "CALL_RELEASED",
                    "call_id": call_id,
                    "reason": reason,
                },
            )

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

            if actor == "officer" and (
                not call.officer_id or call.officer_id != officer_id
            ):
                return False, "You are not assigned to this call."

            call.status = "ENDED"
            call.ended_at = time.time()
            metrics.incr("calls_ended")
            caller_ws = self.caller_sockets.get(call_id)
            status = call.status

        self._cancel_disconnect_task(call_id)
        await self.broadcast_to_officers(
            {"event": "CALL_ENDED", "call_id": call_id, "status": status}
        )
        if caller_ws:
            await self._send(
                caller_ws, {"event": "CALL_ENDED", "call_id": call_id}
            )

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
        return await self._send(websocket, {"event": "WEBRTC_SIGNAL", "payload": payload})

    async def broadcast_to_officers(self, message: Dict[str, Any]) -> None:
        async with self.lock:
            sockets = list(self.officer_sockets.items())

        if not sockets:
            return

        results = await asyncio.gather(
            *(self._send(ws, message) for _, ws in sockets)
        )
        async with self.lock:
            for (officer_id, websocket), ok in zip(sockets, results, strict=False):
                if not ok and self.officer_sockets.get(officer_id) is websocket:
                    self.officer_sockets.pop(officer_id, None)
                    self._forget_socket(websocket)

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
                elif (
                    call_id not in self.caller_sockets
                    and call_id not in self._disconnect_tasks
                    and now - call.created_at > abandoned_ttl
                ) or (
                    call.status == "ENGAGED"
                    and call.officer_id not in self.officer_sockets
                    and now - (call.engaged_at or call.created_at) > abandoned_ttl
                ):
                    removed.append(call_id)

            for call_id in removed:
                self.calls.pop(call_id, None)
                socket = self.caller_sockets.pop(call_id, None)
                if socket is not None:
                    self._forget_socket(socket)

        for call_id in removed:
            self._cancel_disconnect_task(call_id)

        # Let officer consoles drop cards for calls that were purged so their
        # queue does not keep showing a session that no longer exists.
        for call_id in removed:
            await self.broadcast_to_officers(
                {"event": "CALL_ENDED", "call_id": call_id, "status": "ENDED"}
            )
        return removed


# Global singleton instance
call_manager = CallManager(caller_disconnect_grace=CALLER_DISCONNECT_GRACE_SECONDS)
