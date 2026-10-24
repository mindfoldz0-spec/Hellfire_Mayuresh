"""Authenticated recording storage, playback, and metadata APIs."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.app.config import (
    ALLOWED_AUDIO_EXTENSIONS,
    AUDIO_NAME_RE,
    MAX_RECORDING_BYTES,
    META_NAME_RE,
    RECORDINGS_DIR,
    SUPABASE_BUCKET,
    SUPABASE_KEY,
    SUPABASE_URL,
)
from backend.app.models import NotesRequest
from backend.app.security import require_officer
from backend.app.state import call_manager

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


def _upload_to_supabase_sync(
    filename: str, path: Path, mime_type: str
) -> Optional[str]:
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        from supabase import create_client

        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        with open(path, "rb") as handle:
            client.storage.from_(SUPABASE_BUCKET).upload(
                file=handle,
                path=filename,
                file_options={"content-type": mime_type, "upsert": "true"},
            )
        return client.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
    except Exception as exc:
        print(f"[CERS Supabase Storage Error]: {exc}")
        return None


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------
def _safe_recording_path(name: str, pattern: re.Pattern[str]) -> Path:
    if not pattern.fullmatch(name):
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = (RECORDINGS_DIR / name).resolve()
    if path.parent != RECORDINGS_DIR.resolve():
        raise HTTPException(status_code=404, detail="Recording not found.")
    return path


def _write_json_sync(path: Path, data: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def _list_recordings_sync() -> list[Dict[str, Any]]:
    recordings: list[Dict[str, Any]] = []
    if not RECORDINGS_DIR.exists():
        return recordings
    for meta_path in RECORDINGS_DIR.glob("*.json"):
        if not META_NAME_RE.fullmatch(meta_path.name):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                recording = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(recording, dict):
            continue

        filename = str(recording.get("filename", ""))
        existing_url = str(recording.get("url", ""))
        if existing_url.startswith("https://") or existing_url.startswith("http://"):
            recording["url"] = existing_url
        elif AUDIO_NAME_RE.fullmatch(filename):
            recording["url"] = f"/api/recordings/{filename}"
        else:
            recording.pop("url", None)
        recording["meta_name"] = meta_path.name
        recording.setdefault("id", meta_path.stem)
        recording.setdefault("officer_id", None)
        recordings.append(recording)

    recordings.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return recordings


def _update_notes_sync(path: Path, notes: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        recording = json.load(handle)
    if not isinstance(recording, dict):
        raise ValueError("Invalid recording metadata")
    recording["notes"] = notes
    recording["updated_at"] = int(time.time())
    _write_json_sync(path, recording)
    return recording


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/{call_id}")
async def upload_recording(
    call_id: str,
    audio_file: UploadFile = File(...),
    duration: float = Form(0.0),
    notes: str = Form(""),
    officer_id: str = Depends(require_officer),
):
    try:
        parsed = uuid.UUID(call_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Call not found.") from None
    if parsed.version != 4:
        raise HTTPException(status_code=404, detail="Call not found.")

    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found.")
    if call.officer_id != officer_id:
        raise HTTPException(status_code=403, detail="You are not assigned to this call.")
    if duration < 0 or duration > 86_400:
        raise HTTPException(status_code=400, detail="Invalid recording duration.")
    if len(notes) > 10_000:
        raise HTTPException(status_code=400, detail="Notes are too long.")

    extension = Path(audio_file.filename or "").suffix.lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported audio format. Use WebM, OGG, WAV, M4A, or MP4.",
        )

    recording_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"
    filename = f"recording_{recording_id}{extension}"
    meta_name = f"recording_{recording_id}.json"
    destination = RECORDINGS_DIR / filename
    temporary = destination.with_name(destination.name + ".part")
    total_bytes = 0

    try:
        async with aiofiles.open(temporary, "wb") as handle:
            while True:
                chunk = await audio_file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_RECORDING_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Recording exceeds the "
                            f"{MAX_RECORDING_BYTES // (1024 * 1024)} MB limit."
                        ),
                    )
                await handle.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Empty recording upload.")
        await run_in_threadpool(os.replace, temporary, destination)
    except Exception:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise

    recording_url = f"/api/recordings/{filename}"
    mime_type = audio_file.content_type or mimetypes.guess_type(filename)[0] or "audio/webm"
    cloud_url = await run_in_threadpool(
        _upload_to_supabase_sync, filename, destination, mime_type
    )
    if cloud_url:
        recording_url = cloud_url

    metadata: Dict[str, Any] = {
        "id": recording_id,
        "meta_name": meta_name,
        "call_id": call_id,
        "dialed_number": call.dialed_number,
        "officer_id": officer_id,
        "filename": filename,
        "url": recording_url,
        "timestamp": int(time.time()),
        "duration": round(duration, 1),
        "notes": notes,
        "size_bytes": total_bytes,
        "mime_type": mime_type,
        "original_filename": Path(audio_file.filename or "").name,
        "cloud_stored": bool(cloud_url),
    }
    await run_in_threadpool(_write_json_sync, RECORDINGS_DIR / meta_name, metadata)
    await call_manager.attach_recording(call_id, recording_url, notes)

    return JSONResponse(
        {
            "status": "success",
            "url": recording_url,
            "filename": filename,
            "id": recording_id,
            "meta_name": meta_name,
        }
    )


@router.get("")
async def list_recordings(_: str = Depends(require_officer)):
    recordings = await run_in_threadpool(_list_recordings_sync)
    return JSONResponse(recordings)


@router.get("/{filename}")
async def get_recording(
    filename: str,
    download: bool = False,
    _: str = Depends(require_officer),
):
    path = _safe_recording_path(filename, AUDIO_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
    )


@router.put("/{meta_name}")
async def update_recording_notes(
    meta_name: str,
    payload: NotesRequest,
    _: str = Depends(require_officer),
):
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    try:
        updated = await run_in_threadpool(_update_notes_sync, path, payload.notes)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail="Could not update recording notes."
        ) from exc
    updated["meta_name"] = meta_name
    return JSONResponse(updated)
