"""Generate multiple realistic emergency call audio samples with distinct emotional profiles,
and test them against the SUPERB Speech Emotion Recognition model and triage pipeline.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

# Force UTF-8 console output for Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from backend.app.ai import (
    classify_incident_semantics,
    extract_acoustic_features,
    query_huggingface_emotion,
    triage_call_audio,
)

OUTPUT_DIR = BASE_DIR / "test_audio_emotions"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLES = [
    {
        "id": "distress_panic",
        "title": "Severe Distress & Panic (Extortion / Sextortion)",
        "text": "Please help me! Someone is blackmailing me with morphed photos and threatening to ruin my life! I am so terrified, please help me right now!",
        "speaker": "priya",
        "pitch": 0.4,
        "pace": 1.15,
        "loudness": 1.5,
        "expected": "distress / sad",
    },
    {
        "id": "anger_furious",
        "title": "Furious & Aggressive (Wire Fraud / Bank Drained)",
        "text": "This is totally unacceptable! A fake CBI caller stole all my savings and your helpline was engaged for twenty minutes! Catch these thieves right now and return my money!",
        "speaker": "rahul",
        "pitch": -0.2,
        "pace": 1.1,
        "loudness": 2.0,
        "expected": "angry",
    },
    {
        "id": "calm_neutral",
        "title": "Calm & Neutral (General Inquiry / Routine Report)",
        "text": "Good morning officer. I am calling to inquire about the standard procedure for reporting an unsolicited promotional message received on my mobile device. Thank you.",
        "speaker": "neha",
        "pitch": 0.0,
        "pace": 0.95,
        "loudness": 1.0,
        "expected": "neutral",
    },
    {
        "id": "relief_happy",
        "title": "Relieved & Grateful (Transaction Blocked / Life Saved)",
        "text": "Oh thank God! Officer, thank you so much! The fraudulent bank transfer was blocked just in time! You and your team are wonderful, thank you so much!",
        "speaker": "pooja",
        "pitch": 0.2,
        "pace": 1.05,
        "loudness": 1.2,
        "expected": "happy",
    },
    {
        "id": "life_threat",
        "title": "Critical Life Threat (Emergency Bleeding / Accident)",
        "text": "Emergency! There is a terrible accident on the highway! Two people are severely injured and bleeding unconscious! Send an ambulance immediately!",
        "speaker": "ashutosh",
        "pitch": 0.4,
        "pace": 1.2,
        "loudness": 2.0,
        "expected": "distress / sad",
    },
]


def synthesize_sample(sample: dict, api_key: str) -> Path | None:
    output_path = OUTPUT_DIR / f"{sample['id']}.wav"
    if output_path.exists() and output_path.stat().st_size > 1000:
        print(f"  [Cached] {output_path.name} already exists.")
        return output_path

    url = "https://api.sarvam.ai/text-to-speech"
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    payload = {
        "inputs": [sample["text"]],
        "target_language_code": "en-IN",
        "speaker": sample["speaker"],
        "pitch": sample["pitch"],
        "pace": sample["pace"],
        "loudness": sample["loudness"],
        "speech_sample_rate": 16000,
        "model": "bulbul:v3",
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=25)
        if res.status_code == 200:
            audios = res.json().get("audios", [])
            if audios:
                raw_wav = base64.b64decode(audios[0])
                output_path.write_bytes(raw_wav)
                print(f"  [Generated] {output_path.name} ({len(raw_wav)} bytes, 16kHz WAV)")
                return output_path
        print(f"  [Failed] Sarvam status {res.status_code}: {res.text}")
    except Exception as exc:
        print(f"  [Exception] Sarvam TTS error: {exc}")
    return None


def main():
    api_key = (os.getenv("sarvam_api") or "").strip()
    if api_key.endswith("open"):
        api_key = api_key[:-4]

    print("=" * 80)
    print(" CERS MULTI-EMOTION BENCHMARK AGAINST SUPERB SPEECH EMOTION MODEL")
    print(f" Model Space: {os.getenv('HF_SPACE_ID', 'mindfoldz0/cers-voice-emotion')}")
    print(" Target Audio Format: 16000 Hz, 16-bit PCM Mono WAV")
    print("=" * 80)

    results = []

    for idx, sample in enumerate(SAMPLES, start=1):
        print(f"\n[{idx}/{len(SAMPLES)}] Processing: {sample['title']}")
        print(f"  Speech script: \"{sample['text']}\"")
        wav_path = synthesize_sample(sample, api_key)
        if not wav_path or not wav_path.exists():
            print("  Skipping test due to generation failure.")
            continue

        # 1. Direct Hugging Face ZeroGPU Emotion Inference
        print("  Querying Hugging Face ZeroGPU Speech Emotion Recognition Space...")
        t0 = time.time()
        hf_res = query_huggingface_emotion(str(wav_path))
        elapsed = round(time.time() - t0, 2)

        # 2. Complete Multimodal Triage Pipeline
        with open(wav_path, "rb") as f:
            triage = triage_call_audio(f.read(), mime_type="audio/wav")

        print(f"  HF Raw Result ({elapsed}s): Label = {hf_res.get('label')} | Confidences = {hf_res.get('map')}")
        print(f"  CERS Triage Output: Score = {triage['triage_priority_score']}/100 | Badge = {triage['urgency_badge']} | Category = {triage['incident_category']}")

        results.append(
            {
                "id": sample["id"],
                "title": sample["title"],
                "file": wav_path.name,
                "hf_label": hf_res.get("label", "neutral"),
                "hf_map": hf_res.get("map", {}),
                "triage_score": triage["triage_priority_score"],
                "urgency_badge": triage["urgency_badge"],
                "category": triage["incident_category"],
                "latency_sec": elapsed,
            }
        )

    # Save summary report
    summary_path = OUTPUT_DIR / "benchmark_results.json"
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n" + "=" * 80)
    print(f"BENCHMARK COMPLETE. Results saved to {summary_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
