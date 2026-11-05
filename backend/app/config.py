"""Application configuration loaded from the environment.

Single source of truth: every ``os.getenv`` in the application lives here (and
in ``main.py`` for the log level) so operators have one place to audit.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict

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

OFFICER_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'_-]{0,63}$")
DIALED_NUMBER_RE = re.compile(r"^[0-9*#]{1,16}$")
CALLER_NUMBER_RE = re.compile(r"^[+]?[0-9][0-9 -]{3,19}$")
AUDIO_NAME_RE = re.compile(
    r"^(?:recording|call)_[A-Za-z0-9_-]+\.(?:webm|ogg|wav|m4a|mp4)$"
)
META_NAME_RE = re.compile(r"^(?:recording|call)_[A-Za-z0-9_-]+\.json$")
ALLOWED_AUDIO_EXTENSIONS = {".webm", ".ogg", ".wav", ".m4a", ".mp4"}
HOST_RE = re.compile(r"^[A-Za-z0-9._\-:\[\]]{1,253}$")
DISPOSITIONS = {"", "RESOLVED", "REFERRED", "FIR_FILED", "FALSE_POSITIVE", "DUPLICATE"}

# Values that are published in this repository and must never be treated as a
# real secret without at least a warning.
_PLACEHOLDER_TOKENS = {
    "",
    "change-me",
    "change-me-to-a-long-random-token",
    "replace-me",
    "replace-with-a-long-random-token",
    "your-token",
    "your-token-here",
    "test",
    "secret",
    "password",
    "mayuresh2007",
    "cers-officer-token-2026-auth",
    "cers-2007",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip() == "1"


# ---------------------------------------------------------------------------
# Officer authentication
#
# There is deliberately NO fallback credential. A missing credential is a fatal
# misconfiguration: this service stores recordings of crime victims.
#
# Two supported shapes:
#   1. CERS_OFFICERS="Mayuresh:passA|passB,Rahul:pass2"  -> per-officer accounts;
#      each officer may have several accepted secrets, separated by "|"
#   2. CERS_OFFICER_PASSWORD="..."               -> single shared password
# ---------------------------------------------------------------------------
OFFICER_PASSWORD = os.getenv("CERS_OFFICER_PASSWORD", "").strip()
OFFICER_TOKEN = os.getenv("CERS_OFFICER_TOKEN", "").strip()


def _parse_officers(raw: str) -> Dict[str, tuple[str, ...]]:
    """Parse ``name:secretA|secretB`` entries; invalid names/entries are skipped."""
    officers: Dict[str, tuple[str, ...]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, _, secrets_raw = entry.partition(":")
        name = name.strip()
        secrets = tuple(s.strip() for s in secrets_raw.split("|") if s.strip())
        if name and secrets and OFFICER_ID_RE.fullmatch(name):
            officers[name] = secrets
    return officers


OFFICERS = _parse_officers(os.getenv("CERS_OFFICERS", ""))

SECRET_KEY = os.getenv("CERS_SECRET_KEY", "").strip()
if not SECRET_KEY:
    # Stable for a given credential set, so a dev reload does not invalidate
    # sessions. Never used unless a credential is configured.
    SECRET_KEY = hashlib.sha256(
        "cers-session:"
        f"{OFFICER_PASSWORD}:{OFFICER_TOKEN}:{sorted(OFFICERS.items())}".encode()
    ).hexdigest()

COOKIE_SECURE = _env_bool("CERS_COOKIE_SECURE", False)

# ---------------------------------------------------------------------------
# HTTP / operational limits
# ---------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CERS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
MAX_RECORDING_BYTES = _env_int("CERS_MAX_RECORDING_BYTES", 25 * 1024 * 1024)
MAX_TRIAGE_BYTES = _env_int("CERS_MAX_TRIAGE_BYTES", 5 * 1024 * 1024)
MAX_ACTIVE_CALLS = _env_int("CERS_MAX_ACTIVE_CALLS", 500)
CALL_TTL_SECONDS = _env_int("CERS_CALL_TTL_SECONDS", 3600)
CLEANUP_INTERVAL_SECONDS = _env_int("CERS_CLEANUP_INTERVAL_SECONDS", 300)
# How long a caller socket may stay disconnected before the session is ended.
CALLER_DISCONNECT_GRACE_SECONDS = _env_float(
    "CERS_CALLER_DISCONNECT_GRACE_SECONDS", 15.0
)
# Archived recordings older than this are purged. 0 disables retention.
RECORDING_RETENTION_DAYS = _env_int("CERS_RECORDING_RETENTION_DAYS", 0)
# Refuse to share one process with another worker unless explicitly allowed:
# call state is in-memory and atomic claiming is only correct in one process.
ALLOW_MULTI_WORKER = _env_bool("CERS_ALLOW_MULTI_WORKER", False)
WORKER_LOCK_PATH = Path(
    os.getenv("CERS_WORKER_LOCK", str(BASE_DIR / ".cers.worker.lock"))
)

# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
ESCALATION_PRIORITY = _env_int("CERS_ESCALATION_PRIORITY", 85)
ESCALATION_AFTER_SECONDS = _env_float("CERS_ESCALATION_AFTER_SECONDS", 30.0)
ESCALATION_SWEEP_SECONDS = _env_float("CERS_ESCALATION_SWEEP_SECONDS", 5.0)

# ---------------------------------------------------------------------------
# WebRTC / TURN
# ---------------------------------------------------------------------------
STUN_URLS = [
    url.strip()
    for url in os.getenv(
        "CERS_STUN_URLS", "stun:stun.l.google.com:19302,stun:stun1.l.google.com:19302"
    ).split(",")
    if url.strip()
]
TURN_URLS = [
    url.strip()
    for url in os.getenv("CERS_TURN_URL", "").split(",")
    if url.strip()
]
TURN_USERNAME = os.getenv("CERS_TURN_USERNAME", "").strip()
TURN_CREDENTIAL = os.getenv("CERS_TURN_CREDENTIAL", "").strip()

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
# AI providers
# ---------------------------------------------------------------------------
# Force the deterministic pipeline (no network) for demos, CI and outages.
AI_OFFLINE = _env_bool("CERS_AI_OFFLINE", False)

HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "").strip()
HF_SPACE_ID = os.getenv("HF_SPACE_ID", "mindfoldz0/cers-voice-emotion").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
GROQ_TIMEOUT_SECONDS = _env_float("CERS_GROQ_TIMEOUT_SECONDS", 8.0)

SARVAM_API_KEY = (
    os.getenv("sarvam_api", "").strip()
    or os.getenv("SARVAM_API", "").strip()
    or os.getenv("sarvam_api2", "").strip()
    or os.getenv("SARVAM_API_2", "").strip()
    or os.getenv("SARVAM_API_KEY", "").strip()
)
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "saaras:v4").strip()
SARVAM_LANGUAGE_CODE = os.getenv("SARVAM_LANGUAGE_CODE", "unknown").strip()
SARVAM_TIMEOUT_SECONDS = _env_float("CERS_SARVAM_TIMEOUT_SECONDS", 15.0)
SARVAM_LANGUAGES = {"unknown", "en-IN", "hi-IN", "mr-IN", "bn-IN", "ta-IN", "te-IN"}

AWS_EMOTION_URL = os.getenv("AWS_EMOTION_URL", "").strip()
CERS_SER_API_KEY = os.getenv("CERS_SER_API_KEY", "").strip()
AWS_EMOTION_TIMEOUT_SECONDS = _env_float("CERS_AWS_EMOTION_TIMEOUT_SECONDS", 4.0)

# Retry / circuit-breaker tuning for every outbound provider call.
PROVIDER_ATTEMPTS = _env_int("CERS_PROVIDER_ATTEMPTS", 2)
PROVIDER_BACKOFF_SECONDS = _env_float("CERS_PROVIDER_BACKOFF_SECONDS", 0.4)
PROVIDER_BREAKER_THRESHOLD = _env_int("CERS_PROVIDER_BREAKER_THRESHOLD", 5)
PROVIDER_BREAKER_COOLDOWN = _env_float("CERS_PROVIDER_BREAKER_COOLDOWN", 60.0)


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _PLACEHOLDER_TOKENS:
        return True
    # Covers "your-secure-token", "your_token_here", "change-me-please", ...
    return (
        lowered.startswith(
            (
                "your",
                "changeme",
                "change-me",
                "replace-me",
                "replace-with",
                "placeholder",
                "generate-a",
                "example",
            )
        )
        or "change-me" in lowered
        or "replace-me" in lowered
        or "replace-with" in lowered
        or "generate-a-long-random" in lowered
        or "example" in lowered
    )


def validate_officer_credentials() -> None:
    """Require *a* configured credential, but let the operator choose it.

    Deliberately only one rule: a credential must be set somewhere. A shipped
    default in source code is the actual vulnerability — an operator choosing a
    short password in their own ``.env`` is a deployment decision, so that is
    allowed (and merely warned about when it matches a published value).
    """
    configured = [
        *(secret for secrets in OFFICERS.values() for secret in secrets),
        *(value for value in (OFFICER_PASSWORD, OFFICER_TOKEN) if value),
    ]
    if not configured:
        raise RuntimeError(
            "Refusing to start: set CERS_OFFICER_PASSWORD (or CERS_OFFICERS) "
            "in .env. There is no built-in default officer password."
        )
    if all(_is_placeholder(value) for value in configured):
        if _env_bool("CERS_ALLOW_PUBLISHED_CREDENTIALS", False):
            logging.getLogger("cers.config").warning(
                "Officer credentials match published sample values; this is only "
                "tolerated because CERS_ALLOW_PUBLISHED_CREDENTIALS=1."
            )
            return
        raise RuntimeError(
            "Refusing to start: the configured officer credential matches a "
            "published sample/default value. Set your own CERS_OFFICER_PASSWORD "
            "in .env, or set CERS_ALLOW_PUBLISHED_CREDENTIALS=1 to knowingly run "
            "with a demo credential."
        )


validate_officer_credentials()
