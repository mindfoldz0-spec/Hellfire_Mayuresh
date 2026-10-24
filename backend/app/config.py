"""Application configuration loaded from the environment."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
RECORDINGS_DIR = BASE_DIR / "backend" / "recordings"

load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COOKIE_NAME = "cers_officer"
SESSION_TTL_SECONDS = 8 * 60 * 60

OFFICER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DIALED_NUMBER_RE = re.compile(r"^[0-9*#]{1,16}$")
AUDIO_NAME_RE = re.compile(
    r"^(?:recording|call)_[A-Za-z0-9_-]+\.(?:webm|ogg|wav|m4a|mp4)$"
)
META_NAME_RE = re.compile(r"^(?:recording|call)_[A-Za-z0-9_-]+\.json$")
ALLOWED_AUDIO_EXTENSIONS = {".webm", ".ogg", ".wav", ".m4a", ".mp4"}

_PLACEHOLDER_TOKENS = {
    "change-me",
    "change-me-to-a-long-random-token",
    "replace-me",
    "replace-with-a-long-random-token",
    "your-token",
    "your-token-here",
    "test",
    "secret",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Officer authentication
# ---------------------------------------------------------------------------
OFFICER_PASSWORD = os.getenv("CERS_OFFICER_PASSWORD", "mayuresh2007").strip()
OFFICER_TOKEN = os.getenv("CERS_OFFICER_TOKEN", "").strip() or OFFICER_PASSWORD

SECRET_KEY = os.getenv("CERS_SECRET_KEY", "").strip()
if not SECRET_KEY:
    # Stable for a given officer password/token, so a dev reload does not invalidate sessions
    SECRET_KEY = hashlib.sha256(f"cers-session:{OFFICER_PASSWORD}".encode()).hexdigest()

COOKIE_SECURE = os.getenv("CERS_COOKIE_SECURE", "0").strip() == "1"

# ---------------------------------------------------------------------------
# HTTP / operational limits
# ---------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CERS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
MAX_RECORDING_BYTES = _env_int("CERS_MAX_RECORDING_BYTES", 25 * 1024 * 1024)
MAX_ACTIVE_CALLS = _env_int("CERS_MAX_ACTIVE_CALLS", 500)
CALL_TTL_SECONDS = _env_int("CERS_CALL_TTL_SECONDS", 3600)
CLEANUP_INTERVAL_SECONDS = _env_int("CERS_CLEANUP_INTERVAL_SECONDS", 300)

# ---------------------------------------------------------------------------
# Cloud Storage (Supabase)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = (
    os.getenv("SUPABASE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "recordings").strip()

# ---------------------------------------------------------------------------
# Hugging Face Speech Emotion Recognition
# ---------------------------------------------------------------------------
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "").strip()
HUGGINGFACE_MODEL = os.getenv(
    "HUGGINGFACE_MODEL", "ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition"
).strip()
HF_SPACE_ID = os.getenv("HF_SPACE_ID", "mindfoldz0/cers-voice-emotion").strip()

# ---------------------------------------------------------------------------
# Groq Qwen Semantic Triage
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()

# ---------------------------------------------------------------------------
# Sarvam AI Speech-to-Text Model
# ---------------------------------------------------------------------------
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "saaras:v4").strip()

# ---------------------------------------------------------------------------
# AWS Dedicated Speech Emotion Recognition
# ---------------------------------------------------------------------------
AWS_EMOTION_URL = os.getenv(
    "AWS_EMOTION_URL", "http://65.2.34.240:8000/predict"
).strip()

