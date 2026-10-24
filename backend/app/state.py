import asyncio
import time
from typing import Dict, Optional, Any
from fastapi import WebSocket

class CallState:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self.status = "RINGING"  # RINGING, IVR, WAITING, ENGAGED, ENDED
        self.officer_id: Optional[str] = None
        self.created_at = time.time()
        self.engaged_at: Optional[float] = None
        self.ended_at: Optional[float] = None
        self.recording_url: Optional[str] = None
        self.urgency_score: int = 0
        self.notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "status": self.status,
            "officer_id": self.officer_id,
            "created_at": self.created_at,
            "engaged_at": self.engaged_at,
            "ended_at": self.ended_at,
            "duration": round((self.ended_at or time.time()) - (self.engaged_at or self.created_at), 1),
            "recording_url": self.recording_url,
            "urgency_score": self.urgency_score,
            "notes": self.notes
        }

class CallManager:
    def __init__(self):
        self.calls: Dict[str, CallState] = {}
        self.caller_sockets: Dict[str, WebSocket] = {}
        self.officer_sockets: Dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()

    async def register_caller(self, call_id: str, websocket: WebSocket):
        async with self.lock:
            self.caller_sockets[call_id] = websocket
            if call_id not in self.calls or self.calls[call_id].status == "ENDED":
                self.calls[call_id] = CallState(call_id)
            else:
                self.calls[call_id].status = "RINGING"

        # Broadcast new call to all connected officers
        await self.broadcast_to_officers({
            "event": "NEW_CALL",
            "call": self.calls[call_id].to_dict()
        })

    async def unregister_caller(self, call_id: str):
        async with self.lock:
            if call_id in self.caller_sockets:
                del self.caller_sockets[call_id]
            if call_id in self.calls:
                call = self.calls[call_id]
                if call.status != "ENDED":
                    call.status = "ENDED"
                    call.ended_at = time.time()

        await self.broadcast_to_officers({
            "event": "CALL_ENDED",
            "call_id": call_id
        })

    async def register_officer(self, officer_id: str, websocket: WebSocket):
        async with self.lock:
            self.officer_sockets[officer_id] = websocket
            snapshot = [c.to_dict() for c in self.calls.values() if c.status != "ENDED"]

        # Send current queue snapshot to newly connected officer
        await websocket.send_json({
            "event": "QUEUE_SNAPSHOT",
            "calls": snapshot
        })

    async def unregister_officer(self, officer_id: str):
        async with self.lock:
            if officer_id in self.officer_sockets:
                del self.officer_sockets[officer_id]

    async def update_call_status(self, call_id: str, status: str):
        async with self.lock:
            if call_id in self.calls:
                self.calls[call_id].status = status

        await self.broadcast_to_officers({
            "event": "STATUS_CHANGED",
            "call_id": call_id,
            "status": status
        })

    async def claim_call(self, call_id: str, officer_id: str) -> tuple[bool, str]:
        """
        Atomic lockout check:
        Ensures only one officer can successfully claim the call.
        """
        async with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return False, "Call not found."
            if call.status == "ENGAGED":
                return False, f"Call already claimed by {call.officer_id}."
            if call.status == "ENDED":
                return False, "Call has already ended."

            call.status = "ENGAGED"
            call.officer_id = officer_id
            call.engaged_at = time.time()

        # Notify ALL officers of the claim so UI buttons lock out immediately
        await self.broadcast_to_officers({
            "event": "CALL_CLAIMED",
            "call_id": call_id,
            "officer_id": officer_id,
            "status": "ENGAGED"
        })

        # Notify the caller that an officer has accepted the call
        caller_ws = self.caller_sockets.get(call_id)
        if caller_ws:
            try:
                await caller_ws.send_json({
                    "event": "OFFICER_ASSIGNED",
                    "call_id": call_id,
                    "officer_id": officer_id
                })
            except Exception:
                pass

        return True, "Call claimed successfully."

    async def end_call(self, call_id: str, officer_id: Optional[str] = None):
        async with self.lock:
            call = self.calls.get(call_id)
            if call:
                call.status = "ENDED"
                call.ended_at = time.time()

        # Notify officers
        await self.broadcast_to_officers({
            "event": "CALL_ENDED",
            "call_id": call_id
        })

        # Notify caller
        caller_ws = self.caller_sockets.get(call_id)
        if caller_ws:
            try:
                await caller_ws.send_json({
                    "event": "CALL_ENDED",
                    "call_id": call_id
                })
            except Exception:
                pass

    async def broadcast_to_officers(self, message: Dict[str, Any]):
        dead_officers = []
        for off_id, ws in list(self.officer_sockets.items()):
            try:
                await ws.send_json(message)
            except Exception:
                dead_officers.append(off_id)

        for off_id in dead_officers:
            self.officer_sockets.pop(off_id, None)

    async def relay_webrtc(self, recipient_type: str, target_id: str, payload: Dict[str, Any]):
        """
        Relays WebRTC SDP Offer, Answer, and ICE candidates.
        recipient_type: 'caller' or 'officer'
        """
        if recipient_type == "caller":
            ws = self.caller_sockets.get(target_id)
        else:
            ws = self.officer_sockets.get(target_id)

        if ws:
            try:
                await ws.send_json({
                    "event": "WEBRTC_SIGNAL",
                    "payload": payload
                })
            except Exception as e:
                print(f"Error relaying WebRTC signal to {recipient_type} {target_id}: {e}")

# Global singleton instance
call_manager = CallManager()
