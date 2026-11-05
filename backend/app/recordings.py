"""Authenticated recording storage, playback, forensic tooling, and metadata APIs."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from backend.app.ai import analyze_call_forensics
from backend.app.config import (
    ALLOWED_AUDIO_EXTENSIONS,
    AUDIO_NAME_RE,
    DISPOSITIONS,
    MAX_RECORDING_BYTES,
    META_NAME_RE,
    RECORDINGS_DIR,
    SUPABASE_BUCKET,
    SUPABASE_KEY,
    SUPABASE_URL,
)
from backend.app.models import NotesRequest
from backend.app.pdf import build_case_packet
from backend.app.security import analyze_limiter, require_officer
from backend.app.state import call_manager

logger = logging.getLogger("cers.recordings")

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

# Remote listing is paginated (Supabase Storage caps a page at 100 objects).
SYNC_PAGE_SIZE = 100
MAX_SYNC_PAGES = 50
SYNC_MIN_BACKOFF = 30.0
SYNC_MAX_BACKOFF = 600.0

# Channel suffixes: "" is the mixed reference stream.
TRACK_SUFFIXES = {"mixed": "", "officer": "_officer", "caller": "_caller"}

_sync_lock = threading.Lock()
_sync_state: Dict[str, Any] = {"last_attempt": 0.0, "backoff": SYNC_MIN_BACKOFF}


def _supabase_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _signed_access_url(filename: str, expires_in: int = 900) -> Optional[str]:
    """Short-lived download URL for a remote object.

    The officer console authenticates against the gateway, and the gateway
    redirects to a one-shot signed URL. We never hand the browser a public
    Supabase URL, so the bucket can stay private.
    """
    if not _supabase_configured() or not AUDIO_NAME_RE.fullmatch(filename):
        return None
    try:
        client = _storage_client()
        result = client.storage.from_(SUPABASE_BUCKET).create_signed_url(
            filename, expires_in
        )
        if isinstance(result, dict):
            return result.get("signedURL") or result.get("signedUrl")
    except Exception as exc:
        logger.warning("Could not mint Supabase signed URL for %s: %s", filename, exc)
    return None


def _storage_client():
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Supabase sync / upload helpers
# ---------------------------------------------------------------------------
def _upload_to_supabase_sync(filename: str, path: Path, mime_type: str) -> bool:
    """Mirror a local object to Supabase. Return True on success.

    The URL is never written to the metadata; the officer console always goes
    through ``/api/recordings/...`` and gets a one-shot signed redirect.
    """
    if not _supabase_configured():
        return False
    try:
        client = _storage_client()
        with open(path, "rb") as handle:
            client.storage.from_(SUPABASE_BUCKET).upload(
                file=handle,
                path=filename,
                file_options={"content-type": mime_type, "upsert": "true"},
            )
        return True
    except Exception as exc:
        logger.warning("Supabase storage upload failed for %s: %s", filename, exc)
        return False


def _upload_json_to_supabase_sync(filename: str, data: Dict[str, Any]) -> bool:
    if not _supabase_configured():
        return False
    try:
        client = _storage_client()
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        client.storage.from_(SUPABASE_BUCKET).upload(
            file=payload,
            path=filename,
            file_options={"content-type": "application/json", "upsert": "true"},
        )
        return True
    except Exception as exc:
        logger.warning("Supabase metadata upload failed for %s: %s", filename, exc)
        return False


def _delete_from_supabase_sync(*names: str) -> None:
    if not _supabase_configured():
        return
    try:
        client = _storage_client()
        client.storage.from_(SUPABASE_BUCKET).remove([name for name in names if name])
    except Exception as exc:
        logger.warning("Supabase delete failed: %s", exc)


def _list_all_remote(client) -> List[Dict[str, Any]]:
    """Page through the bucket; a single ``list()`` call truncates at 100."""
    files: List[Dict[str, Any]] = []
    offset = 0
    for _ in range(MAX_SYNC_PAGES):
        page = client.storage.from_(SUPABASE_BUCKET).list(
            "", {"limit": SYNC_PAGE_SIZE, "offset": offset}
        )
        if not isinstance(page, list) or not page:
            break
        files.extend(page)
        if len(page) < SYNC_PAGE_SIZE:
            break
        offset += SYNC_PAGE_SIZE
    return files


def _sync_from_supabase_sync(force: bool = False) -> None:
    """Mirror remote metadata locally, with backoff after a failed attempt.

    A cold/down bucket used to be retried on *every* recordings request, so a
    single unreachable project made the console slow forever.
    """
    if not _supabase_configured():
        return

    # A forced sync (startup / explicit refresh) waits for a concurrent run;
    # an implicit one simply skips, so a slow bucket never blocks the API.
    acquired = _sync_lock.acquire(timeout=15 if force else -1, blocking=force)
    if not acquired:
        return
    try:
        now = time.time()
        if not force and now - float(_sync_state["last_attempt"]) < float(
            _sync_state["backoff"]
        ):
            return
        _sync_state["last_attempt"] = now

        try:
            client = _storage_client()
            remote_files = _list_all_remote(client)
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

            json_stems: set[str] = set()
            audio_files: Dict[str, Dict[str, Any]] = {}

            for item in remote_files:
                name = str(item.get("name", ""))
                if name.endswith(".json") and META_NAME_RE.fullmatch(name):
                    stem = name[:-5]
                    json_stems.add(stem)
                    local_path = RECORDINGS_DIR / name
                    if not local_path.exists():
                        try:
                            content = client.storage.from_(
                                SUPABASE_BUCKET
                            ).download(name)
                            with open(local_path, "wb") as handle:
                                handle.write(content)
                        except Exception as err:
                            logger.warning("Could not download %s: %s", name, err)
                elif AUDIO_NAME_RE.fullmatch(name):
                    audio_files[Path(name).stem] = item

            # Recover legacy recordings uploaded before metadata persistence.
            for stem, item in audio_files.items():
                if stem in json_stems:
                    continue
                meta_name = f"{stem}.json"
                local_meta = RECORDINGS_DIR / meta_name
                if local_meta.exists():
                    continue
                audio_name = str(item.get("name", ""))
                recovered_url = f"/api/recordings/{audio_name}"
                metadata = item.get("metadata") or {}
                size = metadata.get("size") if isinstance(metadata, dict) else 0

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
                    "tracks": {"mixed": audio_name},
                    "url": recovered_url,
                    "timestamp": ts,
                    "duration": 0.0,
                    "notes": "Recovered from Supabase Cloud Storage",
                    "size_bytes": size or 0,
                    "mime_type": "audio/webm",
                    "original_filename": audio_name,
                    "cloud_stored": True,
                }
                _write_json_sync(local_meta, synthesized)
                _upload_json_to_supabase_sync(meta_name, synthesized)

            _sync_state["backoff"] = SYNC_MIN_BACKOFF
        except Exception as exc:
            _sync_state["backoff"] = min(
                float(_sync_state["backoff"]) * 2, SYNC_MAX_BACKOFF
            )
            logger.warning(
                "Supabase sync failed (next retry in %.0fs): %s",
                _sync_state["backoff"],
                exc,
            )
    finally:
        _sync_lock.release()


# ---------------------------------------------------------------------------
# File / integrity helpers
# ---------------------------------------------------------------------------
def _safe_recording_path(name: str, pattern: re.Pattern[str]) -> Path:
    if not pattern.fullmatch(name):
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = (RECORDINGS_DIR / name).resolve()
    if path.parent != RECORDINGS_DIR.resolve():
        raise HTTPException(status_code=404, detail="Recording not found.")
    return path


def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Recording metadata is not an object")
    return data


def _write_json_sync(path: Path, data: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _chain_head() -> str:
    """Hash of the newest archived record, or ``""`` for the genesis link."""
    newest_ts = -1.0
    newest_hash = ""
    if not RECORDINGS_DIR.exists():
        return ""
    for meta_path in RECORDINGS_DIR.glob("*.json"):
        if not META_NAME_RE.fullmatch(meta_path.name):
            continue
        try:
            data = _read_json(meta_path)
        except (OSError, ValueError):
            continue
        ts = float(data.get("timestamp") or 0)
        if ts >= newest_ts:
            newest_ts = ts
            newest_hash = str((data.get("chain") or {}).get("hash") or "")
    return newest_hash


def _chain_hash(previous: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{previous}\n{canonical}".encode()).hexdigest()


async def _store_track(upload: UploadFile, destination: Path) -> tuple[int, str]:
    """Stream one audio track to disk, returning (bytes, sha256)."""
    digest = hashlib.sha256()
    total = 0
    temporary = destination.with_name(destination.name + ".part")
    try:
        async with aiofiles.open(temporary, "wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RECORDING_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Recording exceeds the "
                            f"{MAX_RECORDING_BYTES // (1024 * 1024)} MB limit."
                        ),
                    )
                digest.update(chunk)
                await handle.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="Empty recording upload.")
        await run_in_threadpool(os.replace, temporary, destination)
    except Exception:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise
    return total, digest.hexdigest()


def _find_by_client_uuid(client_uuid: str) -> Optional[Dict[str, Any]]:
    if not client_uuid or not RECORDINGS_DIR.exists():
        return None
    for meta_path in RECORDINGS_DIR.glob("*.json"):
        if not META_NAME_RE.fullmatch(meta_path.name):
            continue
        try:
            data = _read_json(meta_path)
        except (OSError, ValueError):
            continue
        if data.get("client_uuid") == client_uuid:
            return data
    return None


def _resolve_tracks(recording: Dict[str, Any]) -> Dict[str, str]:
    tracks = recording.get("tracks")
    if isinstance(tracks, dict) and tracks:
        return {str(k): str(v) for k, v in tracks.items() if v}
    filename = str(recording.get("filename") or "")
    return {"mixed": filename} if filename else {}


def _list_recordings_sync() -> List[Dict[str, Any]]:
    """Full records, newest first, with local audio availability resolved."""
    _sync_from_supabase_sync()
    recordings: List[Dict[str, Any]] = []
    if not RECORDINGS_DIR.exists():
        return recordings
    for meta_path in RECORDINGS_DIR.glob("*.json"):
        if not META_NAME_RE.fullmatch(meta_path.name):
            continue
        try:
            recording = _read_json(meta_path)
        except (OSError, ValueError):
            continue

        tracks = _resolve_tracks(recording)
        filename = str(recording.get("filename") or "")
        existing_url = str(recording.get("url", ""))
        local_audio = (
            RECORDINGS_DIR / filename if AUDIO_NAME_RE.fullmatch(filename) else None
        )
        has_local_audio = bool(local_audio and local_audio.is_file())
        # A mirrored object still counts as audio, but it must never be served
        # straight from a public bucket URL; the officer goes through the
        # authenticated route, which mints a one-shot signed redirect.
        remote_hint = bool(
            recording.get("cloud_stored") is True
            or existing_url.startswith(("https://", "http://"))
        )

        if has_local_audio or remote_hint and AUDIO_NAME_RE.fullmatch(filename):
            recording["url"] = f"/api/recordings/{filename}"
            recording["audio_available"] = True
        else:
            # Metadata without audio (failed/interrupted upload, or a remote
            # object that is gone). Surface it instead of a broken player.
            recording.pop("url", None)
            recording["audio_available"] = False

        recording["tracks"] = tracks
        recording["meta_name"] = meta_path.name
        recording.setdefault("id", meta_path.stem)
        recording.setdefault("officer_id", None)
        recordings.append(recording)

    recordings.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return recordings


def _summary(recording: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight list projection: no transcript, no dossier, no notes."""
    triage = recording.get("triage") or {}
    forensics = recording.get("forensic_analysis") or {}
    verdict = forensics.get("triage_verdict") or {}
    item = {
        "id": recording.get("id"),
        "meta_name": recording.get("meta_name"),
        "call_id": recording.get("call_id"),
        "dialed_number": recording.get("dialed_number"),
        "caller_number": recording.get("caller_number") or "",
        "officer_id": recording.get("officer_id"),
        "filename": recording.get("filename"),
        "tracks": list((recording.get("tracks") or {}).keys()) or ["mixed"],
        "url": recording.get("url"),
        "audio_available": recording.get("audio_available", True),
        "timestamp": recording.get("timestamp"),
        "duration": recording.get("duration"),
        "size_bytes": recording.get("size_bytes"),
        "mime_type": recording.get("mime_type"),
        "disposition": recording.get("disposition") or "",
        "priority_score": verdict.get("priority_score", triage.get("priority_score")),
        "incident_category": verdict.get("category") or triage.get("incident_category"),
        "has_analysis": bool(forensics),
        # Truncated note preview keeps the list useful without shipping the log.
        "notes_preview": (recording.get("notes") or "")[:120],
    }
    if not item["url"]:
        # No playable source: omit the key so the console never binds a
        # player to a URL that is guaranteed to 404.
        item.pop("url")
    return item


def _search_blob(recording: Dict[str, Any]) -> str:
    triage = recording.get("triage") or {}
    forensics = recording.get("forensic_analysis") or {}
    parts = [
        recording.get("call_id"),
        recording.get("dialed_number"),
        recording.get("caller_number"),
        recording.get("officer_id"),
        recording.get("filename"),
        recording.get("notes"),
        recording.get("disposition"),
        triage.get("transcript"),
        triage.get("incident_category"),
        (forensics.get("triage_verdict") or {}).get("category"),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def _update_notes_sync(path: Path, notes: str, disposition: str = "") -> Dict[str, Any]:
    recording = _read_json(path)
    recording["notes"] = notes
    if disposition:
        recording["disposition"] = disposition
    recording["updated_at"] = int(time.time())
    _write_json_sync(path, recording)
    return recording


def purge_expired(retention_days: int) -> List[str]:
    """Delete archived sessions older than the retention window.

    Disabled by default (``CERS_RECORDING_RETENTION_DAYS=0``): automatic
    deletion of police evidence must be an explicit policy choice.
    """
    if retention_days <= 0 or not RECORDINGS_DIR.exists():
        return []
    cutoff = time.time() - retention_days * 86_400
    removed: List[str] = []
    remote_names: List[str] = []
    for meta_path in list(RECORDINGS_DIR.glob("*.json")):
        if not META_NAME_RE.fullmatch(meta_path.name):
            continue
        try:
            recording = _read_json(meta_path)
        except (OSError, ValueError):
            continue
        if float(recording.get("timestamp") or 0) >= cutoff:
            continue
        remote_names.append(meta_path.name)
        for filename in _resolve_tracks(recording).values():
            if AUDIO_NAME_RE.fullmatch(filename):
                remote_names.append(filename)
                audio = RECORDINGS_DIR / filename
                try:
                    if audio.is_file():
                        audio.unlink()
                except OSError as exc:
                    logger.warning("Retention: could not remove %s: %s", filename, exc)
        try:
            meta_path.unlink()
            removed.append(meta_path.name)
        except OSError as exc:
            logger.warning("Retention: could not remove %s: %s", meta_path.name, exc)
    if removed:
        logger.info("Retention purged %d recording(s)", len(removed))
        _delete_from_supabase_sync(*remote_names)
    return removed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/{call_id}")
async def upload_recording(
    call_id: str,
    audio_file: UploadFile = File(...),
    officer_audio: Optional[UploadFile] = File(default=None),
    caller_audio: Optional[UploadFile] = File(default=None),
    duration: float = Form(0.0),
    notes: str = Form(""),
    client_uuid: str = Form(""),
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

    # Idempotency: the pagehide beacon and the normal finish path can both fire.
    if client_uuid:
        existing = await run_in_threadpool(_find_by_client_uuid, client_uuid)
        if existing is not None:
            return JSONResponse(
                {
                    "status": "duplicate",
                    "url": existing.get("url"),
                    "filename": existing.get("filename"),
                    "id": existing.get("id"),
                    "meta_name": existing.get("meta_name"),
                }
            )

    uploads = {"mixed": audio_file, "officer": officer_audio, "caller": caller_audio}
    recording_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"
    meta_name = f"recording_{recording_id}.json"

    tracks: Dict[str, str] = {}
    hashes: Dict[str, str] = {}
    total_bytes = 0
    mime_type = "audio/webm"

    for name, upload in uploads.items():
        if upload is None:
            continue
        extension = Path(upload.filename or "").suffix.lower()
        if extension not in ALLOWED_AUDIO_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported {name} audio format. Use WebM, OGG, WAV, M4A, or MP4."
                ),
            )
        filename = f"recording_{recording_id}{TRACK_SUFFIXES[name]}{extension}"
        size, digest = await _store_track(upload, RECORDINGS_DIR / filename)
        tracks[name] = filename
        hashes[name] = digest
        total_bytes += size
        if name == "mixed":
            mime_type = (
                upload.content_type
                or mimetypes.guess_type(filename)[0]
                or "audio/webm"
            )

    mixed_name = tracks["mixed"]
    recording_url = f"/api/recordings/{mixed_name}"
    cloud_stored = await run_in_threadpool(
        _upload_to_supabase_sync, mixed_name, RECORDINGS_DIR / mixed_name, mime_type
    )
    for name in ("officer", "caller"):
        if name in tracks:
            await run_in_threadpool(
                _upload_to_supabase_sync,
                tracks[name],
                RECORDINGS_DIR / tracks[name],
                "audio/webm",
            )

    triage_data = {
        "priority_score": getattr(call, "triage_priority_score", 10),
        "urgency_level": getattr(call, "urgency_level", "ROUTINE"),
        "urgency_badge": getattr(call, "urgency_badge", "🟢 ROUTINE"),
        "incident_category": getattr(call, "incident_category", "GENERAL REPORT"),
        "incident_summary": getattr(call, "incident_summary", ""),
        "transcript": getattr(call, "transcript", ""),
        "emotions": getattr(call, "emotions", {}),
        "scam_indicators": getattr(call, "scam_indicators", []),
        "officer_sop": getattr(call, "officer_sop", []),
    }

    timestamp = int(time.time())
    previous = await run_in_threadpool(_chain_head)
    chain_payload = {
        "id": recording_id,
        "timestamp": timestamp,
        "officer_id": officer_id,
        "call_id": call_id,
        "tracks": tracks,
        "sha256": hashes,
    }
    metadata: Dict[str, Any] = {
        "id": recording_id,
        "meta_name": meta_name,
        "call_id": call_id,
        "dialed_number": call.dialed_number,
        "caller_number": getattr(call, "caller_number", "") or "",
        "officer_id": officer_id,
        "filename": mixed_name,
        "tracks": tracks,
        "url": recording_url,
        "timestamp": timestamp,
        "duration": round(duration, 1),
        "notes": notes,
        "disposition": "",
        "size_bytes": total_bytes,
        "mime_type": mime_type,
        "original_filename": Path(audio_file.filename or "").name,
        "cloud_stored": bool(cloud_stored),
        "client_uuid": client_uuid,
        "sha256": hashes,
        "chain": {
            "prev_hash": previous,
            "hash": _chain_hash(previous, chain_payload),
        },
        "triage": triage_data,
    }
    await run_in_threadpool(_write_json_sync, RECORDINGS_DIR / meta_name, metadata)
    await run_in_threadpool(_upload_json_to_supabase_sync, meta_name, metadata)
    await call_manager.attach_recording(call_id, recording_url, notes)

    return JSONResponse(
        {
            "status": "success",
            "url": recording_url,
            "filename": mixed_name,
            "tracks": tracks,
            "id": recording_id,
            "meta_name": meta_name,
        }
    )


@router.get("")
async def list_recordings(
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    _: str = Depends(require_officer),
):
    """Paginated, projected archive listing with server-side search."""
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    rows = await run_in_threadpool(_list_recordings_sync)

    needle = (q or "").strip().lower()
    if needle:
        rows = [row for row in rows if needle in _search_blob(row)]

    total = len(rows)
    page = rows[offset : offset + limit]
    return JSONResponse(
        {
            "items": [_summary(row) for row in page],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
        }
    )


@router.get("/{meta_name}/detail")
async def recording_detail(meta_name: str, _: str = Depends(require_officer)):
    """Full record (transcript, dossier, tracks, hashes) for one session."""
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    try:
        recording = await run_in_threadpool(_read_json, path)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail="Invalid recording metadata."
        ) from exc
    recording["meta_name"] = meta_name
    recording["tracks"] = _resolve_tracks(recording)
    for name, filename in recording["tracks"].items():
        if AUDIO_NAME_RE.fullmatch(filename):
            recording.setdefault("track_urls", {})[name] = (
                f"/api/recordings/{filename}"
            )
    return JSONResponse(recording)


@router.get("/{meta_name}/verify")
async def verify_recording(meta_name: str, _: str = Depends(require_officer)):
    """Recompute the evidence hashes and check the chain link."""
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    recording = await run_in_threadpool(_read_json, path)

    expected = recording.get("sha256") or {}
    tracks: Dict[str, str] = {}
    for name, filename in _resolve_tracks(recording).items():
        if not AUDIO_NAME_RE.fullmatch(filename):
            tracks[name] = "invalid-name"
            continue
        audio = RECORDINGS_DIR / filename
        if not audio.is_file():
            tracks[name] = "missing"
            continue
        wanted = expected.get(name) if isinstance(expected, dict) else None
        actual = await run_in_threadpool(_sha256_file, audio)
        if not wanted:
            tracks[name] = "unrecorded"
        else:
            tracks[name] = "ok" if actual == wanted else "MISMATCH"

    chain = recording.get("chain") or {}
    return JSONResponse(
        {
            "meta_name": meta_name,
            "tracks": tracks,
            "chain": {
                "prev_hash": chain.get("prev_hash") or "",
                "hash": chain.get("hash") or "",
            },
            "intact": bool(tracks) and all(value == "ok" for value in tracks.values()),
        }
    )


@router.get("/{meta_name}/packet.pdf")
async def recording_packet(meta_name: str, _: str = Depends(require_officer)):
    """Printable A4 case packet (dossier + SOP + evidence hashes)."""
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    recording = await run_in_threadpool(_read_json, path)
    payload = await run_in_threadpool(build_case_packet, recording)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{path.stem}-case-packet.pdf"'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/{filename}")
async def get_recording(
    filename: str,
    download: bool = False,
    _: str = Depends(require_officer),
):
    path = _safe_recording_path(filename, AUDIO_NAME_RE)
    if not path.is_file():
        signed = _signed_access_url(filename)
        if signed:
            return RedirectResponse(url=signed, status_code=307)
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
    disposition = (payload.disposition or "").strip().upper()
    if disposition not in DISPOSITIONS:
        raise HTTPException(status_code=400, detail="Unknown disposition code.")
    try:
        updated = await run_in_threadpool(
            _update_notes_sync, path, payload.notes, disposition
        )
        await run_in_threadpool(_upload_json_to_supabase_sync, meta_name, updated)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail="Could not update recording notes."
        ) from exc
    updated["meta_name"] = meta_name
    return JSONResponse(updated)


@router.delete("/{meta_name}")
async def delete_recording(meta_name: str, _: str = Depends(require_officer)):
    """Purge an archived session (metadata + every audio track, local and remote)."""
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")

    try:
        recording = await run_in_threadpool(_read_json, path)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail="Invalid recording metadata."
        ) from exc

    remote_names = [meta_name]
    for filename in _resolve_tracks(recording).values():
        if not AUDIO_NAME_RE.fullmatch(filename):
            continue
        audio_path = _safe_recording_path(filename, AUDIO_NAME_RE)
        remote_names.append(filename)
        try:
            if audio_path.is_file():
                audio_path.unlink()
        except OSError as exc:
            logger.warning("Could not remove audio %s: %s", audio_path.name, exc)

    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail="Could not remove recording metadata."
        ) from exc

    await run_in_threadpool(_delete_from_supabase_sync, *remote_names)
    return JSONResponse({"status": "deleted", "meta_name": meta_name})


@router.post("/{meta_name}/analyze")
async def analyze_recording(
    meta_name: str,
    force: bool = False,
    officer_id: str = Depends(require_officer),
):
    path = _safe_recording_path(meta_name, META_NAME_RE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")

    try:
        recording = await run_in_threadpool(_read_json, path)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail="Invalid recording metadata."
        ) from exc

    # Return cached forensic analysis if already done and not forced
    if not force and recording.get("forensic_analysis"):
        return JSONResponse(recording["forensic_analysis"])

    # Each audit can cost an ASR + SER + LLM call: bound how often one officer
    # can trigger it.
    if not await analyze_limiter.allow(f"officer:{officer_id}"):
        raise HTTPException(
            status_code=429, detail="Too many forensic audits. Try again shortly."
        )

    triage_info = recording.get("triage", {})
    transcript = triage_info.get("transcript", "")
    notes = recording.get("notes", "")

    # Only ever resolve a regex-validated name inside the recordings folder.
    audio_filename = str(recording.get("filename", ""))
    audio_path = None
    if AUDIO_NAME_RE.fullmatch(audio_filename):
        candidate = _safe_recording_path(audio_filename, AUDIO_NAME_RE)
        if candidate.is_file():
            audio_path = str(candidate)

    forensics = await run_in_threadpool(
        analyze_call_forensics,
        transcript=transcript,
        notes=notes,
        triage_info=triage_info,
        audio_path=audio_path,
    )

    recording["forensic_analysis"] = forensics
    recording["analyzed_at"] = int(time.time())

    await run_in_threadpool(_write_json_sync, path, recording)
    await run_in_threadpool(_upload_json_to_supabase_sync, meta_name, recording)

    return JSONResponse(forensics)
