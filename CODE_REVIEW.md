# CERS — Workspace Code Review

**Scope:** 68 source files (~9,850 LOC) across `backend/`, `frontend/`, `aws_service/`, plus tests, scripts and deploy config.
**Method:** full read-through, automated cross-checks (JS syntax, ES-module import/export resolution, DOM id ↔ JS binding, CSS linkage, AST unused-import scan, config dead-symbol scan), full test run, and **live reproduction** of suspected runtime bugs against the real app and real AI providers.

**Baseline:** `pytest` → **31 passed, 1 error** (the error is a sandbox `tmp_path` artifact, not a product defect — see M11).

> Everything marked ✅ VERIFIED was reproduced or measured, not inferred from reading alone.

---

## Resolution status — all findings fixed

Every item below was fixed in the same session and re-verified. The suite went
from **31 passed / 1 error** to **52 passed**, runs fully offline, and the real
server was booted and smoke-tested afterwards.

| ID | Fix | Verification |
|---|---|---|
| **P0-1** | `_normalize_forensics()` in `ai.py` is now the single canonical dossier contract (derives `triage_verdict.category` + `vulnerability_assessment`); `vault.js` reads the canonical keys and renders `psychological_evaluation` as an object. | Live Groq run: all 12 console-read fields resolve; contract test asserts the exact key set. |
| **P0-2** | `update_call_triage()` only transitions `RINGING/IVR/WAITING → WAITING`; `ENGAGED` merges data without touching status; `ENDED` is never resurrected. | `test_late_triage_*` + live repro: status stays `ENGAGED`, second claim returns `False`. |
| **P0-3** | Per-call HMAC capability token (`X-Call-Token`), streaming byte cap, RIFF/WAVE sniffing, in-flight de-duplication in new `backend/app/triage.py`, task registry with cancellation. | `test_triage*`: 403 without token, 415 non-WAV, 409 duplicate, 413 over-size. |
| **P0-4** | No credential is baked into the source any more — it must come from `.env`, and startup fails if it is missing. **Relaxed at the owner's request:** a simple password is allowed (only warned about), and the operator ID is now a free-text name instead of `OFFICER_01/02/03`. `render.yaml` no longer contains credentials. | `test_credentials_must_be_configured`, `test_login_uses_the_officer_name_as_identity`, `test_login_rejects_unsafe_officer_names`; live login as `Mayuresh` → 200, wrong password → 401. |
| **P1-1** | `IVR_FINISHED` → `WAITING`. | `test_ivr_finished_makes_call_claimable`. |
| **P1-2** | Caller socket requires the capability token; the "second caller cannot take over" docstring is now true. | `test_caller_socket_requires_token`. |
| **P1-3** | `_constant_time_equals()` compares UTF-8 bytes. | `test_non_ascii_password_is_rejected_not_crashed`. |
| **P1-4** | `visualizer.css` + `controls.css` linked. | Served 200 by the live server. |
| **P1-5** | ScriptProcessor routed into a 0-gain sink instead of `destination`. | Code + comment; no audible echo path remains. |
| **P1-6** | Autouse fixture isolates `RECORDINGS_DIR` to `tmp_path`; leaked mock entry deleted. | Suite no longer writes to `backend/recordings`. |
| **P1-7** | Sync: non-blocking lock, exponential backoff (30s→600s), paginated listing, validated names. | `test_sync_paginates_until_a_short_page`; boot log shows one failed sync then backoff. |
| **P1-8** | `audio_available` flag; orphaned metadata no longer renders a broken player; `DELETE /api/recordings/{meta}` + purge button. | `test_listing_flags_metadata_without_audio`, `test_delete_recording_*`. |
| **M1** | All server-derived text now built with `textContent`/DOM nodes (SOP + emotion meters). | Queue rebuilt without `innerHTML` interpolation. |
| **M2** | Background tasks strongly referenced and cancelled on shutdown incl. `triage.shutdown()`. | Clean shutdown log line. |
| **M3** | Stereo / non-16-bit WAV handled; non-WAV rejected at the boundary with 415. | `test_triage_rejects_non_wav_payload`. |
| **M4** | Deterministic acoustic fallback (no `random`), explicit `emotions.source`, console labels estimates "ESTIMATED". | `test_emotion_estimate_is_deterministic_and_labelled`. |
| **M5** | Duration ticker uses a measured `serverClockOffset`. | `QUEUE_SNAPSHOT.server_time` → `state.serverClockOffset`. |
| **M6** | Per-socket `asyncio.Lock` around every send. | `CallManager._send`. |
| **M7** | Per-officer-identity login limiter + per-call/IP triage limiter. | Limiter reset fixture; 429 covered. |
| **M8** | Blocking JSON reads moved to the threadpool. | `run_in_threadpool(_read_json, …)`. |
| **M9** | `audio_filename` validated through `_safe_recording_path`. | `analyze_recording`. |
| **M10** | Caller-disconnect grace period (15s prod, 0 in bare `CallManager`). | `test_caller_disconnect_grace_*`. |
| **M11** | Unused `tmp_path` fixture removed. | Suite error gone. |
| **M12** | Dead config (`HUGGINGFACE_MODEL`) removed; `MAX_ACTIVE_CALLS` no longer duplicated. | AST scan clean. |
| **M13** | `requirements.txt` pinned to verified versions; ML stack documented for freezing. | — |
| **M14** | Per-request CSP `connect-src` (exact host, no bare `ws:`), `object-src 'none'`, HSTS when secure cookies are on. | Live header inspection. |
| **P3-1…13** | CSS linked, viewport zoom restored, `hold_music.wav` now a real fallback source, unused imports removed, `print` → `logging`, 14 `alert()` → non-blocking toasts, signature-guarded queue render, README/config model names reconciled, close reasons added, purge endpoint added. | `alert(` count = 0; static checks clean. |

**Not done deliberately:** deleting `temp/cloudflared.exe` (53 MB), the root source-art PNGs, and the user's orphaned recording metadata — those are the operator's files, not code.

---


## Severity legend

| Level | Meaning |
|---|---|
| **P0** | Broken core feature, security hole, or data/state corruption. Fix first. |
| **P1** | Wrong behaviour a user will hit in normal operation. |
| **P2** | Robustness, performance, maintainability, hygiene. |
| **P3** | Nice-to-have / polish. |

---

# P0 — Critical

## P0-1 · The flagship "Full AI Forensic Audit" renders almost entirely empty ✅ VERIFIED

The backend and the frontend disagree on **every** field name of the forensic dossier. `ai.py` produces one schema, `vault.js` reads a different, older schema. Result: after an officer clicks `[ 🔍 FULL AI INCIDENT & EMOTION AUDIT ]`, 12 of 12 detail fields fall back to placeholder text and the psychological summary literally renders **`[object Object]`**.

**Producer** — `backend/app/ai.py` (prompt at lines 616-666; same names in the offline fallback at lines 695-742):

```
caller_dossier:     name, contact, location, language_spoken
suspect_profile:    claimed_identity, impersonated_agency, phone_numbers, digital_handles
financial_impact:   amount_lost, amount_demanded, destination_upi_ids,
                    destination_bank_accounts, victim_bank
attack_vector:      scam_type, coercion_technique, malicious_tools
psychological_evaluation: { dominant_state, summary, vulnerability_level }   ← OBJECT
triage_verdict:     priority_score, urgency_badge
emotions:           ..., vocal_distress_score
```

**Consumer** — `frontend/officer/js/vault.js`:

| `vault.js` reads | Line | Actual key | Result |
|---|---|---|---|
| `verdict.category` | 228 | *(absent)* | "CYBER EXTORTION" |
| `verdict.vulnerability_assessment` | 239 | `psychological_evaluation.vulnerability_level` | "VULNERABILITY DETECTED" |
| `caller.contact_number` | 245 | `contact` | "Emergency Line" |
| `caller.location_city` | 246 | `location` | "Not Disclosed" |
| `caller.spoken_language` | 247 | `language_spoken` | "English / Hindi / Marathi" |
| `suspect.claimed_agency` | 252 | `impersonated_agency` | "Unknown / Not Disclosed" |
| `finance.amount_lost_inr` | 268 | `amount_lost` | **"₹0"** (dangerous: implies no loss) |
| `finance.amount_demanded_inr` | 269 | `amount_demanded` | "None reported" |
| `finance.victim_bank_name` | 276 | `victim_bank` | "Unknown" |
| `vector.coercion_mechanism` | 281 | `coercion_technique` | "Psychological Coercion" |
| `vector.malicious_tools_or_apks` | 283 | `malicious_tools` | "None logged" |
| `emo.distress_score` | 295 | `vocal_distress_score` | "ACOUSTIC DISTRESS: 80%" (hardcoded default) |
| `forensics.psychological_evaluation` | 329 | *(object)* | **`[object Object]`** |

**Live proof** — ran the real pipeline against Groq (1.8 s, extraction itself works fine):

```
FRONTEND-READ FIELDS (None => renders fallback text):
  caller.contact_number              -> None
  caller.location_city               -> None
  caller.spoken_language             -> None
  suspect.claimed_agency             -> None
  finance.amount_lost_inr            -> None
  finance.amount_demanded_inr        -> None
  finance.victim_bank_name           -> None
  vector.coercion_mechanism          -> None
  vector.malicious_tools_or_apks     -> None
  verdict.category                   -> None
  verdict.vulnerability_assessment   -> None
  emotions.distress_score            -> None

f-psych-summary  psychological_evaluation -> typeof dict
```

The one **real, previously-generated** cached analysis on disk (`backend/recordings/recording_1792905433007_34cee00c6252.json`) already uses the new keys, so historical data is broken too.

**Why the test suite never caught it:** `backend/tests/test_recordings.py` lines 55-105 mocks `analyze_call_forensics` using the **old** schema (`contact_number`, `amount_lost_inr`, `psychological_evaluation` as a *string*, …). The mock validates the frontend's expectations against itself, never against the real producer. This is a textbook case of a mock hiding a contract break.

**Fix (pick one):**
1. *Cheapest:* rename the 11 reads in `vault.js` to the backend keys, and render `psychological_evaluation.summary` (+ `dominant_state`), and use `vocal_distress_score`.
2. *Right:* define the dossier shape once (a Pydantic model in `backend/app/models.py` or a JSON Schema), validate the LLM output against it, and **derive** a golden fixture test from it so frontend and backend cannot drift again.

Also delete the stale mock and replace it with a contract test asserting the exact key set.

---

## P0-2 · A late AI triage result corrupts call state — a second officer can hijack a live call ✅ VERIFIED

`CallManager.update_call_triage()` unconditionally does `call.status = "WAITING"` (`backend/app/state.py:222`) with **no guard** on the current status.

This is not an edge case — it is the **normal happy path**:
1. `_async_triage_call` sets the call to `WAITING` *immediately* (`backend/app/websockets.py:25`) so triage is claimable in <10 ms.
2. An officer claims it (status → `ENGAGED`).
3. ~1-2 s later the AI result lands and `update_call_triage` reverts the status to `WAITING` **while leaving `officer_id` set**.

Reproduced against the real app:

```
[A] claim ok=True, status=ENGAGED, officer=OFFICER_01
[A] after late triage -> status=WAITING officer=OFFICER_01
[A] BUG: ENGAGED call reverted to WAITING; claimable by anyone? (True, 'Call claimed successfully.')
```

→ Two officers believing they own the same caller; both WebRTC offers relayed to one caller; two recordings; a torn-down call still on someone's screen.

Second, worse variant — **ended calls come back from the dead**:

```
[B] after end -> status=ENDED
[B] BUG: ENDED call resurrected to status=WAITING
```

A call that was hung up reappears in the officer queue and can be claimed, with no caller socket attached.

**Fix:** guard the transition, e.g.

```python
async with self.lock:
    call = self.calls.get(call_id)
    if not call or call.status in {"ENDED", "ENGAGED"}:
        # keep triage data, never the status
        ...update fields only, return...
    call.status = "WAITING"
```

and ideally model the lifecycle as an explicit FSM with an allowed-transition table.

---

## P0-3 · Unauthenticated, unmetered, unbounded AI triage endpoint ✅ VERIFIED

`backend/app/api.py:118-129`

```python
@router.post("/api/calls/{call_id}/triage")
async def upload_triage_audio(call_id: str, file: UploadFile = File(...)):
    call = call_manager.get_call(call_id)          # no auth, no ownership check
    audio_bytes = await file.read()                # whole body into RAM, no size cap
    asyncio.create_task(_async_triage_call(...))   # one task per request, no dedupe
```

```
[D] unauth triage upload -> HTTP 200 {"status":"triaging",...}
[D] 4 triage tasks spawned for the same call (no dedupe / no ownership check)
```

Each accepted request fans out to **three paid third-party APIs** (Sarvam ASR, AWS SER, Groq). Anyone who learns a `call_id` can loop this endpoint to burn API quota and memory, and can overwrite the triage verdict of another caller's session.

Additional problems in the same handler:
- `await file.read()` ignores `MAX_RECORDING_BYTES` (the recordings upload path enforces it; this one does not).
- No rate limit (only `/api/calls` and `/api/auth/login` are limited).
- No dedupe / idempotency — repeated uploads stack concurrent inferences.
- `asyncio.create_task` result is discarded → the task can be garbage-collected mid-flight (documented Python footgun).
- `_async_triage_call` is imported *inside* the function to dodge a circular import (`api.py:127`) — a smell that the triage service belongs in its own module.

**Fix:** require a caller capability (HMAC token minted with the session, or a per-call upload secret returned by `POST /api/calls`), enforce a byte cap while streaming, reject if a triage is already in flight for that `call_id`, rate-limit per call_id + IP, and keep a reference to the task.

---

## P0-4 · Weak default officer password, committed to git ✅ VERIFIED

`<redacted published value>` is the fallback password in `backend/app/config.py:58`, is baked into `render.yaml:13-16` (tracked by git), into `.env.example:9-10`, and into `backend/tests/test_security.py:33`.

```
$ git ls-files render.yaml
render.yaml
$ git grep -l "<redacted published value>"
.env.example
backend/app/config.py
backend/tests/test_security.py
render.yaml
```

Consequences:
- The production credential is public in the repository history. Anyone can log into the dispatch console and listen to victims / read forensic dossiers.
- A single shared password authenticates all three officer identities (`OFFICER_01..03`), so the audit trail (`officer_id` on every recording) is **self-asserted** — an officer picks their identity from a dropdown. It is not evidence.
- `config.py` defines `_PLACEHOLDER_TOKENS` (lines 36-45) for exactly this check, but **never uses it** — the hardening was designed and then not wired up.

**Fix:** delete the hardcoded fallbacks; fail fast at startup if `CERS_OFFICER_PASSWORD`/`CERS_OFFICER_TOKEN` are missing or match `_PLACEHOLDER_TOKENS`; move `render.yaml` credentials to `sync: false` env vars; give each officer their own credential (or at least a per-officer token list); rotate the password and token that are already in git history.

---

# P1 — High

## P1-1 · `IVR_FINISHED` sets status to `IVR`, so calls can become permanently unclaimable ✅ VERIFIED

`backend/app/websockets.py:60-62`

```python
elif action == "IVR_FINISHED":
    # Greeting ended, bot is now recording caller's emergency statement
    await call_manager.update_call_status(call_id, "IVR")     # ← should be WAITING (or a distinct RECORDING state)
```

Reproduced:

```
[E] after IVR_STARTED  = IVR
[E] after IVR_FINISHED = IVR   <-- e2e test + UI expect WAITING
```

`backend/tests/test_e2e_ws.py:82` asserts `WAITING` right after `IVR_FINISHED`, so **the project's own end-to-end test fails** against the current server. (It is not run by `pytest` — `testpaths = backend/tests` — which is why nobody noticed.)

User impact: in `frontend/officer/js/queue.js:282-285`, any `RINGING`/`IVR` call **disables the claim button** with "BOT RECORDING STATEMENT…". The only thing that rescues the call is the triage task setting `WAITING` — so if the caller hangs up before speaking, the upload fails, or the triage task dies, the call sits unclaimable until the 300 s abandoned-cleanup (`state.py:429`).

**Fix:** set `WAITING` (or add an explicit `RECORDING` status that is still claimable) and add a `pytest`-collected integration test for the IVR lifecycle. Move `test_e2e_ws.py` under `backend/tests/` behind a marker.

---

## P1-2 · Anyone with a `call_id` can take over a live, engaged call ✅ VERIFIED

`backend/app/state.py:118-138` — the docstring promises something the code does not do:

```python
"""A caller may reconnect, but the call state is never reset and a
second caller cannot take over an engaged call."""
...
if not call or call.status == "ENDED":
    return False
previous = self.caller_sockets.get(call_id)
self.caller_sockets[call_id] = websocket      # ← replaces, even when ENGAGED
```

Reproduced:

```
[F] second caller attached to ENGAGED call -> status=ENGAGED, socket replaced=True
```

The legitimate caller's socket is closed with code `4001` and the impostor inherits the audio path. The `call_id` is a UUID and is the *only* secret, so any leak (browser history, shared link, log line, referrer) hands over a live call. At minimum the comment is wrong; at worst it is an unauthenticated takeover of a victim's call with a police officer.

**Fix:** allow reconnect only for the existing caller identity (bind a signed reconnect token at session creation, or a short-lived cookie), and refuse replacement when `status == "ENGAGED"`.

---

## P1-3 · Non-ASCII password causes a 500 instead of 401 ✅ VERIFIED

`backend/app/api.py:44-45`

```python
valid_password = secrets.compare_digest(payload.token, OFFICER_PASSWORD)
```

`secrets.compare_digest` raises `TypeError` for `str` containing non-ASCII. Reproduced:

```
File "backend/app/api.py", line 44, in login
TypeError: comparing strings with non-ASCII characters is not supported
```

Any login attempt containing a non-ASCII character (very likely for Indian users typing a password) returns HTTP 500 and a stack trace in logs, instead of 401.

**Fix:** `secrets.compare_digest(payload.token.encode(), OFFICER_PASSWORD.encode())`.

---

## P1-4 · Caller UI's in-call screen is missing two of its five stylesheets ✅ VERIFIED

`frontend/caller/index.html:10-12` links `base.css`, `dialpad.css`, `incall.css` only. But `frontend/caller/css/visualizer.css` and `frontend/caller/css/controls.css` exist and define the styling for elements the page **actually uses**:

```
#caller-visualizer, .visualizer-wrapper, .visualizer-caption,
.skip-ivr-btn, .officer-card, .officer-avatar, .officer-details,
.officer-name, .officer-sub, .officer-title
```

None of those selectors exist in any linked stylesheet. So the visualizer box, its caption, the `[ SKIP ADVISORY & SPEAK NOW ]` button and the "ASSIGNED DISPATCHER" card render completely unstyled.

**Fix:** add the two `<link>` tags (or merge the files). Add a CI check that every `.css` under `frontend/**/css` is referenced by an HTML file.

---

## P1-5 · Caller's microphone is routed to the speakers — echo / howling feedback ✅ VERIFIED (code)

`frontend/caller/js/vad.js:78-80`

```js
pcmSourceNode.connect(pcmProcessorNode);
// Connect to destination so onaudioprocess fires reliably across browsers
pcmProcessorNode.connect(audioCtx.destination);
```

A `ScriptProcessorNode` passes its input to its output, so this plays the caller's microphone **out loud through their speakers** while they record their emergency statement. On a phone/laptop without headphones this is an audible echo of their own voice and, with the officer's audio also playing through `remoteAudio`, a feedback loop.

Also: `createScriptProcessor` is deprecated and runs on the main thread (glitches under load); `AudioWorklet` is the supported replacement.

**Fix:** route the processor into a `GainNode` with `gain.value = 0` connected to `destination` (keeps the callback firing, stays silent), and migrate to `AudioWorkletNode`.

---

## P1-6 · Test run writes a fake recording into the production vault ✅ VERIFIED

`backend/tests/test_recordings.py::test_sync_from_supabase_with_mock` calls the real `_sync_from_supabase_sync(force=True)`, which writes into the real `backend/recordings/` directory. On disk right now:

```json
{
  "id": "1792855139085_test123",
  "filename": "recording_1792855139085_test123.webm",
  "url": "https://mock.supabase.co/recording_test.webm",
  "notes": "Recovered from Supabase Cloud Storage"
}
```

That ghost entry appears in the officer recordings vault, points at a non-existent mock URL, and can never be played or deleted (there is no delete endpoint). Tests must not touch production data paths.

**Fix:** monkeypatch `RECORDINGS_DIR` to `tmp_path` in a fixture; delete the leaked file.

---

## P1-7 · The Supabase vault backend is configured but unreachable — and retries on every single list ✅ VERIFIED

```
SUPABASE_URL configured: True
DNS resolve: FAILED in 0.10s -> gaierror: [Errno 11001] getaddrinfo failed
list #1: 31 recordings in 0.45s
list #2: 31 recordings in 0.02s
```

Every recording upload therefore pays a failed network round-trip, and every `GET /api/recordings` retries the sync because `_last_supabase_sync` is only advanced on success (`backend/app/recordings.py:158`), while the guard at lines 85-87 only short-circuits when *local metadata already exists and the last sync was <15 s ago*. With a hanging (rather than fast-failing) host this becomes a multi-second stall on the vault.

Three separate defects here:
1. `_last_supabase_sync` is never updated on failure → no backoff, retry storm.
2. `list()` on Supabase Storage is **paginated (100 items default)** and is called with no pagination loop → the vault silently truncates once you exceed 100 objects. `recordings.py:93`.
3. `_sync_from_supabase_sync` is a module-level global with no lock, called both from the startup task and from inside `_list_recordings_sync` (which runs in a threadpool) — concurrent invocations can interleave writes.

**Fix:** add exponential backoff, paginate the listing, guard the sync with a `threading.Lock`, and surface storage health in `/health` instead of a `print`.

---

## P1-8 · 16 of 31 archived recordings have no audio file

```
.json: 31 files      .webm: 15 files
ORPHAN META (no audio): 16 files
```

`_list_recordings_sync` happily returns metadata whose `url` points at `/api/recordings/<missing>`, which 404s (or redirects to the dead Supabase bucket). Officers see entries that cannot be played. Combined with P1-6 there is no cleanup/purge path at all — there is no `DELETE /api/recordings/{...}` endpoint.

**Fix:** filter or flag orphaned metadata in the list response; add a delete/retention endpoint; reconcile on startup.

---

# P2 — Medium

| # | Finding | Where | Notes |
|---|---|---|---|
| **M1** | **HTML injection from LLM output.** `officer_sop` (LLM-generated from a caller-controlled transcript) and `emotions.dominant` are interpolated into `innerHTML`. | `frontend/officer/js/queue.js:213,233` | The CSP (`script-src 'self'`, no `unsafe-inline`) blocks script execution, so this is not script XSS — but `style-src` **does** allow `unsafe-inline`, so injected `<div style="position:fixed;inset:0…">` can spoof the officer console. Use `textContent` + `<br>` via DOM nodes, or sanitise. |
| **M2** | **Fire-and-forget startup task.** `asyncio.create_task(run_in_threadpool(...))` is never stored, awaited, or cancelled. | `backend/app/main.py:44` | On shutdown it can outlive the loop ("Task was destroyed but it is pending"); its exceptions are invisible. Observed in tests: every app start performs a real network call. Store the task, cancel it in `finally`, log exceptions. |
| **M3** | **Acoustic features silently degrade on any non-16 kHz-mono-PCM input.** `extract_acoustic_features` assumes `wave.open` + `struct.unpack("<{n_frames}h")`. Any stereo/24-bit/WebM upload raises and falls back to hardcoded `rms=0.05, zcr=0.05, loudness=1.0`. | `backend/app/ai.py:109-139`, `triage_call_audio:449` | The caller path does send proper 16 kHz mono WAV, so the happy path works — but the API accepts any `content_type` and writes it to a `.wav` file regardless, and the failure is only a `logger.warning`. Validate/convert with `ffmpeg`/`soundfile`, or reject non-WAV with 415. |
| **M4** | **Non-AWS emotion scores are fabricated, not measured.** If the AWS SER service is down, `synthesize_fused_emotions` returns a *hardcoded profile selected by incident category*, plus `random.uniform(-0.02, 0.02)` jitter. The same call produces different "fear" values on each run, and a calm caller describing an accident always reads ~88% fear. | `backend/app/ai.py:339-444` | The UI presents these as "Acoustic Vocal Emotion Spectrum" with a % meter. This is the project's biggest **responsible-AI** exposure: the numbers look measured but are a lookup table. Either (a) label the fallback as "estimated from incident category" in the UI, or (b) return `unavailable` and hide the meters. Also: `incident_severity` and `transcript` params are accepted and never used. |
| **M5** | **Client/server clock skew breaks the duration ticker.** The officer UI recomputes elapsed time as `Date.now()/1000 - call.engaged_at` using the *server's* timestamp with the *client's* clock. | `frontend/officer/js/queue.js:319-331` | A client clock 2 min fast shows `00:00` forever; 2 min slow shows absurd durations. Use the server-computed `duration` field (already in `to_dict()`) and add local elapsed time since receipt. |
| **M6** | **No per-socket send serialization.** `broadcast_to_officers` and `relay_webrtc` can `send_json` on the same WebSocket from different tasks (broadcast + claim result + relay). | `backend/app/state.py:396-414` | ASGI sends are not documented as concurrency-safe; interleaved frames can corrupt the stream. Add an `asyncio.Lock` per socket. |
| **M7** | **Rate limits are IP-only, no lockout, no per-call limit.** | `backend/app/security.py:89-94` | Behind a proxy all clients share one key unless `--proxy-headers`/`forwarded_allow_ips` is honoured; a correct deployment is untested. Add per-officer and per-call buckets, and exponential lockout after N failed logins. |
| **M8** | **Blocking I/O inside `async def`.** `analyze_recording` does `open()/json.load()` on the event loop; `logout`/`records` are fine but this one isn't. | `backend/app/recordings.py:410-411` | Move to `run_in_threadpool` like the neighbouring calls do. |
| **M9** | **Unvalidated filename from metadata JSON.** `audio_path = RECORDINGS_DIR / recording.get("filename")`, never regex-checked (unlike every HTTP-supplied name). | `backend/app/recordings.py:426` | Metadata is server-written today, but it is also *synthesised from remote Supabase object names* and editable on disk — a `../../` value would make the SER call read an arbitrary file. Apply `AUDIO_NAME_RE` + `_safe_recording_path`. |
| **M10** | **Caller socket drop ends the call.** `unregister_caller` calls `end_call` on any disconnect, including a transient network blip. | `backend/app/state.py:140-150` | The caller client retries after 1.2 s, but the session is already `ENDED`, so the reconnect is rejected. Add a grace period (e.g. 15 s) before ending, keyed so a fast reconnect cancels it. |
| **M11** | **Unused `tmp_path` fixture breaks the test in sandboxed/temp-restricted environments.** | `backend/tests/test_recordings.py:54` | The fixture is requested but never used; merely requesting it forces temp-dir creation and teardown, which failed here with `PermissionError`. Drop the argument. (This is the single test error in the run — **not** a product bug.) |
| **M12** | **Dead configuration.** `HUGGINGFACE_MODEL` is defined and documented but imported by nothing — the model actually used is whatever the remote HF Space serves. | `backend/app/config.py:95-97` | Remove, or wire it through. Same for `_PLACEHOLDER_TOKENS` (P0-4) and `MAX_ACTIVE_CALLS`'s duplicated default (`state.py:88` hardcodes `500` while `api.py:89` passes the config value). |
| **M13** | **Unpinned dependencies.** Every requirement uses `>=`. | `requirements.txt`, `aws_service/requirements.txt` | `fastapi>=0.110` will pull future majors; `torch>=2.2` is a multi-GB float. Pin with `==` or a lockfile (`uv`/`pip-tools`). |
| **M14** | **No HSTS, and `connect-src ws: wss:` is scheme-wide.** | `backend/app/security.py:105-118` | Add `Strict-Transport-Security` when `COOKIE_SECURE=1`. Narrow `connect-src` to `'self'` + the Supabase origin + the explicit `wss://` host. |

---

# P3 — Polish & hygiene

| # | Finding | Where |
|---|---|---|
| P3-1 | **Dead CSS** `visualizer.css`, `controls.css` unreferenced (root cause of P1-4). | `frontend/caller/css/` |
| P3-2 | **Dead asset** `frontend/static/hold_music.wav` — the page uses `hold_music.mp3`. | `frontend/static/` |
| P3-3 | **52.8 MB `cloudflared.exe`** and generated `temp/` artifacts sitting in the project. | `temp/` |
| P3-4 | **5.2 MB unused PNG** at repo root (source art, not referenced anywhere). | `Gemini_Generated_Image_*.png` |
| P3-5 | **Unused imports:** `io`, `Path`, `BASE_DIR` in `ai.py`; `secrets` in `config.py`; `math`, `struct` in `aws_service/app.py`; `classify_incident_semantics`, `extract_acoustic_features` in `create_and_test_emotions.py`; `pytest` in `test_recordings.py`. | various |
| P3-6 | **`print()` used as logging** across backend (should be `logging`/`structlog`, so level filtering and request IDs work). | `main.py:34`, `websockets.py`, `recordings.py` |
| P3-7 | **14 `alert()` calls** for errors and even for *success* ("Officer dispatch notes saved."). Blocking, unstyleable, bad on mobile. | `frontend/**/js` |
| P3-8 | **`user-scalable=no, maximum-scale=1.0`** blocks zoom — WCAG 1.4.4 failure on the citizen-facing page. | `frontend/caller/index.html:5` |
| P3-9 | **Queue re-renders wholesale on every WS event** — destroys focus, scroll position and the DOM on each `STATUS_CHANGED`/duration tick. | `frontend/officer/js/queue.js:32` |
| P3-10 | **Docs drift:** README says `qwen-2.5-32b`, config and `.env.example` say `qwen/qwen3.8-27b` (the latter is what actually works — verified live). README claims "32 tests"; `testpaths` excludes `test_e2e_ws.py`. | `README.md:78,185` |
| P3-11 | **`_valid_call_id`** rejects UUID v4 only — fine, but the check runs before `accept()`, so the client sees a bare 1008 with no reason. | `websockets.py:15-19` |
| P3-12 | **No `DELETE` endpoint** for recordings; evidence can never be purged (a real compliance requirement for call recordings). | `recordings.py` |
| P3-13 | **`/officer` page itself is unauthenticated** — the whole console UI/JS is public. Data is gated, so not a leak, but the auth gate is client-side only. Consider a server-side redirect for the HTML too. | `pages.py:21` |

---

# What is actually good

Worth saying, because a lot of this codebase is solid:

- **The tests that exist are real tests**, not smoke tests — atomic claim concurrency (`asyncio.gather` over 10 officers), stale-socket cleanup, idempotent end-call, traversal attempts through encoded backslashes. That is better than most projects this size.
- **Path-traversal defence is correct and layered**: regex allowlist (`AUDIO_NAME_RE`), `.resolve()` + parent check (`_safe_recording_path`), and static mounts instead of request-built paths. The `main.py:82-84` comment shows someone learned this the hard way and fixed it properly.
- **Security headers / CSP / HttpOnly+SameSite cookie / HMAC-signed sessions with expiry** — the auth primitives are right, they're just undermined by the default password.
- **Streaming uploads with a byte cap** in `upload_recording` (`recordings.py:269-283`) — chunked, aborts and cleans up the `.part` file.
- **Atomic `.tmp` + `os.replace`** for JSON metadata writes.
- **The AI pipeline genuinely works** — live Groq forensic extraction produced a correct, structured dossier in 1.8 s. The problem is purely the handoff to the UI.
- **Frontend is cleanly modular** (ES modules, event bus to avoid circular imports, no missing imports, no unresolved DOM ids across 122 `getElementById` bindings, zero JS syntax errors).

---

# Suggested fix order

| Sprint | Items |
|---|---|
| **1 — stop the bleeding** | P0-1 (map keys), P0-2 (status guard), P0-3 (auth + cap + dedupe), P0-4 (remove default password, rotate secrets) |
| **2 — correctness** | P1-1 (IVR status), P1-2 (reconnect token), P1-3 (`compare_digest` encode), P1-4 (link CSS), P1-5 (VAD gain node), P1-6 (test isolation) |
| **3 — robustness** | P1-7 (sync backoff + pagination), P1-8 (orphan filter + delete), M1 (textContent), M2 (task lifetime), M6 (send lock), M10 (disconnect grace) |
| **4 — hygiene** | M3/M4 (audio + ethics labelling), M11-M14, P3-1…P3-13 |

**Regression protection to add first** (each of these would have caught a P0/P1 above):

1. A **contract test** that runs `analyze_call_forensics` with a stubbed Groq response and asserts the exact returned key set — the frontend keys derived from the same constant.
2. A **state-machine test**: claim → `update_call_triage` → assert status is still `ENGAGED`; end → `update_call_triage` → assert still `ENDED`.
3. An **auth test** for `POST /api/calls/{id}/triage` (expect 401/403 anonymously) and a size-cap test.
4. Move `test_e2e_ws.py` into `backend/tests/` under an `@pytest.mark.e2e` marker so the IVR lifecycle assertion actually runs.
5. A **link checker** for `frontend/**/css` and `static/**` referenced from HTML.
