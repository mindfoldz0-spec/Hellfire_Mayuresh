import asyncio
import inspect
import json
import os

import requests
import websockets
from dotenv import load_dotenv

load_dotenv()

BASE_HTTP = os.getenv("CERS_E2E_BASE_URL", "http://localhost:8000")
BASE_WS = BASE_HTTP.replace("http://", "ws://").replace("https://", "wss://")
OFFICER_TOKEN = os.getenv("CERS_OFFICER_TOKEN", "")

# websockets renamed extra_headers to additional_headers in newer releases.
_HEADER_KW = (
    "additional_headers"
    if "additional_headers" in inspect.signature(websockets.connect).parameters
    else "extra_headers"
)


def officer_session(officer_id):
    session = requests.Session()
    response = session.post(
        f"{BASE_HTTP}/api/auth/login",
        json={"token": OFFICER_TOKEN, "officer_id": officer_id},
        timeout=10,
    )
    response.raise_for_status()
    return session


def cookie_header(session):
    return "; ".join(f"{key}={value}" for key, value in session.cookies.items())


def connect(url, session):
    return websockets.connect(url, **{_HEADER_KW: {"Cookie": cookie_header(session)}})


async def recv_event(websocket, timeout=5):
    return json.loads(await asyncio.wait_for(websocket.recv(), timeout))


async def test_end_to_end_flow():
    if not OFFICER_TOKEN:
        raise SystemExit(
            "Set CERS_OFFICER_TOKEN to the same value used by the server before running this test."
        )

    print("--- 1. Authenticating two officers ---")
    off1_session = officer_session("OFFICER_01")
    off2_session = officer_session("OFFICER_02")

    async with connect(f"{BASE_WS}/ws/officer/OFFICER_01", off1_session) as ws_off1, \
               connect(f"{BASE_WS}/ws/officer/OFFICER_02", off2_session) as ws_off2:
        assert (await recv_event(ws_off1))["event"] == "QUEUE_SNAPSHOT"
        assert (await recv_event(ws_off2))["event"] == "QUEUE_SNAPSHOT"

        print("--- 2. Creating a server-side call session ---")
        created = requests.post(
            f"{BASE_HTTP}/api/calls",
            json={"dialed_number": "2007"},
            timeout=10,
        )
        created.raise_for_status()
        call_id = created.json()["call_id"]

        async with websockets.connect(f"{BASE_WS}/ws/caller/{call_id}") as ws_caller:
            new1 = await recv_event(ws_off1)
            new2 = await recv_event(ws_off2)
            assert new1["event"] == "NEW_CALL" and new1["call"]["call_id"] == call_id
            assert new2["event"] == "NEW_CALL" and new2["call"]["call_id"] == call_id

            print("--- 3. IVR lifecycle ---")
            await ws_caller.send(json.dumps({"action": "IVR_STARTED"}))
            assert (await recv_event(ws_off1))["status"] == "IVR"
            assert (await recv_event(ws_off2))["status"] == "IVR"
            await ws_caller.send(json.dumps({"action": "IVR_FINISHED"}))
            assert (await recv_event(ws_off1))["status"] == "WAITING"
            assert (await recv_event(ws_off2))["status"] == "WAITING"

            print("--- 4. Atomic claim ---")
            await ws_off1.send(json.dumps({"action": "CLAIM_CALL", "call_id": call_id}))
            assert (await recv_event(ws_off1))["event"] == "CALL_CLAIMED"
            assert (await recv_event(ws_off1))["event"] == "CLAIM_RESULT"
            assert (await recv_event(ws_off2))["event"] == "CALL_CLAIMED"
            assigned = await recv_event(ws_caller)
            assert assigned["event"] == "OFFICER_ASSIGNED"

            print("--- 5. Second officer is locked out ---")
            await ws_off2.send(json.dumps({"action": "CLAIM_CALL", "call_id": call_id}))
            lockout = await recv_event(ws_off2)
            assert lockout["event"] == "CLAIM_RESULT"
            assert lockout["success"] is False

            print("--- 6. WebRTC signaling relay ---")
            await ws_off1.send(json.dumps({
                "action": "WEBRTC_SIGNAL",
                "call_id": call_id,
                "payload": {"offer": {"type": "offer", "sdp": "v=0 mock offer"}},
            }))
            assert (await recv_event(ws_caller))["event"] == "WEBRTC_SIGNAL"

            await ws_caller.send(json.dumps({
                "action": "WEBRTC_SIGNAL",
                "payload": {"answer": {"type": "answer", "sdp": "v=0 mock answer"}},
            }))
            assert (await recv_event(ws_off1))["event"] == "WEBRTC_SIGNAL"

            print("--- 7. End call and verify one event per party ---")
            await ws_off1.send(json.dumps({"action": "END_CALL", "call_id": call_id}))
            assert (await recv_event(ws_off1))["event"] == "CALL_ENDED"
            assert (await recv_event(ws_off2))["event"] == "CALL_ENDED"
            assert (await recv_event(ws_caller))["event"] == "CALL_ENDED"

    print("\n==========================================")
    print("ALL END-TO-END FLOW TESTS PASSED")
    print("==========================================")


if __name__ == "__main__":
    asyncio.run(test_end_to_end_flow())
