"""Tests for AI Speech Emotion Recognition and Fused Cyber Emergency Triage."""

import io
import math
import struct
import wave

import pytest

from backend.app.ai import (
    RESPONSIBLE_AI_ADVISORY,
    _normalize_forensics,
    classify_incident_semantics,
    synthesize_fused_emotions,
    triage_call_audio,
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


@pytest.fixture
def offline_models(monkeypatch):
    """Never hit Sarvam / AWS / Groq / Hugging Face from unit tests."""
    monkeypatch.setattr(
        "backend.app.ai.query_speech_emotion",
        lambda *a, **k: {"success": False, "map": {}, "label": "neutral"},
    )
    monkeypatch.setattr("backend.app.ai.transcribe_audio_sarvam", lambda *a, **k: None)
    monkeypatch.setattr("backend.app.ai.analyze_transcript_groq", lambda *a, **k: None)
    yield


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


def test_triage_call_audio_structure(offline_models):
    audio_bytes = _generate_dummy_wav(0.5)
    res = triage_call_audio(audio_bytes, mime_type="audio/wav")

    assert "triage_priority_score" in res
    assert 1 <= res["triage_priority_score"] <= 100
    assert "urgency_level" in res
    assert "urgency_badge" in res
    assert "scam_indicators" in res
    assert isinstance(res["scam_indicators"], list)
    assert "officer_sop" in res
    assert isinstance(res["officer_sop"], list)
    assert "emotions" in res
    for key in ("fear", "distress", "anger", "nervousness", "neutral"):
        assert key in res["emotions"]
    assert res["responsible_ai_advisory"] == RESPONSIBLE_AI_ADVISORY
    # The console must be able to say whether these numbers are measured.
    assert res["emotions"]["source"] in {"aws_ser", "hf_ser", "acoustic"}
    assert res["emotions_source"] == res["emotions"]["source"]


def test_emotion_estimate_is_deterministic_and_labelled():
    """No random jitter: identical audio must yield identical numbers."""
    features = {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0}
    first = synthesize_fused_emotions(features, {"success": False, "map": {}}, "CYBER REPORT", 0.4)
    second = synthesize_fused_emotions(features, {"success": False, "map": {}}, "CYBER REPORT", 0.4)
    assert first == second
    assert first["source"] == "acoustic"
    assert 0.0 <= first["vocal_distress_score"] <= 1.0


def test_aws_emotions_are_preferred_and_not_rescaled():
    result = synthesize_fused_emotions(
        {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0},
        {
            "source": "aws",
            "dominant_emotion": "fear",
            "vocal_distress_score": 0.87,
            "emotions": {"fear": 0.9, "distress": 0.8, "anger": 0.1, "nervousness": 0.7, "neutral": 0.05},
        },
        "ACTIVE WIRE FRAUD",
        0.8,
    )
    assert result["source"] == "aws_ser"
    assert result["fear"] == 0.9
    assert result["dominant_emotion"] == "fear"


def test_forensic_schema_is_canonical():
    """The single contract shared with frontend/officer/js/vault.js."""
    out = _normalize_forensics(
        {},
        {"incident_category": "ACTIVE WIRE FRAUD", "triage_priority_score": 70},
        {},
    )
    assert set(out) == {
        "analyzed_at",
        "caller_dossier",
        "suspect_profile",
        "financial_impact",
        "attack_vector",
        "key_facts_timeline",
        "actionable_investigation_sop",
        "psychological_evaluation",
        "triage_verdict",
        "emotions",
        "responsible_ai_advisory",
    }
    assert set(out["caller_dossier"]) == {"name", "contact", "location", "language_spoken"}
    assert set(out["suspect_profile"]) == {
        "claimed_identity",
        "impersonated_agency",
        "phone_numbers",
        "digital_handles",
    }
    assert set(out["financial_impact"]) == {
        "amount_lost",
        "amount_demanded",
        "destination_upi_ids",
        "destination_bank_accounts",
        "victim_bank",
    }
    assert set(out["attack_vector"]) == {
        "scam_type",
        "coercion_technique",
        "malicious_tools",
    }
    assert set(out["psychological_evaluation"]) == {
        "dominant_state",
        "summary",
        "vulnerability_level",
    }
    assert set(out["triage_verdict"]) == {
        "priority_score",
        "urgency_badge",
        "category",
        "vulnerability_assessment",
    }
    assert set(out["emotions"]) == {
        "fear",
        "distress",
        "anger",
        "nervousness",
        "neutral",
        "dominant",
        "vocal_distress_score",
        "source",
    }
    # Fields are never rendered as Python objects.
    assert isinstance(out["psychological_evaluation"], dict)
    assert isinstance(out["psychological_evaluation"]["summary"], str)
    assert out["triage_verdict"]["category"] == "ACTIVE WIRE FRAUD"
    assert out["triage_verdict"]["priority_score"] == 70


def test_forensic_normalization_survives_hostile_model_output():
    """LLMs return surprises: prose, strings where lists belong, percentages."""
    out = _normalize_forensics(
        {
            "caller_dossier": "the caller is Ramesh",
            "psychological_evaluation": "Acute panic throughout the call.",
            "suspect_profile": {"phone_numbers": "+91 90000 00000"},
            "financial_impact": {"amount_lost": "₹2,50,000", "destination_upi_ids": None},
            "triage_verdict": {"priority_score": "96/100"},
            "key_facts_timeline": "single fact",
        },
        {},
        {"fear": "88%", "distress": 0.2},
    )
    assert out["caller_dossier"]["name"] == "Not Disclosed"
    assert out["psychological_evaluation"]["summary"] == "Acute panic throughout the call."
    assert out["suspect_profile"]["phone_numbers"] == ["+91 90000 00000"]
    assert out["financial_impact"]["destination_upi_ids"] == []
    assert out["triage_verdict"]["priority_score"] == 96
    assert out["key_facts_timeline"] == ["single fact"]
    assert out["emotions"]["fear"] == 0.88


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
