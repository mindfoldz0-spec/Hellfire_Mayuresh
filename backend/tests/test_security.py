import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.config import OFFICER_PASSWORD
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
        # The configured officer password is also accepted.
        pass_res = login(client, token=OFFICER_PASSWORD, officer_id="OFFICER_01")
        assert pass_res.status_code == 200
        assert pass_res.json()["officer_id"] == "OFFICER_01"
        # Configured token / alternate
        response = login(client, officer_id="OFFICER_02")
        assert response.status_code == 200
        assert response.json()["officer_id"] == "OFFICER_02"
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["officer_id"] == "OFFICER_02"


def test_non_ascii_password_is_rejected_not_crashed():
    """Regression: compare_digest used to raise TypeError -> HTTP 500."""
    with TestClient(app) as client:
        assert login(client, token="pässwörd").status_code == 401


def test_credentials_must_be_configured(monkeypatch):
    """No built-in default — but any configured password is the operator's call."""
    from backend.app import config

    monkeypatch.setattr(config, "OFFICER_PASSWORD", "")
    monkeypatch.setattr(config, "OFFICER_TOKEN", "")
    with pytest.raises(RuntimeError):
        config.validate_officer_credentials()

    # A simple password is a deployment decision, not a startup error.
    monkeypatch.setattr(config, "OFFICER_PASSWORD", "a-simple-unpublished-password")
    config.validate_officer_credentials()

    # But a credential that matches a published sample must not start.
    monkeypatch.setattr(config, "OFFICER_PASSWORD", "mayuresh2007")
    with pytest.raises(RuntimeError):
        config.validate_officer_credentials()


def test_login_uses_the_officer_name_as_identity():
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"token": OFFICER_TOKEN, "officer_id": "Mayur Patil"},
        )
        assert response.status_code == 200
        assert response.json()["officer_id"] == "Mayur Patil"
        assert client.get("/api/auth/me").json()["officer_id"] == "Mayur Patil"

        with client.websocket_connect("/ws/officer/Mayur%20Patil") as websocket:
            assert websocket.receive_json()["event"] == "QUEUE_SNAPSHOT"


def test_login_rejects_unsafe_officer_names():
    with TestClient(app) as client:
        for name in ("../evil", "a/b", "", "x" * 65):
            response = client.post(
                "/api/auth/login", json={"token": OFFICER_TOKEN, "officer_id": name}
            )
            # 400 = failed our own allowlist, 422 = failed request validation.
            assert response.status_code in (400, 422), (name, response.status_code)


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

        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
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
        response = client.get("/officer/login", follow_redirects=False)
        assert response.status_code == 200
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


def test_officer_console_requires_session():
    with TestClient(app) as client:
        response = client.get("/officer", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/officer/login"
        response = client.get("/officer/login", follow_redirects=False)
        assert response.status_code == 200

        assert login(client).status_code == 200
        assert client.get("/officer", follow_redirects=False).status_code == 200
        # An authenticated session is no longer shown the login page.
        logged_in = client.get("/officer/login", follow_redirects=False)
        assert logged_in.status_code == 303
        assert logged_in.headers["location"] == "/officer"


def test_recording_listing_and_notes_require_auth_and_validate_names():
    with TestClient(app) as client:
        assert client.get("/api/recordings").status_code == 401
        assert client.put("/api/recordings/recording_test.json", json={"notes": "x"}).status_code == 401

        assert login(client).status_code == 200
        # Traversal / non-recording names must not escape the recordings folder.
        assert client.get("/api/recordings/..%5C..%5C.env").status_code in (400, 404)
        assert client.get("/api/recordings/not-a-recording.webm").status_code == 404


def test_parse_officers_supports_multiple_secrets_per_officer():
    from backend.app.config import _parse_officers

    parsed = _parse_officers(
        "Mayuresh Alpha:A|B, Tejas Delta:A ,bad entry,NoColon,Siddhant Beta:C|"
    )
    assert parsed == {
        "Mayuresh Alpha": ("A", "B"),
        "Tejas Delta": ("A",),
        "Siddhant Beta": ("C",),
    }


def test_per_officer_login_accepts_each_secret_and_lists_names(monkeypatch):
    from backend.app import api

    officers = {
        "Mayuresh Alpha": ("A", "B"),
        "Tejas Delta": ("A", "B"),
        "Siddhant Beta": ("A", "B"),
    }
    monkeypatch.setattr(api, "OFFICERS", officers)
    with TestClient(app) as client:
        # The picker exposes names only, never secrets.
        listed = client.get("/api/auth/officers").json()["officers"]
        assert listed == ["Mayuresh Alpha", "Siddhant Beta", "Tejas Delta"]

        for name in officers:
            for secret in ("A", "B"):
                response = client.post(
                    "/api/auth/login", json={"token": secret, "officer_id": name}
                )
                assert response.status_code == 200, (name, secret)
                assert response.json()["officer_id"] == name
                client.post("/api/auth/logout")

        # Unknown officer, or a secret that belongs to no one, is rejected.
        assert client.post(
            "/api/auth/login", json={"token": "A", "officer_id": "Unknown"}
        ).status_code == 401
        assert client.post(
            "/api/auth/login", json={"token": "C", "officer_id": "Mayuresh Alpha"}
        ).status_code == 401


def _request_with_headers(headers):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("10.0.0.1", 1234),
    }
    return Request(scope)


def test_client_ip_prefers_valid_forwarded_for():
    from backend.app.security import client_ip

    request = _request_with_headers({"X-Forwarded-For": "203.0.113.9, 10.1.2.3"})
    assert client_ip(request) == "203.0.113.9"


def test_client_ip_rejects_garbage_forwarded_for():
    from backend.app.security import client_ip

    request = _request_with_headers({"X-Forwarded-For": "not-an-ip, also bad"})
    assert client_ip(request) == "10.0.0.1"


def test_client_ip_without_forwarded_for_uses_peer():
    from backend.app.security import client_ip

    assert client_ip(_request_with_headers({})) == "10.0.0.1"


def test_officer_picker_is_empty_in_shared_mode():
    # conftest sets no CERS_OFFICERS, so the default app runs shared mode.
    with TestClient(app) as client:
        assert client.get("/api/auth/officers").json() == {"officers": []}
