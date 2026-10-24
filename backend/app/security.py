"""Signed officer sessions, authentication dependencies, and HTTP hardening."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket

from backend.app.config import (
    COOKIE_NAME,
    OFFICER_ID_RE,
    SECRET_KEY,
    SESSION_TTL_SECONDS,
)

# ---------------------------------------------------------------------------
# Signed session cookies
# ---------------------------------------------------------------------------
def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


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
    if not hmac.compare_digest(signature, _sign(f"{officer_id}.{expires_raw}")):
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


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# HTTP security headers
# ---------------------------------------------------------------------------
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
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "media-src 'self' blob: data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def install_security_headers(app: FastAPI) -> None:
    app.middleware("http")(security_headers)
