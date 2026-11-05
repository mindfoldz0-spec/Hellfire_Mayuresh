"""HTML page routes, health probe, and caller language catalogue."""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from backend.app import providers
from backend.app.config import (
    AI_OFFLINE,
    ALLOW_MULTI_WORKER,
    COOKIE_NAME,
    FRONTEND_DIR,
    OFFICERS,
)
from backend.app.metrics import metrics
from backend.app.security import verify_session_token
from backend.app.state import call_manager

router = APIRouter()

# IVR language options. Audio prompts are generated per language by
# ``generate_ivr.py``; the mapping is used by the dialer and the ASR call.
LANGUAGES = {
    "en-IN": {"label": "English", "ivr": "ivr_prompt.wav"},
    "hi-IN": {"label": "हिन्दी", "ivr": "ivr_prompt_hi.wav"},
    "mr-IN": {"label": "मराठी", "ivr": "ivr_prompt_mr.wav"},
}


@router.get("/")
@router.get("/caller")
async def caller_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "caller" / "index.html")


@router.get("/officer")
async def officer_page(request: Request):
    """Server-side gate: no session, no console HTML at all.

    The SPA also handles sessions client-side, but keeping the markup behind
    an authenticated route means an unauthenticated client receives no
    console at all.
    """
    if not verify_session_token(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse("/officer/login", status_code=303)
    return FileResponse(FRONTEND_DIR / "officer" / "index.html")


@router.get("/officer/login")
async def officer_login_page(request: Request):
    if verify_session_token(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse("/officer", status_code=303)
    return FileResponse(FRONTEND_DIR / "officer" / "login.html")


@router.get("/api/languages")
async def languages() -> Dict[str, Any]:
    return {"languages": LANGUAGES, "default": "en-IN"}


@router.get("/health")
async def health() -> Dict[str, Any]:
    """Liveness + honest dependency state (what the console can rely on)."""
    provider_state = providers.registry.snapshot()
    degraded = AI_OFFLINE or any(
        state.get("breaker_open") for state in provider_state.values()
    )
    return {
        "status": "ok",
        "degraded": degraded,
        "ai_offline": AI_OFFLINE,
        # Loud operational facts: call state is in-memory, so more than one
        # worker would break atomic claiming; per-officer accounts make the
        # officer_id on a recording a verified identity.
        "worker_mode": "multi-unsafe" if ALLOW_MULTI_WORKER else "single",
        "auth_mode": "per-officer" if OFFICERS else "shared",
        "active_calls": call_manager.active_call_count(),
        "escalated": sum(
            1 for call in call_manager.calls.values() if call.escalated_at
        ),
        "providers": provider_state,
        "uptime_seconds": round(time.time() - metrics.started_at, 1),
    }
