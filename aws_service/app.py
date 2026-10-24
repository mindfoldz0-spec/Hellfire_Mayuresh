"""Dedicated Speech Emotion Recognition (SER) Microservice.
Runs on AWS EC2 with PyTorch, Hugging Face Transformers (Wav2Vec2 XLS-R), and Acoustic Prosody Analysis.
"""

from __future__ import annotations

import io
import math
import os
import struct
import tempfile
import time
from typing import Any, Dict

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

app = FastAPI(title="CERS Dedicated Speech Emotion Recognition API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME = os.getenv(
    "SER_MODEL", "ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition"
)
device = torch.device("cpu")

print(f"[Init] Loading SER model: {MODEL_NAME}...")
feature_extractor = AutoFeatureExtractor.from_pretrained(MODEL_NAME)
model = AutoModelForAudioClassification.from_pretrained(MODEL_NAME)
model.to(device)
model.eval()
print("[Init] Model successfully loaded into memory!")


def extract_prosody(audio: np.ndarray, sr: int = 16000) -> Dict[str, float]:
    """Extract physical acoustic prosody features from raw PCM waveform."""
    if len(audio) == 0:
        return {"rms": 0.05, "zcr": 0.05, "loudness": 1.0, "tremor": 0.0}

    # RMS Energy
    rms = float(np.sqrt(np.mean(audio**2)))
    # Zero Crossing Rate (tremor / breathiness)
    zc = np.sum(np.abs(np.diff(np.sign(audio)))) / (2.0 * len(audio))
    # Loudness factor (0.5 to 2.0)
    loudness = float(min(2.0, max(0.5, rms / 0.06)))
    # Energy variance (sudden outbursts vs monotone)
    chunk_size = sr // 10  # 100ms chunks
    if len(audio) > chunk_size:
        chunks = [
            np.sqrt(np.mean(audio[i : i + chunk_size] ** 2))
            for i in range(0, len(audio) - chunk_size, chunk_size)
        ]
        tremor = float(np.std(chunks))
    else:
        tremor = 0.0

    return {
        "rms": round(rms, 4),
        "zcr": round(float(zc), 4),
        "loudness": round(loudness, 2),
        "tremor": round(tremor, 4),
    }


def predict_emotion_from_wav(audio_bytes: bytes) -> Dict[str, Any]:
    t0 = time.time()
    try:
        # Read audio into numpy array via soundfile
        audio, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    except Exception:
        # Fallback using tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp_name = f.name
        try:
            audio, sr = sf.read(tmp_name, dtype="float32")
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    # Convert to mono if stereo
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    # Resample if not 16000 Hz
    if sr != 16000:
        import scipy.signal

        num_samples = int(len(audio) * 16000 / sr)
        audio = scipy.signal.resample(audio, num_samples)
        sr = 16000

    prosody = extract_prosody(audio, sr)

    # Neural inference with Wav2Vec2 XLS-R
    inputs = feature_extractor(
        audio, sampling_rate=16000, return_tensors="pt", padding=True
    )
    with torch.no_grad():
        logits = model(**inputs).logits
        probs = F.softmax(logits, dim=-1).squeeze().tolist()

    id2label = model.config.id2label
    raw_map: Dict[str, float] = {
        id2label[i].lower(): round(float(probs[i]), 4) for i in range(len(probs))
    }

    # Calibrate with physical prosody:
    # High loudness & tremor significantly boost fear, distress, anger over flat neutral
    raw_fear = raw_map.get("fearful", raw_map.get("fear", 0.05))
    raw_anger = raw_map.get("angry", raw_map.get("anger", 0.05))
    raw_sad = raw_map.get("sad", 0.05)
    raw_surprised = raw_map.get("surprised", 0.05)
    raw_neutral = raw_map.get("neutral", raw_map.get("calm", 0.50))

    loudness = prosody["loudness"]
    tremor = prosody["tremor"]

    # In emergency speech, vocal tremor and elevated volume indicate true panic
    fear = min(0.98, max(raw_fear, raw_fear * 0.6 + raw_surprised * 0.4 + tremor * 2.0))
    distress = min(0.98, max(raw_sad, raw_sad * 0.5 + raw_fear * 0.5 + tremor * 1.5))
    anger = min(0.95, max(raw_anger, raw_anger * loudness))
    nervousness = min(0.95, max(0.10, raw_surprised * 0.5 + tremor * 2.5))
    
    # Dampen neutral if fear/distress/anger are strong
    max_distress = max(fear, distress, anger)
    if max_distress > 0.40:
        neutral = max(0.02, round(raw_neutral * (1.0 - max_distress), 3))
    else:
        neutral = round(raw_neutral, 3)

    emotions_final = {
        "fear": round(float(fear), 3),
        "distress": round(float(distress), 3),
        "anger": round(float(anger), 3),
        "nervousness": round(float(nervousness), 3),
        "neutral": round(float(neutral), 3),
    }

    # Dominant emotion
    dominant = max(emotions_final.items(), key=lambda kv: kv[1])[0]

    # Overall vocal distress score (0.0 to 1.0)
    vocal_distress_score = min(
        1.0,
        round(fear * 0.40 + distress * 0.35 + anger * 0.15 + nervousness * 0.10, 3),
    )

    elapsed_ms = round((time.time() - t0) * 1000, 1)

    return {
        "status": "success",
        "dominant_emotion": dominant,
        "vocal_distress_score": vocal_distress_score,
        "emotions": emotions_final,
        "raw_probabilities": raw_map,
        "prosody": prosody,
        "latency_ms": elapsed_ms,
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "CERS Speech Emotion Recognition",
        "model": MODEL_NAME,
        "device": str(device),
    }


@app.post("/predict")
async def predict_emotion(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file")
    audio_bytes = await file.read()
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Empty audio payload")
    try:
        result = predict_emotion_from_wav(audio_bytes)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
