"""Boundary checks for the strict 16-bit mono PCM WAV contract."""

from __future__ import annotations

import io
import math
import struct
import wave

from fastapi.testclient import TestClient

from backend.app.config import OFFICER_TOKEN
from backend.app.main import app
from backend.app.triage import looks_like_wav


def _wav_bytes(channels: int = 1, sampwidth: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sampwidth)
        wav_file.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(math.sin(i * 0.2) * 4000)) for i in range(800)
        )
        if channels == 2:
            # Duplicate each sample into a second channel.
            mono_values = [
                int(math.sin(i * 0.2) * 4000) for i in range(800)
            ]
            frames = b"".join(
                struct.pack("<hh", value, value) for value in mono_values
            )
        wav_file.writeframes(frames)
    return buf.getvalue()


def test_mono_16bit_pcm_wav_is_accepted():
    assert looks_like_wav(_wav_bytes(channels=1, sampwidth=2))


def test_stereo_wav_is_rejected():
    assert not looks_like_wav(_wav_bytes(channels=2, sampwidth=2))


def test_8bit_or_24bit_pcm_is_rejected():
    assert not looks_like_wav(_wav_bytes(channels=1, sampwidth=1))


def test_riff_without_fmt_or_data_is_rejected():
    body = b"RIFF\x00\x00\x00\x00WAVE"
    assert not looks_like_wav(body)


def test_plain_webm_is_rejected():
    assert not looks_like_wav(b"\x1a\x45\xdf\xa3" + b"0" * 200)


def test_truncated_header_is_rejected():
    assert not looks_like_wav(b"RIFF")


def test_triage_upload_rejects_stereo_wav():
    with TestClient(app) as client:
        created = client.post("/api/calls", json={"dialed_number": "2007"})
        assert created.status_code == 200
        call_id = created.json()["call_id"]
        call_token = created.json()["call_token"]
        response = client.post(
            f"/api/calls/{call_id}/triage",
            files={"file": ("stereo.wav", _wav_bytes(channels=2, sampwidth=2), "audio/wav")},
            headers={"X-Call-Token": call_token},
        )
        assert response.status_code == 415


def test_triage_upload_accepts_mono_wav(monkeypatch):
    class FakeResult:
        data = {
            "triage_priority_score": 50,
            "urgency_level": "ELEVATED",
            "urgency_badge": "🟡 ELEVATED",
            "incident_category": "CYBER REPORT",
            "incident_summary": "Test statement.",
            "transcript": "They threatened me and drained my account.",
            "scam_indicators": ["threat"],
            "officer_sop": ["Call bank"],
            "emotions": {
                "fear": 0.5,
                "distress": 0.5,
                "anger": 0.1,
                "nervousness": 0.4,
                "neutral": 0.2,
                "dominant": "distress",
                "vocal_distress_score": 0.55,
                "source": "acoustic",
            },
            "emotions_source": "acoustic",
            "ai_status": {"asr": "offline", "ser": "none", "llm": "offline", "degraded": True, "offline": True},
            "acoustic": {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0},
            "responsible_ai_advisory": "Responsible AI Advisory",
        }

    monkeypatch.setattr(
        "backend.app.triage.triage_call_audio", lambda *a, **k: FakeResult.data
    )

    with TestClient(app) as client:
        created = client.post("/api/calls", json={"dialed_number": "2007"})
        call_id = created.json()["call_id"]
        call_token = created.json()["call_token"]
        response = client.post(
            f"/api/calls/{call_id}/triage",
            files={"file": ("mono.wav", _wav_bytes(channels=1, sampwidth=2), "audio/wav")},
            headers={"X-Call-Token": call_token},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "triaging"
        client.post("/api/auth/login", json={"officer_id": "OFFICER_01", "token": OFFICER_TOKEN})
