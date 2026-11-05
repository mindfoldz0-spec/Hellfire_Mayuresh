"""Escalation, live window triage, metrics, and provider-resilience tests."""

import io
import math
import struct
import wave
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.app import providers, triage
from backend.app.main import OFFICER_TOKEN, app
from backend.app.state import CallManager, call_manager


def _wav_bytes(duration_sec: float = 0.2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        for i in range(int(16000 * duration_sec)):
            handle.writeframes(
                struct.pack("<h", int(math.sin(2 * math.pi * 400 * i / 16000) * 5000))
            )
    return buf.getvalue()


def login(client, officer_id="OFFICER_01"):
    return client.post(
        "/api/auth/login", json={"token": OFFICER_TOKEN, "officer_id": officer_id}
    )


@contextmanager
def claimed_call(client, officer_id="OFFICER_01"):
    call_id = client.post("/api/calls", json={"dialed_number": "2007"}).json()["call_id"]
    with client.websocket_connect(f"/ws/officer/{officer_id}") as ws:
        assert ws.receive_json()["event"] == "QUEUE_SNAPSHOT"
        ws.send_json({"action": "CLAIM_CALL", "call_id": call_id})
        events = {ws.receive_json()["event"], ws.receive_json()["event"]}
        assert "CLAIM_RESULT" in events
        yield call_id


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_escalation_flags_only_old_high_priority_calls():
    manager = CallManager()
    hot = await manager.create_call("2007")
    cool = await manager.create_call("2007")
    await manager.update_call_triage(hot.call_id, {"triage_priority_score": 95})
    await manager.update_call_triage(cool.call_id, {"triage_priority_score": 20})

    # Nothing is old enough yet.
    assert await manager.escalate_stale_calls(priority_min=85, older_than=30) == []

    manager.calls[hot.call_id].created_at -= 60
    manager.calls[cool.call_id].created_at -= 60
    escalated = await manager.escalate_stale_calls(priority_min=85, older_than=30)
    assert [call["call_id"] for call in escalated] == [hot.call_id]

    # Sticky: a second sweep must not re-alarm the same call.
    assert await manager.escalate_stale_calls(priority_min=85, older_than=30) == []


@pytest.mark.asyncio
async def test_escalation_skips_engaged_calls():
    manager = CallManager()
    call = await manager.create_call("2007")
    await manager.update_call_triage(call.call_id, {"triage_priority_score": 99})
    await manager.claim_call(call.call_id, "OFFICER_01")
    manager.calls[call.call_id].created_at -= 120
    assert await manager.escalate_stale_calls(priority_min=85, older_than=30) == []


# ---------------------------------------------------------------------------
# Live window triage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_partial_triage_never_changes_status():
    manager = CallManager()
    call = await manager.create_call("2007")
    await manager.claim_call(call.call_id, "OFFICER_01")

    await manager.update_call_triage(
        call.call_id, {"triage_priority_score": 77}, partial=True
    )
    current = manager.calls[call.call_id]
    assert current.status == "ENGAGED"
    assert current.officer_id == "OFFICER_01"
    assert current.triage_priority_score == 77


def test_window_endpoint_requires_the_assigned_officer(monkeypatch):
    def fake_triage(*args, **kwargs):
        return {
            "triage_priority_score": 60,
            "urgency_level": "ELEVATED",
            "urgency_badge": "ELEVATED",
            "incident_category": "CYBER REPORT",
            "incident_summary": "live",
            "transcript": "more words",
            "scam_indicators": [],
            "officer_sop": [],
            "emotions": {},
            "ai_status": {"asr": "ok", "ser": "aws_ser", "llm": "ok", "degraded": False},
            "responsible_ai_advisory": "advisory",
        }

    monkeypatch.setattr(triage, "triage_call_audio", fake_triage)

    with TestClient(app) as client:
        login(client, "OFFICER_01")
        with claimed_call(client) as call_id:
            payload = {"file": ("w.wav", _wav_bytes(), "audio/wav")}

            # A different officer must not be able to re-triage this call.
            login(client, "OFFICER_02")
            wrong = client.post(
                f"/api/calls/{call_id}/triage/window", files=payload, data={"window": "1"}
            )
            assert wrong.status_code == 403

            login(client, "OFFICER_01")
            ok = client.post(
                f"/api/calls/{call_id}/triage/window", files=payload, data={"window": "1"}
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["window"] == 1


def test_window_endpoint_requires_authentication():
    with TestClient(app) as client:
        call_id = client.post("/api/calls", json={"dialed_number": "2007"}).json()[
            "call_id"
        ]
        response = client.post(
            f"/api/calls/{call_id}/triage/window",
            files={"file": ("w.wav", _wav_bytes(), "audio/wav")},
            data={"window": "1"},
        )
    assert response.status_code == 401


def test_window_endpoint_rejects_non_wav(monkeypatch):
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            response = client.post(
                f"/api/calls/{call_id}/triage/window",
                files={"file": ("w.wav", b"nope" * 40, "audio/wav")},
                data={"window": "1"},
            )
    assert response.status_code == 415


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_metrics_require_auth():
    with TestClient(app) as client:
        assert client.get("/api/metrics").status_code == 401


def test_metrics_report_claims_and_providers():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            call = call_manager.get_call(call_id)
            call.triage_priority_score = 96
        data = client.get("/api/metrics").json()

    assert data["counters"].get("calls_created", 0) >= 1
    assert data["counters"].get("calls_claimed", 0) >= 1
    assert "providers" in data
    assert "time_to_claim" in data
    assert set(data["time_to_claim"]) == {"CRITICAL", "HIGH", "OTHER"}


def test_health_reports_dependency_state():
    with TestClient(app) as client:
        payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert "providers" in payload
    assert "degraded" in payload
    assert "active_calls" in payload
    # conftest sets no CERS_OFFICERS, so the test app runs in shared-token mode.
    assert payload["auth_mode"] == "shared"
    assert payload["worker_mode"] in {"single", "multi-unsafe"}


def test_health_worker_mode_reflects_multi_worker_override(monkeypatch):
    from backend.app import pages

    monkeypatch.setattr(pages, "ALLOW_MULTI_WORKER", False)
    with TestClient(app) as client:
        assert client.get("/health").json()["worker_mode"] == "single"
    monkeypatch.setattr(pages, "ALLOW_MULTI_WORKER", True)
    with TestClient(app) as client:
        assert client.get("/health").json()["worker_mode"] == "multi-unsafe"


# ---------------------------------------------------------------------------
# Provider resilience
# ---------------------------------------------------------------------------
def test_request_returns_none_when_offline(monkeypatch):
    monkeypatch.setattr(providers, "AI_OFFLINE", True)
    assert providers.request("groq", "POST", "https://example.invalid", timeout=1) is None


def test_breaker_opens_after_repeated_failures(monkeypatch):
    providers.registry.reset()
    monkeypatch.setattr(providers, "PROVIDER_ATTEMPTS", 1)
    monkeypatch.setattr(providers, "PROVIDER_BREAKER_THRESHOLD", 2)
    monkeypatch.setattr(providers, "PROVIDER_BREAKER_COOLDOWN", 60.0)

    def boom(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(providers.requests, "request", boom)

    for _ in range(2):
        assert providers.request("groq", "POST", "https://x.invalid", timeout=1) is None
    assert providers.registry.is_open("groq") is True

    snapshot = providers.registry.snapshot()["groq"]
    assert snapshot["failures"] == 2
    assert snapshot["breaker_open"] is True


def test_recovered_provider_closes_the_breaker(monkeypatch):
    providers.registry.reset()
    monkeypatch.setattr(providers, "PROVIDER_ATTEMPTS", 1)
    monkeypatch.setattr(providers, "PROVIDER_BREAKER_THRESHOLD", 2)

    def boom(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(providers.requests, "request", boom)
    for _ in range(2):
        providers.request("groq", "POST", "https://x.invalid", timeout=1)
    assert providers.registry.is_open("groq")

    # Cooldown elapses and a healthy call clears the streak.
    providers.registry._states["groq"].open_until = 0

    class _Response:
        status_code = 200

    monkeypatch.setattr(
        providers.requests, "request", lambda *a, **k: _Response()
    )
    assert providers.request("groq", "POST", "https://x.invalid", timeout=1) is not None
    assert providers.registry.is_open("groq") is False
