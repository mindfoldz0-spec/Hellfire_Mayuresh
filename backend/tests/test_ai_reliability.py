"""AI provider parsing tests using recorded responses (no network)."""

from __future__ import annotations

import json

from backend.app import ai
from backend.app.ai import (
    analyze_call_forensics,
    analyze_transcript_groq,
    synthesize_fused_emotions,
    transcribe_audio_sarvam,
    triage_text_statement,
)


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def groq_payload(**overrides):
    body = {
        "priority_score": 72,
        "urgency_level": "URGENT",
        "urgency_badge": "⚠️ URGENT",
        "incident_category": "ACTIVE WIRE FRAUD",
        "incident_summary": "Money was drained without permission; caller is distressed.",
        "scam_indicators": ["otp fraud", "impersonation"],
        "officer_sop": ["Freeze the destination account", "Log 1930 report"],
    }
    body.update(overrides)
    return {
        "choices": [{"message": {"content": json.dumps(body)}}],
    }


def test_groq_parses_recorded_triage_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(ai, "GROQ_MODEL", "test-model")
    seen = {}

    def fake_request(provider, method, url, **kwargs):
        seen["provider"] = provider
        seen["payload"] = kwargs.get("json")
        return FakeResponse(200, groq_payload())

    monkeypatch.setattr(ai.providers, "request", fake_request)
    result = analyze_transcript_groq(
        "My bank account was drained by an impersonator calling me about an urgent case."
    )
    assert seen["provider"] == "groq"
    assert seen["payload"]["model"] == "test-model"
    assert result["priority_score"] == 72
    assert result["officer_sop"][0].startswith("Freeze")


def test_groq_non_200_returns_none(monkeypatch):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        ai.providers, "request", lambda *a, **k: FakeResponse(500, {"error": "boom"})
    )
    assert analyze_transcript_groq("I need help immediately please") is None


def test_groq_no_answer_returns_none(monkeypatch):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(ai.providers, "request", lambda *a, **k: None)
    assert analyze_transcript_groq("I need help immediately please") is None


def test_groq_empty_text_is_never_sent(monkeypatch):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "test-key")
    called = []
    monkeypatch.setattr(ai.providers, "request", lambda *a, **k: called.append(1))
    assert analyze_transcript_groq("") is None
    assert analyze_transcript_groq("hi") is None
    assert called == []


def test_sarvam_transcription_parses_recorded_response(monkeypatch, tmp_path):
    audio = tmp_path / "statement.wav"
    audio.write_bytes(b"fake-wav")
    monkeypatch.setattr(ai, "SARVAM_API_KEY", "test-key")

    def fake_request(provider, method, url, **kwargs):
        assert provider == "sarvam"
        assert kwargs["files"]["file"][2] == "audio/wav"
        return FakeResponse(200, {"transcript": "मेरे खाते से पैसे निकाल लिए गए", "language_code": "hi-IN"})

    monkeypatch.setattr(ai.providers, "request", fake_request)
    out = transcribe_audio_sarvam(str(audio), "hi-IN")
    assert out and out.startswith("मेरे")


def test_sarvam_file_missing_short_circuits(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "SARVAM_API_KEY", "test-key")
    called = []
    monkeypatch.setattr(ai.providers, "request", lambda *a, **k: called.append(1))
    assert transcribe_audio_sarvam(str(tmp_path / "nope.wav")) is None
    assert called == []


def test_offline_text_triage_is_deterministic(monkeypatch):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "")
    statement = (
        "Someone posing as CBI demanded money; they drained my bank account "
        "and I am scared to go back to him because he threatened me."
    )
    first = triage_text_statement(statement)
    second = triage_text_statement(statement)
    assert first == second
    assert first["ai_status"]["channel"] == "text"
    assert first["responsible_ai_advisory"].startswith("Responsible AI")
    assert first["emotions"]["source"] == "acoustic"
    assert 1 <= first["triage_priority_score"] <= 100


def test_forensics_use_recorded_groq_response(monkeypatch):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(ai, "GROQ_MODEL", "test-model")
    dossier = {
        "caller_dossier": {
            "name": "Ramesh Kumar",
            "contact": "+91 9000000000",
            "location": "Pune",
            "language_spoken": "Hinglish",
        },
        "suspect_profile": {
            "claimed_identity": "DCP Vikram Shinde",
            "impersonated_agency": "CBI",
            "phone_numbers": ["+91 9111111111"],
            "digital_handles": ["fake-cbi@t.me"],
        },
        "financial_impact": {
            "amount_lost": "₹50,000",
            "amount_demanded": "₹2,00,000",
            "destination_upi_ids": ["fraud@icici"],
            "destination_bank_accounts": ["123456789012"],
            "victim_bank": "SBI",
        },
        "attack_vector": {
            "scam_type": "Digital Arrest",
            "coercion_technique": "Narcotic arrest threat",
            "malicious_tools": ["AnyDesk"],
        },
        "key_facts_timeline": ["Scam call received", "Victim bought gift cards"],
        "actionable_investigation_sop": [
            "Freeze destination UPI",
            "File 1930",
            "Register FIR",
        ],
        "psychological_evaluation": {
            "dominant_state": "Acute Panic",
            "summary": "Voice trembling; high duress.",
            "vulnerability_level": "HIGH",
        },
        "triage_verdict": {"priority_score": 88, "urgency_badge": "🚨 VERY VERY URGENT"},
    }

    def fake_request(provider, method, url, **kwargs):
        assert provider == "groq"
        return FakeResponse(
            200, {"choices": [{"message": {"content": json.dumps(dossier)}}]}
        )

    monkeypatch.setattr(ai.providers, "request", fake_request)
    out = analyze_call_forensics(
        transcript="CBI digital arrest demanded money from me",
        notes="victim distressed",
        triage_info={"incident_category": "ACTIVE EXTORTION / RANSOMWARE", "triage_priority_score": 90},
    )
    assert out["caller_dossier"]["name"] == "Ramesh Kumar"
    assert out["financial_impact"]["destination_upi_ids"] == ["fraud@icici"]
    assert out["triage_verdict"]["priority_score"] == 88
    assert out["triage_verdict"]["category"] == "ACTIVE EXTORTION / RANSOMWARE"
    assert out["responsible_ai_advisory"].startswith("Responsible AI")


def test_forensics_fallback_is_used_when_groq_is_absent(monkeypatch):
    monkeypatch.setattr(ai, "GROQ_API_KEY", "")
    monkeypatch.setattr(ai, "query_speech_emotion", lambda *a, **k: {"success": False, "map": {}})
    out = analyze_call_forensics(
        transcript="They threatened me with arrest and demanded money",
        notes="Officer observed panic",
        triage_info={"incident_category": "ACTIVE EXTORTION / RANSOMWARE",
                    "triage_priority_score": 78,
                    "urgency_badge": "⚠️ URGENT"},
        audio_path=None,
    )
    assert out["attack_vector"]["scam_type"] == "ACTIVE EXTORTION / RANSOMWARE"
    assert out["triage_verdict"]["priority_score"] == 78
    assert len(out["actionable_investigation_sop"]) >= 3


def test_aws_ser_beats_hf_when_both_are_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ai,
        "query_aws_emotion",
        lambda path: {
            "success": True,
            "source": "aws",
            "dominant_emotion": "fear",
            "vocal_distress_score": 0.9,
            "emotions": {"fear": 0.9, "distress": 0.5, "anger": 0.1, "nervousness": 0.2, "neutral": 0.1},
        },
    )
    monkeypatch.setattr(
        ai,
        "query_huggingface_emotion",
        lambda path: {"success": True, "map": {"fear": 0.1, "neutral": 0.9}, "label": "calm"},
    )
    out = ai.query_speech_emotion("C:/irrelevant.wav")
    assert out["source"] == "aws"


def test_aws_ser_absent_falls_back_to_hf(monkeypatch):
    monkeypatch.setattr(ai, "query_aws_emotion", lambda path: None)
    monkeypatch.setattr(
        ai,
        "query_huggingface_emotion",
        lambda path: {"success": True, "map": {"sad": 0.8}, "label": "sad"},
    )
    out = ai.query_speech_emotion("C:/irrelevant.wav")
    assert out["map"]["sad"] == 0.8


def test_fused_emotion_hardened_on_hostile_sers():
    out = synthesize_fused_emotions(
        {"loudness_factor": 2.0, "zcr": 0.5, "rms": 0.2},
        {"source": "hf_ser", "success": True, "map": {"fearful": "90%", "angry": 0.3, "sad": "unclear"}},
        "HIGHEST",
        0.95,
    )
    assert out["fear"] == 0.9 and out["anger"] == 0.3
    assert out["source"] == "hf_ser"


def test_llm_verdict_escalates_low_priority_composite(monkeypatch):
    """A stated VERY_VERY_URGENT verdict must not be diluted by acoustic fusion."""
    monkeypatch.setattr(ai, "AI_OFFLINE", False)
    monkeypatch.setattr(
        ai, "extract_acoustic_features", lambda path: {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0}
    )
    monkeypatch.setattr(ai, "query_speech_emotion", lambda path: {"success": False, "map": {}, "label": "neutral"})
    monkeypatch.setattr(
        ai, "transcribe_audio_sarvam", lambda path, language="": "They threatened me with arrest and demanded money"
    )
    monkeypatch.setattr(
        ai,
        "analyze_transcript_groq",
        lambda text: {
            "priority_score": 10,
            "urgency_level": "VERY_VERY_URGENT",
            "urgency_badge": "🚨 VERY VERY URGENT",
            "incident_category": "DIGITAL ARREST",
            "incident_summary": "Digital arrest rework.",
            "scam_indicators": ["arrest threat"],
            "officer_sop": ["Freeze the demand path"],
        },
    )
    out = ai.triage_call_audio(b"not really a wav; IO is mocked", mime_type="audio/wav")
    assert out["triage_priority_score"] >= 85
    assert out["urgency_level"] == "VERY_VERY_URGENT"
    assert out["incident_category"] == "DIGITAL ARREST"
