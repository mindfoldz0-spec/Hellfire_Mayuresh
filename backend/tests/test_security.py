import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.main import OFFICER_TOKEN, app


def login(client, officer_id="OFFICER_01", token=None):
    return client.post(
        "/api/auth/login",
        json={"token": token or OFFICER_TOKEN, "officer_id": officer_id},
    )


def test_officer_api_requires_authentication():
    with TestClient(app) as client:
        assert client.get("/api/recordings").status_code == 401
        assert client.get("/api/auth/me").status_code == 401
        created = client.post("/api/calls", json={"dialed_number": "2007"})
        assert created.status_code == 200
        call_id = created.json()["call_id"]
        upload = client.post(
            f"/api/recordings/{call_id}",
            files={"audio_file": ("x.webm", b"not-audio", "audio/webm")},
        )
        assert upload.status_code == 401


def test_login_rejects_bad_token_and_accepts_good_token():
    with TestClient(app) as client:
        assert login(client, token="wrong-password").status_code == 401
        # Password mayuresh2007
        pass_res = login(client, token="mayuresh2007", officer_id="OFFICER_01")
        assert pass_res.status_code == 200
        assert pass_res.json()["officer_id"] == "OFFICER_01"
        # Configured token / alternate
        response = login(client, officer_id="OFFICER_02")
        assert response.status_code == 200
        assert response.json()["officer_id"] == "OFFICER_02"
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["officer_id"] == "OFFICER_02"


def test_logout_clears_session():
    with TestClient(app) as client:
        assert login(client).status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_officer_websocket_requires_matching_session():
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/officer/OFFICER_01") as websocket:
                websocket.receive_json()

        assert login(client, officer_id="OFFICER_01").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/officer/OFFICER_02") as websocket:
                websocket.receive_json()


def test_authenticated_officer_receives_queue_snapshot_and_call_events():
    with TestClient(app) as client:
        assert login(client).status_code == 200
        with client.websocket_connect("/ws/officer/OFFICER_01") as websocket:
            assert websocket.receive_json()["event"] == "QUEUE_SNAPSHOT"

            created = client.post("/api/calls", json={"dialed_number": "2007"})
            assert created.status_code == 200
            call_id = created.json()["call_id"]

            new_call = websocket.receive_json()
            assert new_call["event"] == "NEW_CALL"
            assert new_call["call"]["call_id"] == call_id
            assert new_call["call"]["dialed_number"] == "2007"

            websocket.send_json({"action": "CLAIM_CALL", "call_id": call_id})
            events = [websocket.receive_json(), websocket.receive_json()]
            assert [event["event"] for event in events] == ["CALL_CLAIMED", "CLAIM_RESULT"]
            assert events[1]["success"] is True

            websocket.send_json({"action": "SYNC_QUEUE"})
            snapshot = websocket.receive_json()
            assert snapshot["event"] == "QUEUE_SNAPSHOT"
            assert any(call["call_id"] == call_id for call in snapshot["calls"])


def test_caller_websocket_rejects_unknown_session():
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/caller/not-a-uuid") as websocket:
                websocket.receive_json()

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/caller/00000000-0000-4000-8000-000000000000"
            ) as websocket:
                websocket.receive_json()


def test_static_routes_do_not_allow_path_traversal():
    with TestClient(app) as client:
        for path in (
            "/caller/..%5C..%5C.env",
            "/caller/..%5C..%5CREADME.md",
            "/officer/..%5C..%5Cbackend%5Capp%5Cmain.py",
        ):
            response = client.get(path)
            assert response.status_code in (400, 404), (path, response.status_code)


def test_security_headers_are_present():
    with TestClient(app) as client:
        response = client.get("/officer")
        assert response.status_code == 200
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


def test_recording_listing_and_notes_require_auth_and_validate_names():
    with TestClient(app) as client:
        assert client.get("/api/recordings").status_code == 401
        assert client.put("/api/recordings/recording_test.json", json={"notes": "x"}).status_code == 401

        assert login(client).status_code == 200
        # Traversal / non-recording names must not escape the recordings folder.
        assert client.get("/api/recordings/..%5C..%5C.env").status_code in (400, 404)
        assert client.get("/api/recordings/not-a-recording.webm").status_code == 404
