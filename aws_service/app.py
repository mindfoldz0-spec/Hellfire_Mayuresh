"""Dedicated Speech Emotion Recognition (SER) microservice.

Runs on AWS EC2 with PyTorch and Hugging Face Transformers (Wav2Vec2 XLS-R),
plus an acoustic-prosody analysis that calibrates the neural probabilities.

Operational notes (see IMPROVEMENTS.md §3.2-3.4):

* The model loads **in the background** after the server starts. A transient
  Hugging Face outage no longer prevents the service from booting; ``/health``
  reports ``ready: false`` until the weights are in memory and ``/predict``
  answers 503 meanwhile.
* Inference runs in a threadpool. It used to run inside ``async def``, which
  serialised every concurrent request behind PyTorch on the event loop.
* There is deliberately **no CORS middleware**: this is a server-to-server API
  called by the dispatch gateway, not by browsers. Restrict it with a security
  group instead of an origin allowlist.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import logging
import math
import os
import struct
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

MODEL_NAME = os.getenv(
    "SER_MODEL", "ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition"
)
MAX_UPLOAD_BYTES = int(os.getenv("SER_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
LOAD_ATTEMPTS = int(os.getenv("SER_LOAD_ATTEMPTS", "4"))
SER_API_KEY = os.getenv("SER_API_KEY", "").strip()
device = torch.device("cpu")

logger = logging.getLogger("cers.ser")


def _authorize(authorization: str, ser_key_header: str) -> None:
    """Require a shared bearer token unless the operator explicitly runs open.

    This is a server-to-server endpoint on a public EC2 nic. Relying on a
    security group alone is not enough for a service that receives audio of
    crime victims.
    """
    if not SER_API_KEY:
        logger.warning("SER_API_KEY is not set; accepting unauthenticated /predict requests.")
        return
    candidate = ""
    if authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
    elif ser_key_header.strip():
        candidate = ser_key_header.strip()
    if not candidate or not hmac.compare_digest(candidate.encode(), SER_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="SER service authentication required.")

_state: Dict[str, Any] = {
    "ready": False,
    "error": "",
    "loaded_at": 0.0,
    "load_seconds": 0.0,
    "feature_extractor": None,
    "model": None,
}


def _load_model() -> None:
    """Fetch the weights with retries; record failure instead of crashing."""
    started = time.time()
    last_error = ""
    for attempt in range(max(1, LOAD_ATTEMPTS)):
        try:
            print(f"[Init] Loading SER model: {MODEL_NAME} (attempt {attempt + 1})")
            extractor = AutoFeatureExtractor.from_pretrained(MODEL_NAME)
            model = AutoModelForAudioClassification.from_pretrained(MODEL_NAME)
            model.to(device)
            model.eval()
            _state.update(
                ready=True,
                error="",
                feature_extractor=extractor,
                model=model,
                loaded_at=time.time(),
                load_seconds=round(time.time() - started, 2),
            )
            print("[Init] Model successfully loaded into memory!")
            return
        except Exception as exc:  # noqa: BLE001 - any HF/network failure
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"[Init] Load attempt {attempt + 1} failed: {last_error}")
            time.sleep(min(2**attempt, 8))
    _state.update(ready=False, error=last_error)
    print(f"[Init] Model unavailable after retries: {last_error}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(asyncio.to_thread(_load_model), name="ser-model-load")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(
    title="CERS Dedicated Speech Emotion Recognition API",
    version="2.1",
    lifespan=lifespan,
)


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


def _riff_info(audio_bytes: bytes):
    """Parse a RIFF/WAVE header and return (audio_format, channels, bits).

    Returns ``None`` when the payload is not a playable PCM WAV. Mirrors the
    server-side check in the dispatch gateway so an accidentally forwarded
    non-WAV file fails here too instead of inside the model.
    """
    if len(audio_bytes) < 44:
        return None
    try:
        if audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
            return None
        offset = 12
        total = len(audio_bytes)
        fmt = None
        data_seen = False
        while offset + 8 <= total:
            chunk_id = audio_bytes[offset : offset + 4]
            chunk_size = struct.unpack_from("<I", audio_bytes, offset + 4)[0]
            payload = offset + 8
            end = payload + chunk_size
            if end > total:
                return None
            if chunk_id == b"fmt ":
                fmt = audio_bytes[payload:end]
            elif chunk_id == b"data":
                data_seen = True
            offset = end + (chunk_size % 2)
        if not fmt or len(fmt) < 16 or not data_seen:
            return None

        audio_format, channels, _rate, _byte_rate, _block_align, bits = (
            struct.unpack_from("<HHIIHH", fmt, 0)
        )
        if audio_format == 0xFFFE:
            if len(fmt) < 26:
                return None
            audio_format = struct.unpack_from("<H", fmt, 24)[0]
        return audio_format, channels, bits
    except (struct.error, IndexError):
        return None


def predict_emotion_from_wav(audio_bytes: bytes) -> Dict[str, Any]:
    t0 = time.time()
    feature_extractor = _state["feature_extractor"]
    model = _state["model"]
    if feature_extractor is None or model is None:
        raise RuntimeError("SER model is not loaded yet.")

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

        divisor = math.gcd(16000, int(sr))
        audio = scipy.signal.resample_poly(audio, 16000 // divisor, int(sr) // divisor)
        sr = 16000

    prosody = extract_prosody(audio, sr)

    # Neural inference with Wav2Vec2 XLS-R
    inputs = feature_extractor(
        audio, sampling_rate=16000, return_tensors="pt", padding=True
    )
    with torch.no_grad():
        logits = model(**inputs).logits
        probs_tensor = F.softmax(logits, dim=-1).squeeze()
        probs = probs_tensor.tolist() if probs_tensor.ndim else [probs_tensor.item()]

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

    dominant = max(emotions_final.items(), key=lambda kv: kv[1])[0]

    vocal_distress_score = min(
        1.0,
        round(fear * 0.40 + distress * 0.35 + anger * 0.15 + nervousness * 0.10, 3),
    )

    return {
        "status": "success",
        "dominant_emotion": dominant,
        "vocal_distress_score": vocal_distress_score,
        "emotions": emotions_final,
        "raw_probabilities": raw_map,
        "prosody": prosody,
        "latency_ms": round((time.time() - t0) * 1000, 1),
    }


@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy" if _state["ready"] else "degraded",
        "service": "CERS Speech Emotion Recognition",
        "model": MODEL_NAME,
        "device": str(device),
        "ready": bool(_state["ready"]),
        "error": _state["error"],
        "load_seconds": _state["load_seconds"],
    }


@app.post("/predict")
async def predict_emotion(
    request: Request, file: UploadFile = File(...)
) -> Dict[str, Any]:
    if not _state["ready"]:
        raise HTTPException(
            status_code=503, detail="SER model is still loading; retry shortly."
        )
    _authorize(
        request.headers.get("authorization", ""),
        request.headers.get("x-ser-key", ""),
    )
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file")

    audio_bytes = await file.read()
    if len(audio_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Empty audio payload")
    riff = _riff_info(audio_bytes)
    if riff is None:
        raise HTTPException(
            status_code=415, detail="Only PCM mono/stereo WAV files are supported."
        )
    audio_format, channels, bits = riff
    if audio_format != 1 or channels not in (1, 2) or bits not in (16, 24, 32):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported WAV encoding (format={audio_format}, channels={channels}, bits={bits}).",
        )

    try:
        # PyTorch is CPU-bound: never run it on the event loop.
        return await run_in_threadpool(predict_emotion_from_wav, audio_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
