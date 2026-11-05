"""Caller capability, triage ingress and IVR lifecycle tests."""

import io
import math
import struct
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from backend.app import triage
from backend.app.main import app
from backend.app.state import call_manager


def _wav_bytes(duration_sec: float = 0.2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        count = int(16000 * duration_sec)
        for i in range(count):
            handle.writeframes(
                struct.pack("<h", int(math.sin(2 * math.pi * 400 * i / 16000) * 5000))
            )
    return buf.getvalue()


def _new_call(client):
    response = client.post("/api/calls", json={"dialed_number": "2007"})
    assert response.status_code == 200
    return response.json()


def test_create_call_returns_capability_token():
    with TestClient(app) as client:
        payload = _new_call(client)
        assert payload["call_token"]
        assert len(payload["call_token"]) == 64  # sha256 hex digest


def test_triage_requires_matching_call_token():
    with TestClient(app) as client:
        payload = _new_call(client)
        call_id = payload["call_id"]

        # No token at all.
        assert (
            client.post(
                f"/api/calls/{call_id}/triage",
                files={"file": ("s.wav", _wav_bytes(), "audio/wav")},
            ).status_code
            == 403
        )
        # Wrong token.
        assert (
            client.post(
                f"/api/calls/{call_id}/triage",
                headers={"X-Call-Token": "0" * 64},
                files={"file": ("s.wav", _wav_bytes(), "audio/wav")},
            ).status_code
            == 403
        )
        # A token minted for a different call must not work.
        other = _new_call(client)
        assert (
            client.post(
                f"/api/calls/{call_id}/triage",
                headers={"X-Call-Token": other["call_token"]},
                files={"file": ("s.wav", _wav_bytes(), "audio/wav")},
            ).status_code
            == 403
        )


def test_triage_rejects_non_wav_payload():
    with TestClient(app) as client:
        payload = _new_call(client)
        response = client.post(
            f"/api/calls/{payload['call_id']}/triage",
            headers={"X-Call-Token": payload["call_token"]},
            files={"file": ("s.wav", b"not-a-wave-file" * 10, "audio/wav")},
        )
        assert response.status_code == 415


def test_triage_deduplicates_in_flight_uploads(monkeypatch):
    release = threading.Event()

    def slow_triage(*args, **kwargs):
        release.wait(5)
        return {
            "triage_priority_score": 50,
            "urgency_level": "ELEVATED",
            "urgency_badge": "🟡 ELEVATED",
            "incident_category": "CYBER REPORT",
            "incident_summary": "test",
            "transcript": "test",
            "scam_indicators": [],
            "officer_sop": [],
            "emotions": {},
            "responsible_ai_advisory": "advisory",
        }

    monkeypatch.setattr(triage, "triage_call_audio", slow_triage)

    with TestClient(app) as client:
        payload = _new_call(client)
        call_id = payload["call_id"]
        headers = {"X-Call-Token": payload["call_token"]}
        files = {"file": ("s.wav", _wav_bytes(), "audio/wav")}

        assert (
            client.post(f"/api/calls/{call_id}/triage", headers=headers, files=files).status_code
            == 200
        )
        second = client.post(f"/api/calls/{call_id}/triage", headers=headers, files=files)
        assert second.status_code == 409
        release.set()
        time.sleep(0.2)


def test_ivr_finished_makes_call_claimable():
    """Regression: IVR_FINISHED used to leave the session in IVR forever."""
    with TestClient(app) as client:
        payload = _new_call(client)
        call_id = payload["call_id"]
        with client.websocket_connect(
            f"/ws/caller/{call_id}?token={payload['call_token']}"
        ) as ws:
            ws.send_json({"action": "IVR_STARTED"})
            ws.send_json({"action": "PING"})  # ordered barrier
            assert ws.receive_json()["event"] == "PONG"
            assert call_manager.get_call(call_id).status == "IVR"

            ws.send_json({"action": "IVR_FINISHED"})
            ws.send_json({"action": "PING"})
            assert ws.receive_json()["event"] == "PONG"
            assert call_manager.get_call(call_id).status == "WAITING"

            ws.send_json({"action": "END_CALL"})


def test_caller_socket_requires_token():
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        payload = _new_call(client)
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            f"/ws/caller/{payload['call_id']}"
        ):
            pass


def test_text_statement_fallback_accepts_both_shapes(monkeypatch):
    """Microphone-less callers can submit a typed statement either way."""
    import time as _time

    from backend.app import ai as ai_module

    def fake_text(statement: str):
        return {
            "triage_priority_score": 71,
            "urgency_level": "URGENT",
            "urgency_badge": "URGENT",
            "incident_category": "ACTIVE WIRE FRAUD",
            "incident_summary": "typed statement",
            "transcript": statement,
            "scam_indicators": [],
            "officer_sop": [],
            "emotions": {"fear": 0.5, "distress": 0.6, "anger": 0.2,
                         "nervousness": 0.3, "neutral": 0.1, "dominant": "distress",
                         "vocal_distress_score": 0.5, "source": "acoustic"},
            "emotions_source": "acoustic",
            "ai_status": {"asr": "skipped", "ser": "none", "llm": "ok", "degraded": True},
            "responsible_ai_advisory": "advisory",
        }

    monkeypatch.setattr(ai_module, "triage_text_statement", fake_text)

    with TestClient(app) as client:
        payload = _new_call(client)
        call_id = payload["call_id"]
        headers = {"X-Call-Token": payload["call_token"]}

        # Plain form field (what the dialer sends).
        first = client.post(
            f"/api/calls/{call_id}/statement",
            headers=headers,
            data={"file": "A fake CBI officer took 2 lakh rupees from my SBI account"},
        )
        assert first.status_code == 200, first.text

        deadline = _time.time() + 5
        while _time.time() < deadline:
            if call_manager.get_call(call_id).status == "WAITING":
                break
            _time.sleep(0.05)
        call = call_manager.get_call(call_id)
        assert call.status == "WAITING"
        assert call.triage_priority_score == 71

        # Uploaded text part also works.
        second = client.post(
            f"/api/calls/{call_id}/statement",
            headers=headers,
            files={"file": ("statement.txt", b"typed part", "text/plain")},
        )
        assert second.status_code in (200, 409), second.text
