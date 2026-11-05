"""Asynchronous AI triage scheduling.

Kept separate from the transport modules so that both the HTTP endpoint and
the WebSocket path share one in-flight registry: a caller (or an attacker)
cannot stack concurrent inference jobs for the same session, and every task
is strongly referenced until it finishes.

Two kinds of job are scheduled:

* **initial** (``window=0``) — the pre-dispatch statement; may move a claimable
  call to ``WAITING`` and yields ``CALL_TRIAGED``.
* **window** (``window>0``) — a live in-call slice sent by the engaged officer;
  merges the numbers and appended transcript and yields ``CALL_TRIAGE_UPDATED``.
  It never changes the call status.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Dict, Optional, Set, Tuple

from backend.app.ai import triage_call_audio
from backend.app.config import AI_OFFLINE
from backend.app.metrics import metrics
from backend.app.state import call_manager

logger = logging.getLogger("cers.triage")

_inflight: Set[str] = set()
_tasks: Dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()


def looks_like_wav(audio_bytes: bytes) -> bool:
    """Validate that the payload is a mono 16-bit PCM WAV.

    The AI pipeline decodes ``wave.open`` + 16-bit ``h`` samples, and the
    caller/browser paths produce exactly that. Checking only the leading
    magic accepted stereo, 24-bit and other RIFF/WAVE files, which then
    failed deep inside the acoustic feature extractor and produced a
    meaningless default score. Anything that is not 16-bit mono PCM is
    rejected here with an explicit 415.
    """
    if not isinstance(audio_bytes, (bytes, bytearray)):
        return False
    if len(audio_bytes) < 44:
        return False
    try:
        if bytes(audio_bytes[0:4]) != b"RIFF" or bytes(audio_bytes[8:12]) != b"WAVE":
            return False

        offset = 12
        fmt_chunk: Optional[bytes] = None
        has_data = False
        total = len(audio_bytes)
        while offset + 8 <= total:
            chunk_id = bytes(audio_bytes[offset : offset + 4])
            chunk_size = struct.unpack_from("<I", audio_bytes, offset + 4)[0]
            payload = offset + 8
            end = payload + chunk_size
            if end > total:
                return False
            if chunk_id == b"fmt ":
                fmt_chunk = bytes(audio_bytes[payload:end])
            elif chunk_id == b"data":
                has_data = True
            # Chunks are padded to an even boundary.
            offset = end + (chunk_size % 2)

        if fmt_chunk is None or len(fmt_chunk) < 16 or not has_data:
            return False

        audio_format, channels, _sample_rate, _byte_rate, _block_align, bits = (
            struct.unpack_from("<HHIIHH", fmt_chunk, 0)
        )
        if audio_format == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: real format is in cbSize>0.
            if len(fmt_chunk) < 26:
                return False
            audio_format = struct.unpack_from("<H", fmt_chunk, 24)[0]
        return audio_format == 1 and channels == 1 and bits == 16
    except struct.error:
        return False


def in_flight(call_id: str, window: int = 0) -> bool:
    return f"{call_id}:{window}" in _inflight


async def schedule_triage(
    call_id: str,
    audio_bytes: bytes,
    mime_type: str = "audio/wav",
    *,
    window: int = 0,
    language: str = "",
) -> Tuple[bool, str]:
    """Queue a triage job, refusing duplicates for the same session+window."""
    key = f"{call_id}:{window}"
    async with _lock:
        if key in _inflight:
            return False, "Triage already in progress for this audio window."
        _inflight.add(key)

    task = asyncio.create_task(
        _run_triage(call_id, audio_bytes, mime_type, window=window, language=language)
    )
    _tasks[key] = task

    def _on_done(finished: asyncio.Task, _key: str = key) -> None:
        _finalize(_key, finished)

    # Strong reference + guaranteed registry cleanup, including cancellation.
    task.add_done_callback(_on_done)
    return True, "triaging"


async def schedule_text_triage(call_id: str, text: str) -> Tuple[bool, str]:
    """Queue a typed-statement triage (no audio available)."""
    key = f"{call_id}:text"
    async with _lock:
        if key in _inflight:
            return False, "Triage already in progress for this call."
        _inflight.add(key)

    task = asyncio.create_task(_run_text_triage(call_id, text))
    _tasks[key] = task

    def _on_done(finished: asyncio.Task, _key: str = key) -> None:
        _finalize(_key, finished)

    task.add_done_callback(_on_done)
    return True, "triaging"


async def _run_text_triage(call_id: str, text: str) -> None:
    from backend.app.ai import triage_text_statement

    try:
        await call_manager.update_call_status(call_id, "WAITING")
        result = await asyncio.to_thread(triage_text_statement, text)
        await call_manager.update_call_triage(call_id, result)
        metrics.incr("triage_text")
        if (result.get("ai_status") or {}).get("degraded"):
            metrics.incr("triage_heuristic")
        logger.info("Call %s triaged from typed statement: %s", call_id, result["triage_priority_score"])
    except Exception as exc:  # pragma: no cover - defensive
        metrics.incr("triage_failed")
        logger.exception("Text triage failed for %s: %s", call_id, exc)


def _finalize(key: str, task: asyncio.Task) -> None:
    _tasks.pop(key, None)
    _inflight.discard(key)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Triage task %s failed: %s", key, error)


async def _run_triage(
    call_id: str,
    audio_bytes: bytes,
    mime_type: str,
    *,
    window: int = 0,
    language: str = "",
) -> None:
    try:
        if window == 0:
            # Make the call claimable immediately; the score lands a moment later.
            await call_manager.update_call_status(call_id, "WAITING")

        result = await asyncio.to_thread(
            triage_call_audio, audio_bytes, mime_type, language
        )

        if window > 0:
            result["transcript"] = _merge_transcript(call_id, result.get("transcript"))
            await call_manager.update_call_triage(call_id, result, partial=True)
            metrics.incr("triage_windows")
        else:
            await call_manager.update_call_triage(call_id, result)
            metrics.incr("triage_ok")

        status = result.get("ai_status") or {}
        if status.get("degraded"):
            metrics.incr("triage_heuristic")
        logger.info(
            "Call %s triaged (window=%s): score %s | %s | %s | ai=%s",
            call_id,
            window,
            result["triage_priority_score"],
            result["urgency_badge"],
            result["incident_category"],
            status,
        )
    except Exception as exc:  # pragma: no cover - defensive
        metrics.incr("triage_failed")
        logger.exception("Error in triage for %s: %s", call_id, exc)


def _merge_transcript(call_id: str, incoming: str | None) -> str:
    """Append a live window's transcript to the running call transcript."""
    call = call_manager.get_call(call_id)
    previous = (call.transcript if call else "") or ""
    fresh = (incoming or "").strip()
    if not fresh or fresh == "Acoustic audio stream analyzed.":
        return previous
    if fresh in previous:
        return previous
    return f"{previous} {fresh}".strip()[:4000]


async def shutdown() -> None:
    """Cancel outstanding triage work (used on application shutdown)."""
    pending = list(_tasks.values())
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
    _inflight.clear()


def offline() -> bool:
    return AI_OFFLINE
