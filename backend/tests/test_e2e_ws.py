import asyncio
import inspect
import json
import os
from urllib.parse import quote

import pytest
import requests
import websockets
from dotenv import load_dotenv

load_dotenv()

# End-to-end: needs a real running gateway. Deselected by default (see
# pyproject addopts) so the normal suite stays offline and fast.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.getenv("CERS_E2E_BASE_URL"),
        reason="set CERS_E2E_BASE_URL to a running gateway to run the e2e flow",
    ),
]

BASE_HTTP = os.getenv("CERS_E2E_BASE_URL", "http://localhost:8000")
BASE_WS = BASE_HTTP.replace("http://", "ws://").replace("https://", "wss://")
SHARED_TOKEN = os.getenv("CERS_OFFICER_TOKEN", "")

# Per-officer mode: CERS_E2E_OFFICERS="OFFICER_01:secret1,OFFICER_02:secret2"
# mirrors the gateway's CERS_OFFICERS, so the e2e flow can verify that each
# officer must present their own secret. Falls back to the shared
# CERS_OFFICER_TOKEN mode (any name + one token) when unset.
def _parse_e2e_officers(raw):
    officers = {}
    for entry in raw.split(","):
        name, sep, secret = entry.strip().partition(":")
        # A gateway entry may list several secrets ("id:passA|passB"); the e2e
        # run signs in with the first one.
        first_secret = secret.split("|")[0].strip() if sep else ""
        if sep and name.strip() and first_secret:
            officers[name.strip()] = first_secret
    return officers


E2E_OFFICERS = _parse_e2e_officers(os.getenv("CERS_E2E_OFFICERS", ""))

# websockets renamed extra_headers to additional_headers in newer releases.
_HEADER_KW = (
    "additional_headers"
    if "additional_headers" in inspect.signature(websockets.connect).parameters
    else "extra_headers"
)


def officer_credentials():
    """The two (officer_id, secret) pairs this run should sign in with."""
    if E2E_OFFICERS:
        ids = list(E2E_OFFICERS)[:2]
        if len(ids) < 2:
            raise SystemExit(
                "CERS_E2E_OFFICERS needs at least two 'officer_id:secret' entries."
            )
        return [(officer_id, E2E_OFFICERS[officer_id]) for officer_id in ids]
    if SHARED_TOKEN:
        return [("OFFICER_01", SHARED_TOKEN), ("OFFICER_02", SHARED_TOKEN)]
    raise SystemExit(
        "Set CERS_E2E_OFFICERS='officer_id:secret,...' (per-officer mode, matching "
        "the gateway's CERS_OFFICERS) or CERS_OFFICER_TOKEN (shared-token mode)."
    )


def officer_session(officer_id, secret):
    session = requests.Session()
    response = session.post(
        f"{BASE_HTTP}/api/auth/login",
        json={"token": secret, "officer_id": officer_id},
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
    (off1_id, off1_secret), (off2_id, off2_secret) = officer_credentials()

    print("--- 1. Authenticating two officers ---")
    off1_session = officer_session(off1_id, off1_secret)
    off2_session = officer_session(off2_id, off2_secret)

    # Officer IDs may contain spaces ("Mayuresh Alpha") — percent-encode.
    async with connect(f"{BASE_WS}/ws/officer/{quote(off1_id, safe='')}", off1_session) as ws_off1, \
               connect(f"{BASE_WS}/ws/officer/{quote(off2_id, safe='')}", off2_session) as ws_off2:
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
        call_token = created.json()["call_token"]

        async with websockets.connect(
            f"{BASE_WS}/ws/caller/{call_id}?token={call_token}"
        ) as ws_caller:
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
