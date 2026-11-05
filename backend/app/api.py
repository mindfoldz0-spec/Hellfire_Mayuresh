"""Officer authentication and caller-facing HTTP APIs."""

from __future__ import annotations

import secrets
from typing import Any, Dict, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)

from backend.app import providers, triage
from backend.app.config import (
    AI_OFFLINE,
    CALLER_NUMBER_RE,
    COOKIE_NAME,
    COOKIE_SECURE,
    DIALED_NUMBER_RE,
    MAX_ACTIVE_CALLS,
    MAX_TRIAGE_BYTES,
    OFFICER_ID_RE,
    OFFICER_PASSWORD,
    OFFICER_TOKEN,
    OFFICERS,
    SARVAM_LANGUAGES,
    SESSION_TTL_SECONDS,
    STUN_URLS,
    TURN_CREDENTIAL,
    TURN_URLS,
    TURN_USERNAME,
)
from backend.app.metrics import metrics
from backend.app.models import CreateCallRequest, LoginRequest
from backend.app.security import (
    call_creation_limiter,
    client_ip,
    create_call_token,
    create_session_token,
    login_identity_limiter,
    login_limiter,
    require_officer,
    triage_limiter,
    verify_call_token,
)
from backend.app.state import CapacityError, call_manager

router = APIRouter()


def _constant_time_equals(candidate: str, expected: str) -> bool:
    """Compare secrets as bytes.

    ``secrets.compare_digest`` raises TypeError for non-ASCII ``str`` input,
    which used to turn a non-ASCII password into an HTTP 500 instead of a 401.
    """
    if not expected:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


async def _read_upload_capped(file: UploadFile, limit: int) -> bytes:
    """Stream an upload into memory with a hard byte ceiling."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Audio exceeds the {limit // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@router.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    if not await login_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Too many login attempts. Try again shortly."
        )
    officer_id = payload.officer_id.strip()
    if not OFFICER_ID_RE.fullmatch(officer_id):
        raise HTTPException(status_code=400, detail="Invalid officer ID.")
    if not await login_identity_limiter.allow(officer_id):
        raise HTTPException(
            status_code=429,
            detail="This station operator is temporarily locked out. Try again later.",
        )

    supplied = payload.token
    if OFFICERS:
        # Per-officer accounts configured: only listed names may sign in, so the
        # officer_id recorded against a call is actually verified. An officer
        # may have several accepted secrets ("Name:passA|passB").
        accepted = OFFICERS.get(officer_id, ())
        authenticated = any(
            _constant_time_equals(supplied, secret) for secret in accepted
        )
    else:
        authenticated = _constant_time_equals(
            supplied, OFFICER_PASSWORD
        ) or _constant_time_equals(supplied, OFFICER_TOKEN)
    if not authenticated:
        raise HTTPException(status_code=401, detail="Invalid officer password.")

    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(officer_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    return {"officer_id": officer_id, "expires_in": SESSION_TTL_SECONDS}


@router.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/api/auth/me")
async def me(officer_id: str = Depends(require_officer)):
    return {"officer_id": officer_id}


@router.get("/api/auth/officers")
async def list_officer_ids():
    """Officer IDs only — never secrets — so the login page can offer a picker.

    Empty in shared-password mode, where any name is allowed and the login
    page keeps its free-text input. Names are not secrets (they appear in the
    queue and on recordings); the per-identity login rate limit still applies.
    """
    return {"officers": sorted(OFFICERS)}


# ---------------------------------------------------------------------------
# Caller sessions and WebRTC configuration
# ---------------------------------------------------------------------------
@router.post("/api/calls")
async def create_call(payload: CreateCallRequest, request: Request):
    dialed_number = payload.dialed_number.strip()
    if not DIALED_NUMBER_RE.fullmatch(dialed_number):
        raise HTTPException(status_code=400, detail="Invalid dialed number.")
    caller_number = (payload.caller_number or "").strip()
    if caller_number and not CALLER_NUMBER_RE.fullmatch(caller_number):
        raise HTTPException(status_code=400, detail="Invalid caller number.")
    if not await call_creation_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Too many call attempts. Try again shortly."
        )
    try:
        call = await call_manager.create_call(
            dialed_number, max_active=MAX_ACTIVE_CALLS, caller_number=caller_number
        )
    except CapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # The caller keeps this capability token private: it is what proves
    # ownership of the session when attaching the socket or uploading audio.
    return {
        "call_id": call.call_id,
        "dialed_number": call.dialed_number,
        "call_token": create_call_token(call.call_id),
    }


@router.get("/api/rtc-config")
async def rtc_config() -> Dict[str, Any]:
    ice_servers: list[Dict[str, Any]] = [
        {"urls": url} for url in STUN_URLS
    ] or [{"urls": "stun:stun.l.google.com:19302"}]
    if TURN_URLS:
        ice_servers.append(
            {
                "urls": TURN_URLS,
                "username": TURN_USERNAME,
                "credential": TURN_CREDENTIAL,
            }
        )
    return {"iceServers": ice_servers}


@router.post("/api/calls/{call_id}/triage")
async def upload_triage_audio(
    call_id: str,
    request: Request,
    file: UploadFile = File(...),
    call_token: Optional[str] = Header(default=None, alias="X-Call-Token"),
):
    if not verify_call_token(call_id, call_token):
        raise HTTPException(status_code=403, detail="Invalid call token.")
    if not await triage_limiter.allow(f"call:{call_id}"):
        raise HTTPException(
            status_code=429, detail="Too many triage uploads for this call."
        )
    if not await triage_limiter.allow(f"ip:{client_ip(request)}"):
        raise HTTPException(status_code=429, detail="Too many triage uploads.")

    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call session not found.")
    if call.status == "ENDED":
        raise HTTPException(status_code=409, detail="Call session has already ended.")

    audio_bytes = await _read_upload_capped(file, MAX_TRIAGE_BYTES)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio recording.")
    if not triage.looks_like_wav(audio_bytes):
        # The AI pipeline's acoustic analysis decodes 16 kHz mono PCM WAV.
        # Rejecting anything else is better than silently scoring defaults.
        raise HTTPException(
            status_code=415,
            detail="Triage audio must be a 16-bit PCM WAV file.",
        )

    accepted, detail = await triage.schedule_triage(
        call_id,
        audio_bytes,
        file.content_type or "audio/wav",
        language=payload_language(request),
    )
    if not accepted:
        raise HTTPException(status_code=409, detail=detail)
    return {"status": "triaging", "call_id": call_id, "ai_offline": AI_OFFLINE}


def payload_language(request: Request) -> str:
    """Per-call ASR language chosen at the IVR (validated against the allowlist)."""
    value = (request.query_params.get("lang") or "").strip()
    return value if value in SARVAM_LANGUAGES else ""


@router.post("/api/calls/{call_id}/statement")
async def upload_text_statement(
    call_id: str,
    request: Request,
    call_token: Optional[str] = Header(default=None, alias="X-Call-Token"),
):
    """Fallback for callers whose microphone is unavailable.

    Accepts the statement either as a plain form field (``text``, or ``file``
    for the dialer's FormData shape) or as an uploaded text part.
    """
    if not verify_call_token(call_id, call_token):
        raise HTTPException(status_code=403, detail="Invalid call token.")
    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call session not found.")
    if call.status == "ENDED":
        raise HTTPException(status_code=409, detail="Call session has already ended.")

    form = await request.form()
    raw = form.get("text") or form.get("file") or ""
    if hasattr(raw, "read"):
        raw = (await raw.read(4000)).decode("utf-8", "replace")
    text = str(raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty statement.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Statement is too long.")
    if not await triage_limiter.allow(f"text:{call_id}"):
        raise HTTPException(status_code=429, detail="Too many statement uploads.")

    accepted, detail = await triage.schedule_text_triage(call_id, text)
    if not accepted:
        raise HTTPException(status_code=409, detail=detail)
    return {"status": "triaging", "call_id": call_id, "channel": "text"}


@router.post("/api/calls/{call_id}/triage/window")
async def upload_triage_window(
    call_id: str,
    request: Request,
    file: UploadFile = File(...),
    window: int = Form(0),
    officer_id: str = Depends(require_officer),
):
    """Live in-call re-triage of a short audio slice (officer-authenticated)."""
    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call session not found.")
    if call.status == "ENDED":
        raise HTTPException(status_code=409, detail="Call session has already ended.")
    if call.officer_id != officer_id:
        raise HTTPException(status_code=403, detail="You are not assigned to this call.")
    if not await triage_limiter.allow(f"window:{call_id}"):
        raise HTTPException(status_code=429, detail="Too many live triage windows.")

    audio_bytes = await _read_upload_capped(file, MAX_TRIAGE_BYTES)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio window.")
    if not triage.looks_like_wav(audio_bytes):
        raise HTTPException(
            status_code=415, detail="Triage audio must be a 16-bit PCM WAV file."
        )

    accepted, detail = await triage.schedule_triage(
        call_id,
        audio_bytes,
        "audio/wav",
        window=max(1, int(window)),
        language=payload_language(request),
    )
    if not accepted:
        raise HTTPException(status_code=409, detail=detail)
    return {"status": "triaging", "call_id": call_id, "window": max(1, int(window))}


# ---------------------------------------------------------------------------
# Operational metrics
# ---------------------------------------------------------------------------
@router.get("/api/metrics")
async def get_metrics(_: str = Depends(require_officer)) -> Dict[str, Any]:
    snapshot = metrics.snapshot()
    snapshot["providers"] = providers.registry.snapshot()
    snapshot["ai_offline"] = AI_OFFLINE
    snapshot["active_calls"] = call_manager.active_call_count()
    snapshot["escalated"] = sum(
        1 for call in call_manager.calls.values() if call.escalated_at
    )
    return snapshot
