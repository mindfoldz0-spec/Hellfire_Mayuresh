"""Officer authentication and caller-facing HTTP APIs."""

from __future__ import annotations

import asyncio
import os
import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from backend.app.config import (
    COOKIE_NAME,
    COOKIE_SECURE,
    DIALED_NUMBER_RE,
    MAX_ACTIVE_CALLS,
    OFFICER_ID_RE,
    OFFICER_PASSWORD,
    OFFICER_TOKEN,
    SESSION_TTL_SECONDS,
)
from backend.app.models import CreateCallRequest, LoginRequest
from backend.app.security import (
    call_creation_limiter,
    client_ip,
    create_session_token,
    login_limiter,
    require_officer,
)
from backend.app.state import CapacityError, call_manager

router = APIRouter()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@router.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    if not await login_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Too many login attempts. Try again shortly."
        )
    valid_password = secrets.compare_digest(payload.token, OFFICER_PASSWORD)
    valid_token = secrets.compare_digest(payload.token, OFFICER_TOKEN)
    if not (valid_password or valid_token):
        raise HTTPException(status_code=401, detail="Invalid officer password.")
    officer_id = payload.officer_id.strip()
    if not OFFICER_ID_RE.fullmatch(officer_id):
        raise HTTPException(status_code=400, detail="Invalid officer ID.")

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


# ---------------------------------------------------------------------------
# Caller sessions and WebRTC configuration
# ---------------------------------------------------------------------------
@router.post("/api/calls")
async def create_call(payload: CreateCallRequest, request: Request):
    dialed_number = payload.dialed_number.strip()
    if not DIALED_NUMBER_RE.fullmatch(dialed_number):
        raise HTTPException(status_code=400, detail="Invalid dialed number.")
    if not await call_creation_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Too many call attempts. Try again shortly."
        )
    try:
        call = await call_manager.create_call(
            dialed_number, max_active=MAX_ACTIVE_CALLS
        )
    except CapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"call_id": call.call_id, "dialed_number": call.dialed_number}


@router.get("/api/rtc-config")
async def rtc_config() -> Dict[str, Any]:
    ice_servers: list[Dict[str, Any]] = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]
    turn_urls = [
        url.strip()
        for url in os.getenv("CERS_TURN_URL", "").split(",")
        if url.strip()
    ]
    if turn_urls:
        ice_servers.append(
            {
                "urls": turn_urls,
                "username": os.getenv("CERS_TURN_USERNAME", ""),
                "credential": os.getenv("CERS_TURN_CREDENTIAL", ""),
            }
        )
    return {"iceServers": ice_servers}


@router.post("/api/calls/{call_id}/triage")
async def upload_triage_audio(call_id: str, file: UploadFile = File(...)):
    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call session not found.")
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio recording.")

    from backend.app.websockets import _async_triage_call
    asyncio.create_task(_async_triage_call(call_id, audio_bytes, file.content_type or "audio/wav"))
    return {"status": "triaging", "call_id": call_id}
