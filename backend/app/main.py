"""FastAPI application assembly for the CERS dispatch gateway."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app import api, pages, recordings, websockets
from backend.app.config import (
    ALLOWED_ORIGINS,
    CALL_TTL_SECONDS,
    CLEANUP_INTERVAL_SECONDS,
    FRONTEND_DIR,
    OFFICER_TOKEN,
    RECORDINGS_DIR,
)
from backend.app.security import install_security_headers
from backend.app.state import call_manager

__all__ = ["app", "create_app", "OFFICER_TOKEN"]


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            await call_manager.cleanup_stale_calls(ended_ttl=CALL_TTL_SECONDS)
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"[CERS] cleanup failed: {exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (FRONTEND_DIR / "static").mkdir(parents=True, exist_ok=True)
    (FRONTEND_DIR / "caller").mkdir(parents=True, exist_ok=True)
    (FRONTEND_DIR / "officer").mkdir(parents=True, exist_ok=True)
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


def create_app() -> FastAPI:
    # Ensure mount targets exist before StaticFiles validates them.
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    for directory in ("static", "caller", "officer"):
        (FRONTEND_DIR / directory).mkdir(parents=True, exist_ok=True)

    application = FastAPI(
        title="Cyber Emergency Response System (CERS)", lifespan=lifespan
    )

    # Same-origin deployments need no CORS entry. Configure an explicit
    # allowlist only when the frontend is hosted elsewhere.
    if ALLOWED_ORIGINS:
        allow_all = "*" in ALLOWED_ORIGINS
        application.add_middleware(
            CORSMiddleware,
            allow_origins=ALLOWED_ORIGINS,
            allow_credentials=not allow_all,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

    install_security_headers(application)

    application.include_router(api.router)
    application.include_router(recordings.router)
    application.include_router(websockets.router)
    application.include_router(pages.router)

    # Mounts are intentionally used for sub-resources only. Never build file
    # paths from request path parameters (the previous implementation allowed
    # traversal with encoded backslashes on Windows).
    application.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR / "static")),
        name="static",
    )
    application.mount(
        "/caller",
        StaticFiles(directory=str(FRONTEND_DIR / "caller"), html=True),
        name="caller-assets",
    )
    application.mount(
        "/officer",
        StaticFiles(directory=str(FRONTEND_DIR / "officer"), html=True),
        name="officer-assets",
    )
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
