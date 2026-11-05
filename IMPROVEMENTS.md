# CERS — Improvement Roadmap

> Companion to [CODE_REVIEW.md](CODE_REVIEW.md). That document lists defects and their fixes; this one is about making the product *better* — not repairing it.
>
> Prioritised by (value to a cybercrime dispatch desk) ÷ (effort). Everything here is grounded in the current code, not generic advice.

---

## Implementation status (all shipped except where noted)

Everything below is implemented, tested and covered by the CI gates described in
`README.md` section 7. Deferrals are explicit, not silent.

| Item | Status |
|---|---|
| 1.1 Live window triage | **Done** - `POST /api/calls/{id}/triage/window`, `CALL_TRIAGE_UPDATED`, live transcript merge, officer console loop (`live-triage.js`) |
| 1.2 Dual-channel recording | **Done** - mixed + officer + caller `MediaRecorder`s, three tracks stored and hashed, per-channel playback |
| 1.3 Vault pagination/projection | **Done** - `GET /api/recordings?q=&limit=&offset=` returns summaries; full record via `/detail` |
| 1.4 Per-officer credentials | **Done** - `CERS_OFFICERS=Name:secret,...`; when set, only listed names authenticate |
| 1.5 Provider resilience | **Done** - retries + circuit breaker (`providers.py`), `ai_status` per triage, `CERS_AI_OFFLINE` |
| 1.6 Escalation | **Done** - 5s sweeper, `CALL_ESCALATED`, repeating alarm, card highlight |
| 2.1 PDF case packet | **Done** - `GET /api/recordings/{meta}/packet.pdf` (reportlab) |
| 2.2 Evidence hash chain | **Done** - per-track SHA-256 + `prev_hash` chain, `/verify`, shown in the PDF |
| 2.3 Live transcript | **Done** - appended per window and streamed into the station panel |
| 2.4 Metrics | **Done** - `/api/metrics` + console panel (time-to-claim, mix, providers) |
| 2.5 IVR language | **Done (plumbing)** - selector, per-call `?lang=` ASR, localized prompt with automatic English fallback; run `generate_ivr.py` with a Sarvam key to emit the Hindi/Marathi files |
| 2.6 Repeat-caller detection | **Partial** - `caller_number` is accepted, stored and shown; the prior-call index UI is not built (there is no telephony feed in this deployment) |
| 2.7 Disposition codes | **Done** - validated enum, saved with notes, shown in list + PDF |
| 3.1 Persistent state | **Partial by design** - single-worker guard (OS file lock) instead of a half-built Redis layer; multi-worker stays unsupported until a real broker is added |
| 3.2-3.4 aws_service | **Done** - threadpool inference, no CORS, background model load with `/health` readiness |
| 3.5 Analyze rate limit | **Done** |
| 3.6 Config consolidation | **Done** - all env access goes through `config.py` |
| 3.7 Lint/types/CI | **Done** - ruff + mypy + 70% coverage floor + frontend checker in GitHub Actions |
| 3.8 Asset caching | **Done** - content-hashed URLs (`scripts/stamp_assets.py`) + immutable `Cache-Control` |
| 3.9 Secret scanning | **Done** - gitleaks job in CI (`.github/workflows/ci.yml`) with `.gitleaks.toml` allowlisting only the published sample values; the startup placeholder check remains as a runtime guard |
| 3.10 Idempotent upload | **Done** - client UUID + server-side dedupe |
| 3.11 Vault backup | **Done** - `scripts/backup_vault.py` with manifest + `--verify` |
| 4.1 e2e in CI | **Done** - `backend/tests/test_e2e_ws.py`, marked `e2e`, deselected by default; CI runs it against a live gateway in per-officer mode (`CERS_E2E_OFFICERS` mirrors the server's `CERS_OFFICERS`) |
| 4.2-4.10 UX items | **Done** - shortcuts, waiting time, station triage panel, real quick actions, countdown/extend, consent notice, mic fallback, night theme, reduced motion |

**Remaining honest gaps:** no Redis-backed state (single worker only — `/health`
now reports `worker_mode`/`auth_mode` so the constraint is visible to
monitoring), no telephony integration for caller identity, and the
Hindi/Marathi IVR audio files need one `generate_ivr.py` run with a Sarvam key.

---

## 0. The framing: three claims the code does not yet back

This is the most valuable thing to fix, because the README sells a product the code only partly delivers. Each gap is also a feature.

| Claim in README | What the code does | Either build it, or soften the claim |
|---|---|---|
| "**Real-Time** Speech Emotion Analysis … detects distress **directly from caller audio in real time**" | Triage runs **once**, on a ≤10 s statement recorded *before* any officer connects (`vad.js:16` — `MAX_STATEMENT_MS = 10000`). Live call audio is never analysed. | **Build it** (item 2.1). This is the single most impressive upgrade available. |
| "Opus Audio **Dual-Stream** Recordings" / "**Dual-Track** Audio Oscilloscopes" | One `MediaRecorder` fed by a **mixed** `createMediaStreamDestination()` (`call.js:140-166`). Officer + caller are summed into a single mono track. | **Build it** (item 2.2) — dual-channel is genuinely better evidence. |
| "**32** unit and integration tests" | 54 now, but `test_e2e_ws.py` is still never collected (`testpaths = backend/tests`). | Wire it in behind a marker (item 4.1). |

---

## 1. P1 — Highest leverage

### 1.1 Make triage **stream during the call** (the headline feature)

**What.** Re-run SER + ASR on rolling ~5 s windows while the officer is engaged, and push `CALL_TRIAGE_UPDATED` so the queue card's priority/emotion meters move live.

**Why it matters.** Today the officer's screen shows numbers captured *before* the conversation began. If the caller calms down, or panics when the suspect calls back, the console never knows. The fused 60/40 score is the product's differentiator — right now it is computed from the least informative part of the call.

**How.**
- Client: the existing VAD already has a live `AnalyserNode`; add a `MediaRecorder` timeslice loop (or `AudioWorklet`) that posts 5 s chunks to `POST /api/calls/{id}/triage/window`.
- Server: the `triage.py` in-flight registry already prevents pile-ups — key it per `(call_id, window)`.
- Sound-force: the SER classes in `aws_service/app.py` already accept arbitrary WAV; no model change needed.
- Cheapest MVP: keep the one-shot triage, but add a **"re-triage live audio"** button on the active call for the officer. ~30 lines, proves the concept.

---

### 1.2 Dual-channel recording (officer and caller as separate tracks)

**What.** Record two blobs (or one container with two tracks) instead of a mono mix.

**Why it matters.**
- **Evidence.** A mixed track cannot prove who said what. A defence lawyer can argue the officer's "yes" came from the caller. Separate channels make the recording admissibility defensible — which is the entire point of a "forensic" vault.
- **Re-analysis.** You can re-run SER on the *caller channel only*. Today the officer's voice contaminates the emotion signal.
- **Playback.** Per-speaker volume/mute in the vault player.

**How.** Two `MediaRecorder`s — one on `micSource`, one on `remoteSource` — upload as `{call_id}.officer.webm` + `{call_id}.caller.webm`, and keep the mixed one as a convenience stream. `recordings.py` already accepts `.webm`; add a `channels: ["officer","caller"]` field to the metadata and a synced dual `<audio>` pair in the vault.

---

### 1.3 The vault will not survive 100 recordings

**What.** `GET /api/recordings` returns **every** metadata file *including the full `forensic_analysis` and transcript* (`recordings.py:290`), then the client filters in memory.

**Why it matters.** A single dossier is ~4–8 KB of JSON; 500 calls is several MB per page load, parsed and held in a JS Map, on a phone-class laptop at a police desk. It also means the archive search can never exceed what the browser received.

**How.**
- Return a **summary projection** (`id, timestamp, duration, dialed_number, officer_id, notes, audio_available, incident_category, priority_score, has_analysis`) and fetch the dossier on selection (`GET /api/recordings/{meta}`).
- Add server-side `?q=&limit=&offset=` with the existing `META_NAME_RE`-validated names.
- This is a ~90-minute change with a big perceived-speed win.

---

### 1.4 Per-officer credentials (make the audit trail real)

**What.** Names are now free text and *self-asserted* — anyone with the shared password can sign in as `Mayuresh`, and the "Handled by" field becomes fiction.

**Why it matters.** For a police tool, `officer_id` on a recording is the difference between an audit trail and a decoration. It is also the cheapest possible "multi-user" upgrade.

**How.**
```ini
# .env — one line per officer, token may differ
CERS_OFFICERS=Mayuresh:<redacted published value>,Rahul:rahul-pass,Neha:neha-pass
```
Parse into `{name: token}` at startup; `login` looks the name up and compares against *that officer's* token. Keep `CERS_OFFICER_PASSWORD` as a fallback so nothing breaks. ~40 lines in `config.py` + `api.py`, no UI change.

---

### 1.5 Provider resilience: retries, circuit breaker, and an explicit "degraded" mode

**What.** Every AI call is a bare `requests.post(..., timeout=N)` with no retry, no breaker, no budget (`ai.py`). Three providers, each a single point of failure.

**Why it matters.** If Groq is down you silently fall back to keyword heuristics, and the card still renders as if AI analysed it (now labelled, but the officer cannot tell *how* degraded). If Sarvam is slow, triage blocks for 15 s.

**How.**
- `providers.py` with `call_with_retry(fn, attempts=2, backoff=0.4)` + a per-provider breaker (5 failures → 60 s open).
- Return `ai_status: {asr: "ok|failed|skipped", ser: "aws|hf|none", llm: "ok|failed"}` in the triage payload; render a small badge on the card ("⚠ heuristic only").
- Add `CERS_AI_OFFLINE=1` to force the deterministic path for demos and CI.

---

### 1.6 Escalation: a CRITICAL call must not sit unclaimed

**What.** Priority 95 is computed, sorted, and then… waits, exactly like priority 10. The only signal is a single `playIncomingPing()` for score ≥ 85.

**Why it matters.** The entire 60/40 thesis is "get the worst call answered first". Without escalation, the score only reorders a list nobody may be looking at.

**How.**
- Server: in `_cleanup_loop` (already running every 300 s — make it 5 s for this), if a call ≥ 85 has been `WAITING` for > 30 s, broadcast `CALL_ESCALATED` and bump a `escalated_at` field.
- Client: repeating alarm tone until the card is focused/claimed, plus a flashing border; supervisor view = another officer tab with an "ESCALATIONS" filter.
- ~80 lines, no schema risk.

---

## 2. P2 — Product features that match the pitch

| # | Improvement | Why | How |
|---|---|---|---|
| 2.1 | **Case packet export (PDF)** | The SOP/FIR checklist is currently text on a screen an officer must retype into a form. | Server-side render of `forensic_analysis` → A4 dossier with the recording hash + timeline; `GET /api/recordings/{meta}/packet.pdf`. |
| 2.2 | **Evidence integrity hash chain** | Plain files + a JSON sidecar prove nothing. | On write, store `sha256(audio)` + `prev_hash`; expose a `verify` endpoint. ~30 lines, turns "vault" into "chain of custody". |
| 2.3 | **Live transcript in the active station** | Officers currently read a summary captured before they picked up; the live conversation is not transcribed. | Same streaming loop as 1.1; render Sarvam partials under the oscilloscope. |
| 2.4 | **Triage queue analytics** | "60/40 fusion" is impossible to demo without numbers. | `/api/metrics`: median time-to-claim by priority band, category mix, provider latency, heuristic-fallback rate. One endpoint + one small dashboard tab. |
| 2.5 | **Caller language selection at IVR** | `SARVAM_LANGUAGE_CODE=unknown` and the IVR audio is English-only for a Marathi/Hindi hotline. | IVR prompt "1 Hindi, 2 Marathi, 3 English" → sets the ASR language per call; three pre-generated prompts via the existing `generate_ivr.py`. |
| 2.6 | **Repeat-caller detection** | Helplines are full of repeat victims and serial scammers; nothing links calls. | When telephony gives an MSISDN (or the officer captures one), index it and show "3 prior calls" on the card. |
| 2.7 | **Structured disposition codes** | Notes are free text — unanalysable. | A small enum (resolved / referred / FIR filed / false positive) + optional note, stored next to `notes`. |

---

## 3. P3 — Engineering, operations, DX

| # | Improvement | Detail |
|---|---|---|
| 3.1 | **Persist call state** | `CallManager` is a process-local dict. One Render restart (free tier spins down!) wipes the queue; two workers break atomic claims entirely. Move to Redis (`SETNX` for claims, TTL for the session) or document "single worker only" loudly in the README. This is the biggest architectural risk left. |
| 3.2 | **Fix `aws_service` concurrency** | `async def predict_emotion` does blocking `soundfile`/`torch` work on the event loop (`aws_service/app.py:182`), so concurrent calls serialise — directly inflating your advertised ~740 ms latency under load. Wrap in `run_in_threadpool`, and cap request size. |
| 3.3 | **`aws_service` CORS is invalid** | `allow_origins=["*"]` **with** `allow_credentials=True` (lines 25-26) is rejected by browsers and pointless for a server-to-server API. Drop CORS entirely; restrict by network/firewall instead. |
| 3.4 | **Model load failure = no start** | `from_pretrained` at import (lines 37-38) means a transient HF outage prevents the service from booting. Load in a lifespan hook, retry, and serve `/health` = `degraded` until ready. |
| 3.5 | **Rate-limit the expensive endpoint** | `POST /api/recordings/{meta}/analyze` has no limiter — one officer clicking re-analyse in a loop is a paid Groq + SER request each time. Reuse `SlidingWindowLimiter` (added for triage). |
| 3.6 | **Consolidate env access** | `api.py:142-150` reads `CERS_TURN_*` directly; `ai.py` re-reads `sarvam_api`, `GROQ_API_KEY`, `AWS_EMOTION_URL`. Move everything into `config.py` so there is one place to audit. |
| 3.7 | **Lint + types in CI** | Nothing enforces style today. Add `ruff` (fast, covers the unused-import class of bug I found by hand), `mypy` on `backend/app`, `eslint` on the frontend, and a GitHub Actions job running `pytest --cov` with a floor. The repo already gitignores `.coverage`/`htmlcov`, so coverage was intended. |
| 3.8 | **Cache the frontend properly** | Assets are unversioned; only two URLs carry a hand-bumped `?v=4`. Add content-hashed filenames (or a simple `?v=<mtime>` template) + `Cache-Control: immutable` on `/static`. |
| 3.9 | **Secret scanning** | The officer password now lives in `.env.example` by design. Add `gitleaks` in CI so a future real key cannot be committed silently. |
| 3.10 | **Idempotent recording upload** | The `pagehide` beacon and the normal finish path can both fire. Send a client-generated `recording_uuid` and have the server ignore a duplicate. |
| 3.11 | **Backup/restore for the vault** | 30 metadata files and 15 audio files sit on one disk with a dead Supabase mirror. A `scripts/backup_vault.ps1` (zip + hash manifest) is 20 lines and protects real evidence. |

---

## 4. P4 — UX quick wins (mostly <30 min each)

| # | Improvement | Where |
|---|---|---|
| 4.1 | Collect `test_e2e_ws.py` under `backend/tests/` with `@pytest.mark.e2e` (deselected by default) so the IVR/claim flow actually runs in CI. | `pytest.ini` |
| 4.2 | **Keyboard shortcuts** on the officer console: `A` claim top call, `Esc` end, `M` mute, `Ctrl+K` vault search. Dispatch desks are keyboard-driven. | `events.js` |
| 4.3 | Show **waiting time** on each queue card ("waiting 1m 42s") — currently only call *duration* is shown, which stays 0 until claimed. | `queue.js` |
| 4.4 | Show triage summary + SOP **in the active-call station**, not only on the queue card the officer just clicked away from. | `station.css` / `call.js` |
| 4.5 | Replace the **fake iOS tab bar** (Favorites/Recents/Contacts/Voicemail — none wired) with real actions: "Re-record statement", "Language", "Call 1930". Dead chrome erodes trust in a helpline UI. | `caller/index.html` |
| 4.6 | **Countdown for the 10 s statement** ("recording stops in 4s") instead of a silent cutoff mid-sentence; allow "press * to add another 10s". | `vad.js` |
| 4.7 | **Visible recording consent** on the caller screen ("This call is recorded for police records"). Likely a legal requirement, and it exists only as audio today. | `caller/index.html` |
| 4.8 | **Denied-mic fallback**: continue with DTMF/text instead of a dead end. | `caller/js/call.js` |
| 4.9 | **High-contrast / dark theme** for the officer console — this is a night-shift tool, and the current theme is bright off-white with scanlines. | `base.css` tokens |
| 4.10 | Honour `prefers-reduced-motion` for the scanlines/pulses (the toast CSS already does; the console does not). | `base.css` |

---

## 5. Suggested sequence

**Sprint A — credibility (≈1 week).** 1.4 per-officer creds · 1.2 dual-channel recording · 1.3 vault pagination · 4.1 CI test wiring · 3.7 lint/types.

**Sprint B — the demo (≈1 week).** 1.1 live triage (MVP button) · 1.5 degraded-mode labelling · 2.4 metrics endpoint + small dashboard · 2.1 PDF case packet.

**Sprint C — operations (≈1 week).** 1.6 escalation · 3.1 Redis-backed state · 3.2/3.3/3.4 `aws_service` fixes · 3.11 vault backup.

**Cheapest three wins, if you only have an afternoon:** 1.3 (vault payload), 1.5 (degraded badge), 4.3 (waiting time). All are visible, low-risk, and touch files already under test.
