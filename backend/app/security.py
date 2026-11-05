"""Signed officer sessions, caller capabilities, and HTTP hardening."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket

from backend.app.config import (
    COOKIE_NAME,
    COOKIE_SECURE,
    HOST_RE,
    OFFICER_ID_RE,
    SECRET_KEY,
    SESSION_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Signed session cookies
# ---------------------------------------------------------------------------
def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _matches(payload: str, signature: Optional[str]) -> bool:
    if not signature or not isinstance(signature, str):
        return False
    return hmac.compare_digest(signature, _sign(payload))


def create_session_token(officer_id: str) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{officer_id}.{expires}"
    return f"{payload}.{_sign(payload)}"


def verify_session_token(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        officer_id, expires_raw, signature = value.rsplit(".", 2)
        expires = int(expires_raw)
    except (ValueError, AttributeError):
        return None
    if expires < time.time():
        return None
    if not OFFICER_ID_RE.fullmatch(officer_id):
        return None
    if not _matches(f"{officer_id}.{expires_raw}", signature):
        return None
    return officer_id


def require_officer(request: Request) -> str:
    officer_id = verify_session_token(request.cookies.get(COOKIE_NAME))
    if not officer_id:
        raise HTTPException(status_code=401, detail="Officer authentication required.")
    return officer_id


def websocket_officer(websocket: WebSocket) -> Optional[str]:
    return verify_session_token(websocket.cookies.get(COOKIE_NAME))


# ---------------------------------------------------------------------------
# Caller capability tokens
#
# The dialer is intentionally unauthenticated, so the call_id alone must not
# be a bearer credential: it travels through URLs, logs and browser history.
# POST /api/calls mints a per-call token (HMAC of the call id under the server
# secret) that the caller presents when it attaches its socket or uploads
# triage audio. Server recomputes the same token, so nothing is stored.
# ---------------------------------------------------------------------------
def create_call_token(call_id: str) -> str:
    return _sign(f"call:{call_id}")


def verify_call_token(call_id: str, token: Optional[str]) -> bool:
    return _matches(f"call:{call_id}", token)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.time()
        async with self._lock:
            bucket = [t for t in self._events.get(key, []) if now - t < self.window]
            if len(bucket) >= self.limit:
                self._events[key] = bucket
                return False
            bucket.append(now)
            self._events[key] = bucket
            if len(self._events) > 4096:
                self._events = {
                    k: v
                    for k, v in self._events.items()
                    if v and now - v[-1] < self.window
                }
            return True


call_creation_limiter = SlidingWindowLimiter(limit=20, window_seconds=60)
login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
login_identity_limiter = SlidingWindowLimiter(limit=8, window_seconds=300)
triage_limiter = SlidingWindowLimiter(limit=6, window_seconds=60)
# Each forensic audit costs an ASR + SER + LLM call.
analyze_limiter = SlidingWindowLimiter(limit=6, window_seconds=60)


def client_ip(request: Request) -> str:
    """Best-effort client IP for per-IP rate limiting.

    Render's edge overwrites ``X-Forwarded-For`` with the real peer before
    forwarding, so the first entry is the client when hosted there. Uvicorn
    is NOT started with ``--proxy-headers`` (a deliberate choice: with no
    trusted-proxy config we cannot verify the peer), so ``request.client``
    would be Render's proxy IP and every visitor would share one rate-limit
    bucket. Direct peers can spoof this header; the per-identity login
    limiter is the hard brute-force guard — IP limiting is defence in
    depth, so we accept the valid XFF value only.
    """
    import ipaddress

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# HTTP security headers
# ---------------------------------------------------------------------------
_BASE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "media-src 'self' blob: data: https://*.supabase.co; "
    "object-src 'none'; worker-src 'self'; "
    "{connect_src}"
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _connect_src(request: Request) -> str:
    """Same-origin WebSockets only — do not allow ``ws:`` anywhere."""
    host = request.headers.get("host", "")
    if not host or not HOST_RE.fullmatch(host):
        return "connect-src 'self' https://*.supabase.co; "
    return f"connect-src 'self' ws://{host} wss://{host} https://*.supabase.co; "


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "microphone=(self), camera=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        _BASE_CSP.format(connect_src=_connect_src(request)),
    )
    if COOKIE_SECURE:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    path = request.url.path
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif path.startswith(("/static/", "/caller/", "/officer/")) and not path.endswith(
        ".html"
    ):
        # Content-hashed URLs (see scripts/stamp_assets.py) can be cached
        # forever; anything else must revalidate so a deploy is picked up.
        if request.query_params.get("v"):
            response.headers.setdefault(
                "Cache-Control", "public, max-age=31536000, immutable"
            )
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
    return response


def install_security_headers(app: FastAPI) -> None:
    app.middleware("http")(security_headers)
