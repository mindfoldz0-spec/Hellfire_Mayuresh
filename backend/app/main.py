"""FastAPI application assembly for the CERS dispatch gateway."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.app import api, pages, recordings, triage, websockets
from backend.app.config import (
    ALLOW_MULTI_WORKER,
    ALLOWED_ORIGINS,
    CALL_TTL_SECONDS,
    CLEANUP_INTERVAL_SECONDS,
    ESCALATION_AFTER_SECONDS,
    ESCALATION_PRIORITY,
    ESCALATION_SWEEP_SECONDS,
    FRONTEND_DIR,
    OFFICER_TOKEN,
    RECORDING_RETENTION_DAYS,
    RECORDINGS_DIR,
    WORKER_LOCK_PATH,
)
from backend.app.security import install_security_headers
from backend.app.state import call_manager

__all__ = ["app", "create_app", "OFFICER_TOKEN"]

logger = logging.getLogger("cers")

# Held for the lifetime of the process; the OS releases it on exit (so a crash
# never leaves a stale lock, unlike a pid file).
_worker_lock_handle = None


def configure_logging() -> None:
    level = os.getenv("CERS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _acquire_worker_lock() -> Optional[object]:
    """Refuse to run two workers against in-memory call state.

    Atomic claiming is only correct inside one process: two workers would each
    hold their own ``CallManager`` and both could hand the same caller to a
    different officer. Rather than corrupt the queue silently, the second
    process fails fast.
    """
    global _worker_lock_handle
    if ALLOW_MULTI_WORKER:
        logger.warning(
            "CERS_ALLOW_MULTI_WORKER=1: call state is in-memory, so atomic "
            "claiming is NOT safe across workers."
        )
        return None
    WORKER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not a context manager: the handle must outlive this call and
    # is closed by _release_worker_lock() on shutdown.
    handle = open(WORKER_LOCK_PATH, "a+")  # noqa: SIM115
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == "":
                handle.write("x")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - POSIX path
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined]
                handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB  # type: ignore[attr-defined]
            )
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            "Refusing to start a second worker: call state is in-memory, so two "
            "workers would break atomic call claiming. Run a single worker "
            f"('--workers 1'), or set CERS_ALLOW_MULTI_WORKER=1 to override. ({exc})"
        ) from exc
    _worker_lock_handle = handle
    return handle


def _release_worker_lock() -> None:
    global _worker_lock_handle
    handle, _worker_lock_handle = _worker_lock_handle, None
    if handle is not None:
        with suppress(Exception):
            handle.close()


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            removed = await call_manager.cleanup_stale_calls(
                ended_ttl=CALL_TTL_SECONDS
            )
            if removed:
                logger.info("Purged %d stale call session(s)", len(removed))
            if RECORDING_RETENTION_DAYS > 0:
                purged = await run_in_threadpool(
                    recordings.purge_expired, RECORDING_RETENTION_DAYS
                )
                if purged:
                    logger.info("Retention removed %d recording(s)", len(purged))
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("Stale-call cleanup failed")


async def _escalation_loop() -> None:
    """Turn the priority score into an actual alarm for unclaimed calls."""
    while True:
        await asyncio.sleep(ESCALATION_SWEEP_SECONDS)
        try:
            escalated = await call_manager.escalate_stale_calls(
                priority_min=ESCALATION_PRIORITY,
                older_than=ESCALATION_AFTER_SECONDS,
            )
            if escalated:
                logger.warning(
                    "Escalated %d unclaimed high-priority call(s)", len(escalated)
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("Escalation sweep failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    _acquire_worker_lock()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (FRONTEND_DIR / "static").mkdir(parents=True, exist_ok=True)
    (FRONTEND_DIR / "caller").mkdir(parents=True, exist_ok=True)
    (FRONTEND_DIR / "officer").mkdir(parents=True, exist_ok=True)

    background: list[asyncio.Task] = [
        asyncio.create_task(_cleanup_loop(), name="cers-cleanup"),
        asyncio.create_task(_escalation_loop(), name="cers-escalation"),
        # Strongly referenced so it is neither garbage collected nor lost.
        asyncio.create_task(
            run_in_threadpool(recordings._sync_from_supabase_sync, True),
            name="cers-supabase-sync",
        ),
    ]
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError, Exception):
                await task
        await triage.shutdown()
        _release_worker_lock()
        logger.info("CERS gateway shut down cleanly")


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
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-Call-Token"],
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
