"""AI Speech Emotion Recognition and Fused Cyber Emergency Call Triage.

Integrates:
1. Acoustic feature analysis (RMS volume, pitch tremor, zero-crossing rate).
2. Acoustic SER via Hugging Face Space (Wav2Vec2 XLS-R ZeroGPU).
3. Incident semantic analysis (Accidents, Suicide distress, Ransomware, Bank draining).
4. Realistic 60/40 Audio-Semantic Fusion formula into a Composite Priority Score (1-100).
5. Problem Statement compliance: Mandatory Responsible AI Ethics Advisory guardrail.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import os
import re
import struct
import tempfile
import time
import wave
from typing import Any, Dict, List, Optional, Tuple

from backend.app import providers
from backend.app.config import (
    AI_OFFLINE,
    AWS_EMOTION_TIMEOUT_SECONDS,
    AWS_EMOTION_URL,
    CERS_SER_API_KEY,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_TIMEOUT_SECONDS,
    HF_SPACE_ID,
    HUGGINGFACE_API_KEY,
    SARVAM_API_KEY,
    SARVAM_LANGUAGE_CODE,
    SARVAM_MODEL,
    SARVAM_TIMEOUT_SECONDS,
)

logger = logging.getLogger("cers.ai")

# ---------------------------------------------------------------------------
# Responsible AI Ethics Advisory
# ---------------------------------------------------------------------------
RESPONSIBLE_AI_ADVISORY = (
    "Responsible AI Advisory: Emotion predictions are indicative signals for call triage "
    "prioritization only, not legal evidence of honesty, guilt, or intent."
)

NOT_DISCLOSED = "Not Disclosed"


# ---------------------------------------------------------------------------
# Small coercion helpers — LLM output is untrusted input
# ---------------------------------------------------------------------------
def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _as_text(value: Any, default: str = NOT_DISCLOSED) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        joined = ", ".join(str(item).strip() for item in value if str(item).strip())
        return joined or default
    return default


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group())
    return default


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}

# ---------------------------------------------------------------------------
# Incident Threat Keyword Dictionaries
# ---------------------------------------------------------------------------
CRITICAL_LIFE_KEYWORDS = [
    r"\baccident\b",
    r"\bcrash\b",
    r"\bbleed(?:ing)?\b",
    r"\bhospital\b",
    r"\bambulance\b",
    r"\binjur(?:ed|y)\b",
    r"\bhit and run\b",
    r"\bcollision\b",
    r"\bcasualt(?:y|ies)\b",
]

SUICIDE_DISTRESS_KEYWORDS = [
    r"\bsuicid(?:e|al)\b",
    r"\bkill myself\b",
    r"\bend(?:ing)? my life\b",
    r"\bhang(?:ing)? myself\b",
    r"\bpoison\b",
    r"\bcan't live\b",
    r"\bwant to die\b",
    r"\bgiving up on life\b",
]

ACTIVE_EXTORTION_KEYWORDS = [
    r"\bransomware\b",
    r"\bencrypt(?:ed)?\b",
    r"\bhostage\b",
    r"\bextort(?:ion)?\b",
    r"\bblackmail\b",
    r"\bthreaten(?:ed|ing)?\b",
    r"\bgun\b",
    r"\bweapon\b",
]

FINANCIAL_FRAUD_KEYWORDS = [
    r"\bempty(?:ing)? account\b",
    r"\bwire transfer\b",
    r"\bunauthorized transaction\b",
    r"\botp stolen\b",
    r"\bstole(?:n)? all (?:my )?money\b",
    r"\blife savings gone\b",
    r"\bbank drained\b",
    r"\bhacked my bank\b",
]

CYBER_HARASSMENT_KEYWORDS = [
    r"\bdeepfake\b",
    r"\bmorph(?:ed)? (?:photo|video)\b",
    r"\bblackmailing with photos\b",
    r"\bprivate photos leaked\b",
    r"\bstalk(?:ing|er)\b",
    r"\bdefamat(?:ion|ory)\b",
]


def extract_acoustic_features(audio_path: str) -> Dict[str, float]:
    """Extract raw acoustic physical waveform parameters (RMS energy, tremor, ZCR)."""
    try:
        with wave.open(audio_path, "rb") as w:
            n_frames = w.getnframes()
            channels = w.getnchannels()
            sample_width = w.getsampwidth()
            if n_frames == 0:
                return {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0}
            if sample_width != 2:
                raise ValueError(f"unsupported sample width: {sample_width * 8}-bit")
            raw = w.readframes(n_frames)
            total = len(raw) // 2
            if total <= 0:
                return {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0}
            samples: list[float] = list(struct.unpack(f"<{total}h", raw))
            if channels > 1:
                # Fold interleaved channels down to mono.
                mono = [
                    sum(samples[i : i + channels]) / channels
                    for i in range(0, len(samples) - channels + 1, channels)
                ]
                if mono:
                    samples = mono

            # RMS Volume (0.0 to 1.0)
            sum_sq = sum(s * s for s in samples)
            rms = math.sqrt(sum_sq / len(samples)) / 32768.0

            # Zero crossing rate (vocal tremor / high-frequency breathiness)
            zc = sum(
                1 for i in range(1, len(samples))
                if (samples[i] >= 0 > samples[i - 1]) or (samples[i] < 0 <= samples[i - 1])
            )
            zcr = zc / len(samples)

            # Loudness scale: 0.02 is quiet, 0.12+ is shouting/loud
            loudness_factor = min(2.0, max(0.5, rms / 0.06))
            return {
                "rms": round(rms, 4),
                "zcr": round(zcr, 4),
                "loudness_factor": round(loudness_factor, 2),
            }
    except Exception as exc:
        logger.warning("Could not extract acoustic features: %s", exc)
        return {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0}


def classify_incident_semantics(transcript: str) -> Tuple[float, str, str]:
    """Scan transcript for emergency categories and return (severity 0.0-1.0, category, summary)."""
    text = (transcript or "").lower().strip()
    if not text:
        return 0.30, "UNCATEGORIZED", "Awaiting caller voice statement."

    # 1. Critical Life Safety: Accident
    for pattern in CRITICAL_LIFE_KEYWORDS:
        if re.search(pattern, text):
            return 1.0, "ACCIDENT / LIFE THREAT", f"High lethality incident: '{text[:80]}'"

    # 2. Critical Life Safety: Suicide Distress
    for pattern in SUICIDE_DISTRESS_KEYWORDS:
        if re.search(pattern, text):
            return 1.0, "SUICIDE DISTRESS", f"Critical self-harm distress: '{text[:80]}'"

    # 3. Active Ransomware / Hostage Extortion
    for pattern in ACTIVE_EXTORTION_KEYWORDS:
        if re.search(pattern, text):
            return 0.95, "ACTIVE EXTORTION / RANSOMWARE", f"Active cyber extortion: '{text[:80]}'"

    # 4. Active Financial Draining
    for pattern in FINANCIAL_FRAUD_KEYWORDS:
        if re.search(pattern, text):
            return 0.85, "ACTIVE WIRE FRAUD", f"Live bank draining: '{text[:80]}'"

    # 5. Cyber Harassment / Blackmail
    for pattern in CYBER_HARASSMENT_KEYWORDS:
        if re.search(pattern, text):
            return 0.65, "CYBER HARASSMENT / BLACKMAIL", f"Targeted harassment: '{text[:80]}'"

    # General Cyber Report
    return 0.40, "CYBER REPORT", f"Statement: '{text[:80]}'"


def transcribe_audio_sarvam(audio_path: str, language: str = "") -> Optional[str]:
    """Transcribe audio using Sarvam AI Speech-to-Text API if available.

    ``language`` overrides the configured default (per-call IVR selection).
    """
    if not SARVAM_API_KEY or not os.path.isfile(audio_path):
        return None

    try:
        url = "https://api.sarvam.ai/speech-to-text"
        headers = {"api-subscription-key": SARVAM_API_KEY}
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/wav")}
            data = {
                "model": SARVAM_MODEL or "saaras:v4",
                "language_code": language or SARVAM_LANGUAGE_CODE or "unknown",
            }
            res = providers.request(
                "sarvam", "POST", url,
                headers=headers, files=files, data=data,
                timeout=SARVAM_TIMEOUT_SECONDS,
            )
            if res is None:
                return None
            if res.status_code == 200:
                body = res.json()
                transcript = body.get("transcript", "").strip()
                detected_lang = body.get("language_code", "unknown")
                if transcript:
                    logger.info("Sarvam ASR transcribed [%s]: '%s'", detected_lang, transcript)
                    return transcript
            else:
                logger.warning("Sarvam ASR status %d: %s", res.status_code, res.text[:200])
    except Exception as exc:
        logger.warning("Sarvam ASR transcription failed: %s", exc)
    return None


def analyze_transcript_groq(transcript: str) -> Optional[Dict[str, Any]]:
    """Analyze emergency transcript using Groq Qwen (qwen/qwen3.8-27b) with sub-second latency."""
    api_key = GROQ_API_KEY
    if not api_key:
        return None
    text = (transcript or "").strip()
    if not text or len(text) < 4:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    prompt = (
        "You are the AI triage copilot for the Cyber Emergency Response System (CERS).\n"
        "Analyze the caller's spoken emergency transcript (which may be in English, Hindi, Marathi, or code-mixed Hinglish):\n"
        f"'{text}'\n\n"
        "Respond ONLY in valid JSON with these exact fields (provide all string values in clear English so dispatch officers can immediately read and execute):\n"
        "{\n"
        '  "priority_score": <integer 1-100>,\n'
        '  "urgency_level": <"VERY_VERY_URGENT" | "URGENT" | "ELEVATED" | "ROUTINE">,\n'
        '  "urgency_badge": <"🚨 VERY VERY URGENT" | "⚠️ URGENT" | "🟡 ELEVATED" | "🟢 ROUTINE">,\n'
        '  "incident_category": <string in English>,\n'
        '  "incident_summary": <concise 1-2 sentence emergency summary in English>,\n'
        '  "scam_indicators": [<list of strings in English>],\n'
        '  "officer_sop": <tactical SOP steps for the police officer in English>\n'
        "}"
    )

    try:
        payload = {
            "model": GROQ_MODEL or "qwen/qwen3.8-27b",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a high-speed emergency police dispatch triage AI. Always respond in pure JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        res = providers.request(
            "groq", "POST", url,
            headers=headers, json=payload, timeout=GROQ_TIMEOUT_SECONDS,
        )
        if res is None:
            return None
        if res.status_code == 200:
            content = res.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            logger.info("Groq Qwen triage completed: %s", parsed.get("incident_category"))
            return parsed
        else:
            logger.warning("Groq API status %d: %s", res.status_code, res.text[:200])
    except Exception as exc:
        logger.warning("Groq Qwen triage failed: %s", exc)
    return None


_hf_client = None


def _get_hf_client():
    global _hf_client
    if _hf_client is None:
        from gradio_client import Client

        space_id = HF_SPACE_ID or "mindfoldz0/cers-voice-emotion"
        _hf_client = Client(space_id, token=HUGGINGFACE_API_KEY or None)
    return _hf_client


def query_huggingface_emotion(audio_path: str) -> Dict[str, Any]:
    """Query the deployed Hugging Face Space for acoustic speech emotion recognition."""
    global _hf_client
    if not providers.available("hf_ser"):
        return {"success": False, "map": {}, "label": "neutral"}
    try:
        from gradio_client import handle_file

        client = _get_hf_client()
        res = client.predict(
            audio_path=handle_file(audio_path),
            api_name="/_infer",
        )

        conf_list = res.get("confidences", [])
        emotions_map: Dict[str, float] = {}
        for item in conf_list:
            emotions_map[item.get("label", "").lower()] = float(item.get("confidence", 0.0))

        providers.registry.record_success("hf_ser", 0.0)
        return {"success": True, "map": emotions_map, "label": res.get("label", "neutral")}
    except Exception as exc:
        logger.warning("Hugging Face Space query error: %s", exc)
        providers.registry.record_failure("hf_ser", str(exc))
        _hf_client = None  # Reset client in case of connection drop
        return {"success": False, "map": {}, "label": "neutral"}


def query_aws_emotion(audio_path: str) -> Optional[Dict[str, Any]]:
    """Query dedicated AWS EC2 Speech Emotion Recognition microservice."""
    url = AWS_EMOTION_URL
    if not url or not os.path.isfile(audio_path):
        return None
    try:
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/wav")}
            headers = (
                {"Authorization": f"Bearer {CERS_SER_API_KEY}"}
                if CERS_SER_API_KEY
                else None
            )
            res = providers.request(
                "aws_ser",
                "POST",
                url,
                files=files,
                headers=headers,
                timeout=AWS_EMOTION_TIMEOUT_SECONDS,
            )
            if res is None:
                return None
            if res.status_code == 200:
                data = res.json()
                logger.info(
                    "AWS Dedicated SER returned: %s (vocal distress: %s)",
                    data.get("dominant_emotion"),
                    data.get("vocal_distress_score"),
                )
                return {
                    "success": True,
                    "dominant_emotion": data.get("dominant_emotion", "neutral"),
                    "vocal_distress_score": float(data.get("vocal_distress_score", 0.0)),
                    "emotions": data.get("emotions", {}),
                    "prosody": data.get("prosody", {}),
                    "source": "aws",
                }
            else:
                logger.warning("AWS SER status %d: %s", res.status_code, res.text)
    except Exception as exc:
        logger.warning("AWS SER request error: %s", exc)
    return None


def query_speech_emotion(audio_path: str) -> Dict[str, Any]:
    """Query speech emotion recognition with priority: AWS Dedicated -> HF Space fallback."""
    aws_res = query_aws_emotion(audio_path)
    if aws_res and aws_res.get("success"):
        return aws_res
    return query_huggingface_emotion(audio_path)


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group())
    return default


def _as_probability(value: Any, default: float = 0.0) -> float:
    """Accept 0..1 floats or 0..100 percentages and normalize to 0..1."""
    number = _as_float(value, default)
    if number > 1.0:
        number = number / 100.0
    return _clamp(number)


def _distress_index(fear: float, distress: float, anger: float, nervousness: float) -> float:
    return _clamp(fear * 0.40 + distress * 0.35 + anger * 0.15 + nervousness * 0.10)


def synthesize_fused_emotions(
    acoustic: Dict[str, float],
    ser_result: Dict[str, Any],
    incident_category: str,
    incident_severity: float,
) -> Dict[str, Any]:
    """Build the emotion spectrum from the best source available.

    The returned ``source`` field states which evidence produced the numbers,
    so the console never presents a heuristic estimate as a measured signal:

    * ``aws_ser``   – dedicated Wav2Vec2 XLS-R microservice (calibrated)
    * ``hf_ser``    – Hugging Face ZeroGPU space classifier
    * ``acoustic``  – deterministic prosody estimate (RMS/ZCR/severity only)

    There is deliberately no random jitter: identical audio must always
    produce identical triage numbers.
    """
    # 1. Dedicated AWS SER — highest fidelity, already calibrated.
    if ser_result.get("source") == "aws" and ser_result.get("emotions"):
        emo = _as_dict(ser_result["emotions"])
        fear = _as_probability(emo.get("fear"), 0.08)
        distress = _as_probability(emo.get("distress"), 0.08)
        anger = _as_probability(emo.get("anger"), 0.08)
        nervousness = _as_probability(emo.get("nervousness"), 0.08)
        neutral = _as_probability(emo.get("neutral"), 0.60)
        dominant = _as_text(ser_result.get("dominant_emotion"), "neutral").lower()
        distress_score = _as_probability(
            ser_result.get("vocal_distress_score"),
            _distress_index(fear, distress, anger, nervousness),
        )
        return {
            "fear": round(fear, 2),
            "distress": round(distress, 2),
            "anger": round(anger, 2),
            "nervousness": round(nervousness, 2),
            "neutral": round(neutral, 2),
            "dominant_emotion": dominant,
            "vocal_distress_score": round(distress_score, 3),
            "source": "aws_ser",
        }

    # 2. Hugging Face ZeroGPU space.
    hf_map = _as_dict(ser_result.get("map"))
    if ser_result.get("success") and hf_map:
        fear = _as_probability(hf_map.get("fearful", hf_map.get("fear")))
        anger = _as_probability(hf_map.get("angry", hf_map.get("anger")))
        distress = _as_probability(hf_map.get("sad", hf_map.get("distress")))
        nervousness = _as_probability(hf_map.get("surprised", hf_map.get("nervous")))
        neutral = _as_probability(hf_map.get("neutral", hf_map.get("calm")), 0.0)
        if any((fear, anger, distress, nervousness)):
            scores = {
                "fearful": fear,
                "distress": distress,
                "angry": anger,
                "nervousness": nervousness,
                "neutral": neutral,
            }
            dominant = max(scores.items(), key=lambda item: item[1])[0]
            return {
                "fear": round(fear, 2),
                "distress": round(distress, 2),
                "anger": round(anger, 2),
                "nervousness": round(nervousness, 2),
                "neutral": round(neutral, 2),
                "dominant_emotion": dominant,
                "vocal_distress_score": round(
                    _distress_index(fear, distress, anger, nervousness), 3
                ),
                "source": "hf_ser",
            }

    # 3. No SER model reachable: derive deterministically from measured prosody
    #    plus the semantic severity of the statement. Clearly labelled.
    loudness = _clamp(_as_float(acoustic.get("loudness_factor"), 1.0), 0.5, 2.0)
    zero_crossing = _clamp(_as_float(acoustic.get("zcr"), 0.05), 0.0, 0.5)
    energy = _clamp((loudness - 0.5) / 1.5)
    tremor = _clamp(zero_crossing / 0.25)
    severity = _clamp(_as_float(incident_severity, 0.3))

    fear = _clamp(0.22 + 0.40 * energy + 0.20 * tremor + 0.15 * severity)
    distress = _clamp(0.18 + 0.30 * energy + 0.20 * tremor + 0.30 * severity)
    anger = _clamp(0.14 + 0.34 * energy)
    nervousness = _clamp(0.15 + 0.45 * tremor)
    neutral = _clamp(1.0 - max(fear, distress, anger))

    scores = {
        "fearful": fear,
        "distress": distress,
        "angry": anger,
        "nervousness": nervousness,
        "neutral": neutral,
    }
    dominant = max(scores.items(), key=lambda item: item[1])[0]
    return {
        "fear": round(fear, 2),
        "distress": round(distress, 2),
        "anger": round(anger, 2),
        "nervousness": round(nervousness, 2),
        "neutral": round(neutral, 2),
        "dominant_emotion": dominant,
        "vocal_distress_score": round(
            _distress_index(fear, distress, anger, nervousness), 3
        ),
        "source": "acoustic",
    }


def _ai_status(
    transcript: Optional[str],
    ser_result: Dict[str, Any],
    groq_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """State exactly which evidence produced this triage.

    The console renders this so an officer can tell a measured dossier from a
    heuristic one during a provider outage.
    """
    if AI_OFFLINE:
        asr = "offline"
        llm = "offline"
    else:
        asr = "ok" if transcript else ("no_key" if not SARVAM_API_KEY else "failed")
        if not GROQ_API_KEY:
            llm = "no_key"
        elif not transcript:
            llm = "skipped"
        else:
            llm = "ok" if groq_result else "failed"

    if ser_result.get("source") == "aws":
        ser = "aws_ser"
    elif ser_result.get("success") and ser_result.get("map"):
        ser = "hf_ser"
    else:
        ser = "none"

    return {
        "asr": asr,
        "ser": ser,
        "llm": llm,
        "degraded": asr != "ok" or llm != "ok" or ser == "none",
        "offline": AI_OFFLINE,
    }


def triage_call_audio(
    audio_bytes: bytes, mime_type: str = "audio/wav", language: str = ""
) -> Dict[str, Any]:
    """Execute complete 60/40 acoustic-semantic fusion triage pipeline on WAV audio."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_path = tmp_file.name
        tmp_file.write(audio_bytes)

    try:
        # 1. Physical Acoustic Waveform Features (Instant: ~5ms)
        acoustic_features = extract_acoustic_features(tmp_path)

        # 2 & 3. Run Dedicated Speech Emotion and Sarvam ASR in PARALLEL threads
        if AI_OFFLINE:
            ser_result: Dict[str, Any] = {"success": False, "map": {}}
            transcript: Optional[str] = None
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_ser = executor.submit(query_speech_emotion, tmp_path)
                future_asr = executor.submit(
                    transcribe_audio_sarvam, tmp_path, language
                )
                ser_result = future_ser.result()
                transcript = future_asr.result()

        # 4. Threat Semantic Classification
        incident_severity, incident_category, incident_summary = classify_incident_semantics(
            transcript or ""
        )
        scam_indicators: List[str] = []
        officer_sop: List[str] = []
        groq_result = None

        if transcript:
            groq_result = analyze_transcript_groq(transcript)

        groq_prio: Optional[int] = None
        if groq_result:
            groq_prio = _as_int(
                groq_result.get("priority_score"), int(round(incident_severity * 100))
            )
            groq_prio = max(1, min(100, groq_prio))
            incident_category = _as_text(
                groq_result.get("incident_category"), incident_category
            )
            incident_summary = _as_text(
                groq_result.get("incident_summary"), incident_summary
            )
            scam_indicators = _as_list(groq_result.get("scam_indicators"))
            officer_sop = _as_list(groq_result.get("officer_sop"))
            # Normalize to 0.1-1.0 for emotion synthesis
            incident_severity = max(0.1, min(1.0, groq_prio / 100.0))

        # 5. Multimodal fusion (acoustic + semantic). `emotions["source"]`
        #    states whether the numbers are measured or estimated.
        emotions = synthesize_fused_emotions(
            acoustic_features,
            ser_result,
            incident_category,
            incident_severity,
        )

        vocal_distress = emotions["vocal_distress_score"]

        if groq_result and groq_prio is not None:
            # Fuse acoustic vocal distress (40%) and Groq Qwen deep triage (60%)
            composite = int(round(vocal_distress * 40.0 + groq_prio * 0.60))
            urgency_level = _as_text(groq_result.get("urgency_level"), "ROUTINE")

            if urgency_level == "VERY_VERY_URGENT" or groq_prio >= 85:
                composite = max(85, composite)
                default_badge = "🚨 VERY VERY URGENT"
            elif urgency_level == "URGENT" or groq_prio >= 65:
                composite = max(65, composite)
                default_badge = "⚠️ URGENT"
            elif urgency_level == "ELEVATED" or groq_prio >= 40:
                composite = max(40, composite)
                default_badge = "🟡 ELEVATED"
            else:
                default_badge = "🟢 ROUTINE"
            urgency_badge = _as_text(
                groq_result.get("urgency_badge"), default_badge
            )
        else:
            # Fallback 60/40 Fusion: (Vocal Distress * 60) + (Incident Threat * 40)
            composite = int(round(vocal_distress * 60.0 + incident_severity * 40.0))

            # Enforce urgent priority tiers for life threats
            if incident_category in ("ACCIDENT / LIFE THREAT", "SUICIDE DISTRESS"):
                composite = max(95, composite)
            elif incident_category in ("ACTIVE EXTORTION / RANSOMWARE", "ACTIVE WIRE FRAUD"):
                composite = max(78, composite)

            composite = max(1, min(100, composite))

            if composite >= 85:
                urgency_level = "VERY_VERY_URGENT"
                urgency_badge = "🚨 VERY VERY URGENT"
            elif composite >= 65:
                urgency_level = "URGENT"
                urgency_badge = "⚠️ URGENT"
            elif composite >= 40:
                urgency_level = "ELEVATED"
                urgency_badge = "🟡 ELEVATED"
            else:
                urgency_level = "ROUTINE"
                urgency_badge = "🟢 ROUTINE"

        composite = max(1, min(100, composite))

        return {
            "triage_priority_score": composite,
            "urgency_level": urgency_level,
            "urgency_badge": urgency_badge,
            "incident_category": incident_category,
            "incident_summary": incident_summary,
            "transcript": transcript or "Acoustic audio stream analyzed.",
            "scam_indicators": scam_indicators,
            "officer_sop": officer_sop,
            "emotions": {
                "fear": emotions["fear"],
                "distress": emotions["distress"],
                "anger": emotions["anger"],
                "nervousness": emotions["nervousness"],
                "neutral": emotions["neutral"],
                "dominant": emotions["dominant_emotion"],
                "vocal_distress_score": vocal_distress,
                "source": emotions["source"],
            },
            "emotions_source": emotions["source"],
            "ai_status": _ai_status(transcript, ser_result, groq_result),
            "acoustic": acoustic_features,
            "responsible_ai_advisory": RESPONSIBLE_AI_ADVISORY,
        }
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def triage_text_statement(text: str) -> Dict[str, Any]:
    """Triage a typed statement (caller has no working microphone).

    There is no audio, so no ASR and no SER: the score comes from semantic
    severity plus the lexical LLM, and the emotion block is explicitly labelled
    as an acoustic-free estimate.
    """
    statement = (text or "").strip()[:2000]
    severity, category, summary = classify_incident_semantics(statement)

    groq_result = analyze_transcript_groq(statement) if statement else None
    scam_indicators: List[str] = []
    officer_sop: List[str] = []
    groq_prio: Optional[int] = None

    if groq_result:
        groq_prio = max(
            1, min(100, _as_int(groq_result.get("priority_score"), int(round(severity * 100))))
        )
        category = _as_text(groq_result.get("incident_category"), category)
        summary = _as_text(groq_result.get("incident_summary"), summary)
        scam_indicators = _as_list(groq_result.get("scam_indicators"))
        officer_sop = _as_list(groq_result.get("officer_sop"))
        severity = max(0.1, min(1.0, groq_prio / 100.0))

    emotions = synthesize_fused_emotions(
        {"rms": 0.05, "zcr": 0.05, "loudness_factor": 1.0}, {}, category, severity
    )

    if groq_prio is not None:
        composite = max(1, min(100, groq_prio))
    else:
        composite = max(1, min(100, int(round(severity * 100))))

    if composite >= 85:
        urgency_level, urgency_badge = "VERY_VERY_URGENT", "\U0001f6a8 VERY VERY URGENT"
    elif composite >= 65:
        urgency_level, urgency_badge = "URGENT", "\u26a0\ufe0f URGENT"
    elif composite >= 40:
        urgency_level, urgency_badge = "ELEVATED", "\U0001f7e1 ELEVATED"
    else:
        urgency_level, urgency_badge = "ROUTINE", "\U0001f7e2 ROUTINE"

    return {
        "triage_priority_score": composite,
        "urgency_level": urgency_level,
        "urgency_badge": urgency_badge,
        "incident_category": category,
        "incident_summary": summary,
        "transcript": statement,
        "scam_indicators": scam_indicators,
        "officer_sop": officer_sop,
        "emotions": {
            "fear": emotions["fear"],
            "distress": emotions["distress"],
            "anger": emotions["anger"],
            "nervousness": emotions["nervousness"],
            "neutral": emotions["neutral"],
            "dominant": emotions["dominant_emotion"],
            "vocal_distress_score": emotions["vocal_distress_score"],
            "source": emotions["source"],
        },
        "emotions_source": emotions["source"],
        "acoustic": {"rms": 0.0, "zcr": 0.0, "loudness_factor": 0.0},
        "ai_status": {
            "asr": "skipped",
            "ser": "none",
            "llm": "ok" if groq_result else ("no_key" if not GROQ_API_KEY else "failed"),
            "degraded": True,
            "offline": AI_OFFLINE,
            "channel": "text",
        },
        "responsible_ai_advisory": RESPONSIBLE_AI_ADVISORY,
    }


def _normalize_emotions(raw: Any, fallback_dominant: str = "neutral") -> Dict[str, Any]:
    """Coerce an arbitrary emotion payload into the console's fixed shape."""
    emo = _as_dict(raw)
    fear = _as_probability(emo.get("fear"), 0.15)
    distress = _as_probability(emo.get("distress"), 0.20)
    anger = _as_probability(emo.get("anger"), 0.10)
    nervousness = _as_probability(emo.get("nervousness", emo.get("jitter")), 0.15)
    neutral = _as_probability(emo.get("neutral"), 0.40)
    dominant = _as_text(
        emo.get("dominant") or emo.get("dominant_emotion"), fallback_dominant
    ).lower()
    distress_score = _as_probability(
        emo.get("vocal_distress_score", emo.get("distress_score")),
        _distress_index(fear, distress, anger, nervousness),
    )
    return {
        "fear": round(fear, 2),
        "distress": round(distress, 2),
        "anger": round(anger, 2),
        "nervousness": round(nervousness, 2),
        "neutral": round(neutral, 2),
        "dominant": dominant,
        "vocal_distress_score": round(distress_score, 3),
        "source": _as_text(emo.get("source"), "unknown"),
    }


def _normalize_forensics(
    raw: Any, triage: Dict[str, Any], emotions: Dict[str, Any]
) -> Dict[str, Any]:
    """Return the forensic dossier in exactly one canonical shape.

    Everything the officer console renders is guaranteed to exist here, even
    when the model omits a field, renames one, or answers with prose. This is
    the single contract shared with ``frontend/officer/js/vault.js``.
    """
    payload = _as_dict(raw)
    caller = _as_dict(payload.get("caller_dossier"))
    suspect = _as_dict(payload.get("suspect_profile"))
    finance = _as_dict(payload.get("financial_impact"))
    vector = _as_dict(payload.get("attack_vector"))
    verdict = _as_dict(payload.get("triage_verdict"))

    psych_raw = payload.get("psychological_evaluation")
    psych = {"summary": psych_raw} if isinstance(psych_raw, str) else _as_dict(psych_raw)

    priority = _as_int(
        verdict.get("priority_score"),
        _as_int(triage.get("triage_priority_score"), 45),
    )
    priority = max(1, min(100, priority))
    vulnerability = _as_text(
        psych.get("vulnerability_level"), "MEDIUM"
    ).upper()

    return {
        "analyzed_at": int(time.time()),
        "caller_dossier": {
            "name": _as_text(caller.get("name")),
            "contact": _as_text(caller.get("contact"), "Emergency Line"),
            "location": _as_text(caller.get("location")),
            "language_spoken": _as_text(
                caller.get("language_spoken"), "Not detected"
            ),
        },
        "suspect_profile": {
            "claimed_identity": _as_text(
                suspect.get("claimed_identity"), "Unknown"
            ),
            "impersonated_agency": _as_text(
                suspect.get("impersonated_agency"), "Unknown / Not Disclosed"
            ),
            "phone_numbers": _as_list(suspect.get("phone_numbers")),
            "digital_handles": _as_list(suspect.get("digital_handles")),
        },
        "financial_impact": {
            "amount_lost": _as_text(finance.get("amount_lost"), "₹0"),
            "amount_demanded": _as_text(finance.get("amount_demanded"), "None"),
            "destination_upi_ids": _as_list(finance.get("destination_upi_ids")),
            "destination_bank_accounts": _as_list(
                finance.get("destination_bank_accounts")
            ),
            "victim_bank": _as_text(finance.get("victim_bank"), "Unknown"),
        },
        "attack_vector": {
            "scam_type": _as_text(
                vector.get("scam_type"),
                _as_text(triage.get("incident_category"), "Cyber Incident"),
            ),
            "coercion_technique": _as_text(
                vector.get("coercion_technique"), "Not identified"
            ),
            "malicious_tools": _as_list(vector.get("malicious_tools")),
        },
        "key_facts_timeline": _as_list(payload.get("key_facts_timeline")),
        "actionable_investigation_sop": _as_list(
            payload.get("actionable_investigation_sop")
        ),
        "psychological_evaluation": {
            "dominant_state": _as_text(
                psych.get("dominant_state"), "Undetermined"
            ),
            "summary": _as_text(psych.get("summary"), "No assessment available."),
            "vulnerability_level": vulnerability,
        },
        "triage_verdict": {
            "priority_score": priority,
            "urgency_badge": _as_text(
                verdict.get("urgency_badge"),
                _as_text(triage.get("urgency_badge"), "🟡 ELEVATED"),
            ),
            # Derived so the console does not need a second lookup.
            "category": _as_text(
                verdict.get("category"),
                _as_text(triage.get("incident_category"), "CYBER INCIDENT REPORT"),
            ),
            "vulnerability_assessment": vulnerability,
        },
        "emotions": _normalize_emotions(emotions),
        "responsible_ai_advisory": RESPONSIBLE_AI_ADVISORY,
    }


def analyze_call_forensics(
    transcript: str = "",
    notes: str = "",
    triage_info: Optional[Dict[str, Any]] = None,
    audio_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Perform post-call deep forensic intelligence extraction and psychological evaluation using Groq Qwen."""
    api_key = GROQ_API_KEY
    clean_transcript = (transcript or "").strip()
    clean_notes = (notes or "").strip()
    triage = triage_info or {}

    # Check if AWS SER can provide fresh acoustic emotions from the recorded file
    ser_data = None
    if audio_path and os.path.isfile(audio_path):
        try:
            ser_data = query_speech_emotion(audio_path)
        except Exception as e:
            logger.warning("SER analysis on recording failed: %s", e)

    emotions = _normalize_emotions(
        (ser_data or {}).get("emotions") or triage.get("emotions") or {}
    )

    combined_dialog = (
        f"CALLER SPOKEN TRANSCRIPT:\n{clean_transcript or 'No automated transcript available.'}\n\n"
        f"OFFICER OBSERVATIONS & DISPATCH LOG:\n{clean_notes or 'No officer notes recorded.'}"
    )

    if api_key and (clean_transcript or clean_notes):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        prompt = (
            "You are the Senior Cyber Forensic Investigator for the Cyber Emergency Response System (CERS).\n"
            "Perform an exhaustive post-call forensic entity extraction and incident reconstruction from this emergency call:\n\n"
            f"{combined_dialog}\n\n"
            "Analyze all statements in any language (English, Hindi, Marathi, Hinglish). Extract every critical detail mentioned by caller or officer.\n"
            "Respond ONLY in valid JSON matching this exact structure (all string fields must be in clear English):\n"
            "{\n"
            '  "caller_dossier": {\n'
            '    "name": "<victim/caller full name if mentioned, e.g. Ramesh Kumar, or \'Not Disclosed\'>",\n'
            '    "contact": "<caller phone/email if mentioned, or \'Not Disclosed\'>",\n'
            '    "location": "<caller city, state, or area mentioned, or \'Not Disclosed\'>",\n'
            '    "language_spoken": "<e.g. English, Hindi, Marathi, Hinglish>"\n'
            "  },\n"
            '  "suspect_profile": {\n'
            '    "claimed_identity": "<name or title scammer claimed, e.g. DCP Vikram Shinde / CBI Officer / Electricity Dept, or \'Unknown\'>",\n'
            '    "impersonated_agency": "<e.g. Mumbai Police, CBI, ED, FedEx, SBI, MSEDCL, TRAI>",\n'
            '    "phone_numbers": ["<suspect phone numbers mentioned>"],\n'
            '    "digital_handles": ["<Skype IDs, Telegram handles, URLs, email addresses>"]\n'
            "  },\n"
            '  "financial_impact": {\n'
            '    "amount_lost": "<amount already stolen/transferred, e.g. ₹50,000, or \'₹0\'>",\n'
            '    "amount_demanded": "<extortion demand, e.g. ₹2,00,000, or \'None\'>",\n'
            '    "destination_upi_ids": ["<UPI VPAs mentioned, e.g. fraud@icici>"],\n'
            '    "destination_bank_accounts": ["<bank accounts mentioned>"],\n'
            '    "victim_bank": "<victim\'s bank name, e.g. SBI, HDFC, or \'Unknown\'>"\n'
            "  },\n"
            '  "attack_vector": {\n'
            '    "scam_type": "<e.g. Digital Arrest / Law Enforcement Impersonation, Electricity Bill APK Fraud, Sextortion>",\n'
            '    "coercion_technique": "<e.g. Narcotics arrest threat, Power disconnection ultimatum, Contact book leak>",\n'
            '    "malicious_tools": ["<e.g. ElectricityBill.apk, AnyDesk, Skype Video Call>"]\n'
            "  },\n"
            '  "key_facts_timeline": [\n'
            '    "<chronological bullet point of what happened and what caller said>",\n'
            '    "<chronological bullet point of officer action>"\n'
            '  ],\n'
            '  "actionable_investigation_sop": [\n'
            '    "<concrete police investigation step 1, e.g. Freeze recipient UPI ID on NPCI portal>",\n'
            '    "<concrete police investigation step 2, e.g. Register formal FIR under BNS 318(4) / IT Act 66D>",\n'
            '    "<concrete police investigation step 3, e.g. File 1930 / I4C cybercrime portal report>"\n'
            '  ],\n'
            '  "psychological_evaluation": {\n'
            '    "dominant_state": "<e.g. Acute Panic, Severe Distress, Confused, Composed>",\n'
            '    "summary": "<1-2 sentences assessing caller\'s psychological and emotional state>",\n'
            '    "vulnerability_level": "<CRITICAL | HIGH | MEDIUM | LOW>"\n'
            '  },\n'
            '  "triage_verdict": {\n'
            '    "priority_score": <integer 1-100>,\n'
            '    "urgency_badge": <"🚨 VERY VERY URGENT" | "⚠️ URGENT" | "🟡 ELEVATED" | "🟢 ROUTINE">\n'
            '  }\n'
            "}"
        )
        try:
            payload = {
                "model": GROQ_MODEL or "qwen/qwen3.8-27b",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a cyber emergency forensic investigator. Always return strict JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
            res = providers.request(
                "groq", "POST", url,
                headers=headers, json=payload, timeout=max(GROQ_TIMEOUT_SECONDS, 12.0),
            )
            if res is not None and res.status_code == 200:
                parsed = json.loads(res.json()["choices"][0]["message"]["content"])
                return _normalize_forensics(parsed, triage, emotions)
        except Exception as e:
            logger.warning("Groq forensic analysis failed: %s", e)

    # Fallback heuristic analysis if API key is missing or prompt failed
    category = _as_text(triage.get("incident_category"), "CYBER INCIDENT REPORT")
    prio = _as_int(triage.get("triage_priority_score"), 45)
    urgency = _as_text(triage.get("urgency_badge"), "🟡 ELEVATED")

    fallback = {
        "caller_dossier": {
            "name": NOT_DISCLOSED,
            "contact": "Emergency Gateway Line",
            "location": "Local Dispatch Gateway",
            "language_spoken": "Detected via Audio",
        },
        "suspect_profile": {
            "claimed_identity": "Unknown Cyber Actor",
            "impersonated_agency": category,
            "phone_numbers": [],
            "digital_handles": [],
        },
        "financial_impact": {
            "amount_lost": "Unverified",
            "amount_demanded": "Unverified",
            "destination_upi_ids": [],
            "destination_bank_accounts": [],
            "victim_bank": "Unverified",
        },
        "attack_vector": {
            "scam_type": category,
            "coercion_technique": "Social engineering / Threat coercion",
            "malicious_tools": [],
        },
        "key_facts_timeline": [
            f"Emergency call received and triaged under {category}.",
            f"Caller statement: '{clean_transcript[:120]}...'"
            if clean_transcript
            else "Caller voice recorded.",
            f"Officer notes: '{clean_notes[:120]}...'"
            if clean_notes
            else "Session archived.",
        ],
        "actionable_investigation_sop": [
            "Follow standard cybercrime operating procedure.",
            "Verify victim credentials and advise immediate password resets.",
            "Log case reference in state dispatch registry.",
        ],
        "psychological_evaluation": {
            "dominant_state": _as_text(emotions.get("dominant"), "Distressed").capitalize(),
            "summary": (
                f"Caller demonstrated {round(emotions.get('fear', 0.2) * 100)}% acoustic fear "
                f"and {round(emotions.get('distress', 0.2) * 100)}% distress during the call."
            ),
            "vulnerability_level": "HIGH" if prio >= 65 else "MEDIUM",
        },
        "triage_verdict": {
            "priority_score": prio,
            "urgency_badge": urgency,
        },
    }
    return _normalize_forensics(fallback, triage, emotions)
