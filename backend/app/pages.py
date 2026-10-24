"""HTML page routes for the caller dialer and officer console."""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter
from fastapi.responses import FileResponse

from backend.app.config import FRONTEND_DIR

router = APIRouter()


@router.get("/")
@router.get("/caller")
async def caller_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "caller" / "index.html")


@router.get("/officer")
async def officer_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "officer" / "index.html")


@router.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}
