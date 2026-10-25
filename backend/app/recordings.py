"""Authenticated recording storage, playback, and metadata APIs."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from backend.app.ai import analyze_call_forensics
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


_last_supabase_sync: float = 0.0


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


def _upload_json_to_supabase_sync(filename: str, data: Dict[str, Any]) -> bool:
    if not (SUPABASE_URL and SUPABASE_KEY):
        return False
    try:
        from supabase import create_client

        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        client.storage.from_(SUPABASE_BUCKET).upload(
            file=payload,
            path=filename,
            file_options={"content-type": "application/json", "upsert": "true"},
        )
        return True
    except Exception as exc:
        print(f"[CERS Supabase JSON Upload Error]: {exc}")
        return False


def _sync_from_supabase_sync(force: bool = False) -> None:
    global _last_supabase_sync
    if not (SUPABASE_URL and SUPABASE_KEY):
        return
    now = time.time()
    has_local_meta = any(RECORDINGS_DIR.glob("*.json")) if RECORDINGS_DIR.exists() else False
    if not force and has_local_meta and (now - _last_supabase_sync < 15.0):
        return

    try:
        from supabase import create_client

        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        remote_files = client.storage.from_(SUPABASE_BUCKET).list()
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

        json_stems: set[str] = set()
        audio_files: dict[str, dict] = {}

        for item in remote_files:
            name = item.get("name", "")
            if name.endswith(".json") and META_NAME_RE.fullmatch(name):
                stem = name[:-5]
                json_stems.add(stem)
                local_path = RECORDINGS_DIR / name
                if not local_path.exists():
                    try:
                        content = client.storage.from_(SUPABASE_BUCKET).download(name)
                        with open(local_path, "wb") as handle:
                            handle.write(content)
                    except Exception as err:
                        print(f"[CERS Supabase Download Error for {name}]: {err}")
            elif (
                name.endswith(".webm")
                or name.endswith(".ogg")
                or name.endswith(".wav")
                or name.endswith(".m4a")
                or name.endswith(".mp4")
            ) and AUDIO_NAME_RE.fullmatch(name):
                stem = Path(name).stem
                audio_files[stem] = item

        # Auto-recover legacy recordings that were uploaded to Supabase before metadata persistence
        for stem, item in audio_files.items():
            if stem not in json_stems:
                meta_name = f"{stem}.json"
                local_meta = RECORDINGS_DIR / meta_name
                if not local_meta.exists():
                    audio_name = item.get("name", "")
                    public_url = client.storage.from_(SUPABASE_BUCKET).get_public_url(audio_name)
                    size = item.get("metadata", {}).get("size") or 0

                    ts = int(now)
                    rec_id = stem.replace("recording_", "").replace("call_", "")
                    parts = rec_id.split("_")
                    if parts and parts[0].isdigit():
                        raw = int(parts[0])
                        ts = raw // 1000 if raw > 10_000_000_000 else raw

                    synthesized = {
                        "id": rec_id,
                        "meta_name": meta_name,
                        "call_id": None,
                        "dialed_number": "112",
                        "officer_id": None,
                        "filename": audio_name,
                        "url": public_url,
                        "timestamp": ts,
                        "duration": 0.0,
                        "notes": "Recovered from Supabase Cloud Storage",
                        "size_bytes": size,
                        "mime_type": "audio/webm",
                        "original_filename": audio_name,
                        "cloud_stored": True,
                    }
                    _write_json_sync(local_meta, synthesized)
                    _upload_json_to_supabase_sync(meta_name, synthesized)

        _last_supabase_sync = now
    except Exception as exc:
        print(f"[CERS Supabase Metadata Sync Error]: {exc}")


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
    _sync_from_supabase_sync()
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

    triage_data = {
        "priority_score": getattr(call, "triage_priority_score", 10),
        "urgency_level": getattr(call, "urgency_level", "ROUTINE"),
        "urgency_badge": getattr(call, "urgency_badge", "🟢 ROUTINE"),
        "incident_category": getattr(call, "incident_category", "GENERAL REPORT"),
        "incident_summary": getattr(call, "incident_summary", ""),
        "transcript": getattr(call, "transcript", ""),
        "emotions": getattr(call, "emotions", {}),
        "scam_indicators": getattr(call, "scam_indicators", []),
        "officer_sop": getattr(call, "officer_sop", ""),
    }

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
        "triage": triage_data,
    }
    await run_in_threadpool(_write_json_sync, RECORDINGS_DIR / meta_name, metadata)
    await run_in_threadpool(_upload_json_to_supabase_sync, meta_name, metadata)
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
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                from supabase import create_client

                client = create_client(SUPABASE_URL, SUPABASE_KEY)
                public_url = client.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
                return RedirectResponse(url=public_url)
            except Exception:
                pass
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
        await run_in_threadpool(_upload_json_to_supabase_sync, meta_name, updated)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail="Could not update recording notes."
        ) from exc
    updated["meta_name"] = meta_name
    return JSONResponse(updated)


@router.post("/{meta_name}/analyze")
async def analyze_recording(
    meta_name: str,
    force: bool = False,
    _: str = Depends(require_officer),
):
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")

    with open(path, "r", encoding="utf-8") as handle:
        recording = json.load(handle)

    if not isinstance(recording, dict):
        raise HTTPException(status_code=500, detail="Invalid recording metadata.")

    # Return cached forensic analysis if already done and not forced
    if not force and recording.get("forensic_analysis"):
        return JSONResponse(recording["forensic_analysis"])

    triage_info = recording.get("triage", {})
    transcript = triage_info.get("transcript", "")
    notes = recording.get("notes", "")

    # Look for local audio file to run acoustic SER
    audio_filename = recording.get("filename", "")
    audio_path = str(RECORDINGS_DIR / audio_filename) if audio_filename else None

    # Run forensic intelligence extraction via Groq Qwen + AWS SER
    forensics = await run_in_threadpool(
        analyze_call_forensics,
        transcript=transcript,
        notes=notes,
        triage_info=triage_info,
        audio_path=audio_path,
    )

    recording["forensic_analysis"] = forensics
    recording["analyzed_at"] = int(time.time())

    # Save to disk and sync to Supabase
    await run_in_threadpool(_write_json_sync, path, recording)
    await run_in_threadpool(_upload_json_to_supabase_sync, meta_name, recording)

    return JSONResponse(forensics)
