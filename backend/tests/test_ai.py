"""Tests for AI Speech Emotion Recognition and Fused Cyber Emergency Triage."""

import pytest
import wave
import io
import struct
import math
from backend.app.ai import (
    classify_incident_semantics,
    triage_call_audio,
    RESPONSIBLE_AI_ADVISORY,
)
from backend.app.state import CallManager


def _generate_dummy_wav(duration_sec: float = 0.5) -> bytes:
    """Generate in-memory 16kHz mono PCM wave audio bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        num_samples = int(16000 * duration_sec)
        for i in range(num_samples):
            val = int(math.sin(2 * math.pi * 400 * i / 16000) * 8000)
            wav_file.writeframes(struct.pack("<h", val))
    return buf.getvalue()


def test_classify_incident_accident():
    sev, cat, summary = classify_incident_semantics("There was a severe car accident and someone is bleeding!")
    assert sev == 1.0
    assert cat == "ACCIDENT / LIFE THREAT"
    assert "bleeding" in summary or "accident" in summary


def test_classify_incident_suicide():
    sev, cat, summary = classify_incident_semantics("I can't take this harassment anymore, I want to end my life")
    assert sev == 1.0
    assert cat == "SUICIDE DISTRESS"


def test_classify_incident_ransomware():
    sev, cat, summary = classify_incident_semantics("All our corporate servers are locked with ransomware demanding bitcoin")
    assert sev == 0.95
    assert cat == "ACTIVE EXTORTION / RANSOMWARE"


def test_classify_incident_wire_fraud():
    sev, cat, summary = classify_incident_semantics("My bank account was drained and unauthorized transaction happened right now!")
    assert sev == 0.85
    assert cat == "ACTIVE WIRE FRAUD"


def test_classify_incident_general():
    sev, cat, summary = classify_incident_semantics("I received a weird spam email asking for my email password.")
    assert sev <= 0.50
    assert cat == "CYBER REPORT"


def test_triage_call_audio_structure():
    audio_bytes = _generate_dummy_wav(0.5)
    res = triage_call_audio(audio_bytes, mime_type="audio/wav")

    assert "triage_priority_score" in res
    assert 1 <= res["triage_priority_score"] <= 100
    assert "urgency_level" in res
    assert "urgency_badge" in res
    assert "scam_indicators" in res
    assert isinstance(res["scam_indicators"], list)
    assert "officer_sop" in res
    assert "emotions" in res
    assert "fear" in res["emotions"]
    assert "distress" in res["emotions"]
    assert "anger" in res["emotions"]
    assert "nervousness" in res["emotions"]
    assert "neutral" in res["emotions"]
    assert res["responsible_ai_advisory"] == RESPONSIBLE_AI_ADVISORY


@pytest.mark.asyncio
async def test_dynamic_priority_queue_sorting():
    cm = CallManager()
    c1 = await cm.create_call("2007")
    c2 = await cm.create_call("2007")
    c3 = await cm.create_call("2007")

    # Set statuses to WAITING
    await cm.update_call_status(c1.call_id, "WAITING")
    await cm.update_call_status(c2.call_id, "WAITING")
    await cm.update_call_status(c3.call_id, "WAITING")

    # Call 1: Routine (score 25)
    await cm.update_call_triage(c1.call_id, {
        "triage_priority_score": 25,
        "urgency_level": "ROUTINE",
        "incident_category": "GENERAL REPORT",
        "incident_summary": "Routine inquiry",
    })

    # Call 3: Critical Accident (score 98) - arrived LATER than c1 and c2!
    await cm.update_call_triage(c3.call_id, {
        "triage_priority_score": 98,
        "urgency_level": "VERY_VERY_URGENT",
        "incident_category": "ACCIDENT / LIFE THREAT",
        "incident_summary": "High speed crash distress",
    })

    # Call 2: Urgent Ransomware (score 75)
    await cm.update_call_triage(c2.call_id, {
        "triage_priority_score": 75,
        "urgency_level": "URGENT",
        "incident_category": "ACTIVE EXTORTION / RANSOMWARE",
        "incident_summary": "Hospital locked",
    })

    # Queue snapshot should automatically order: c3 (98) -> c2 (75) -> c1 (25)
    snapshot = cm._snapshot_locked()
    call_ids_in_order = [c["call_id"] for c in snapshot]

    assert call_ids_in_order == [c3.call_id, c2.call_id, c1.call_id]


def test_analyze_transcript_groq_empty():
    from backend.app.ai import analyze_transcript_groq
    assert analyze_transcript_groq("") is None
    assert analyze_transcript_groq("   ") is None
    assert analyze_transcript_groq("hi") is None
