# ⚡ CERS: Cyber Emergency Response System (Hellfire_Mayuresh)

A real-time emergency dispatch console and voice communication platform built for rapid response to cyber threats (ransomware, DDoS, wire fraud, identity hijacking). Features full-duplex WebRTC 2-way voice dispatch, automated AI IVR greeting, multi-officer atomic queue lockout, dual-audio session recording, and an archived call recordings vault.

---

## 🛠️ System Architecture

```
[ Victim / Caller Web Dialer ]
       │  (HTTPS / Mobile-Friendly DTMF)
       ▼
[ Automated IVR Greeting + 1000Hz Sync Tone ]
       │  (Sarvam AI TTS bulbul:v3 + Wave Sine Tone)
       ▼
[ WebSocket Gateway & WebRTC Signaling Relay ]
       │  (FastAPI + asyncio.Lock Atomic Claim Dispatch)
       ▼
[ Officer Dispatch Consoles ] ──► [ Atomic Lockout Rejection to Concurrent Officers ]
       │
 ┌─────┴────────────────────────────────┐
 │ 2-Way Opus WebRTC Audio Stream       │
 │ Dual-Track Oscilloscope Visualizers  │
 │ In-Browser Dual-Audio Recorder       │
 │ Retro CRT Taskbar & Recordings Vault │
 └──────────────────────────────────────┘
```

---

## ✨ Features

- **Mobile Caller Web Dialer (`/caller`)**:
  - DTMF tone audio feedback for keypad dialing.
  - Automated playback of the emergency IVR greeting with a timeout/skip fallback.
  - Mic auto un-mutes after the 1000Hz sync tone (or immediately if audio is blocked).
  - Live canvas frequency oscilloscope.
  - Server-issued, unguessable call session IDs (no shared `2007` collision).

- **Officer Dispatch Console (`/officer`)**:
  - Signed HttpOnly officer sessions; the console requires an access token.
  - Retro Terminal / 90s OS style aesthetic with `VT323` monospace typography.
  - Multi-officer concurrent dispatch queue with real-time atomic claim lockout (`asyncio.Lock()`).
  - One active call per officer; calls are released back to the queue on disconnect.
  - Dual WebRTC audio visualizers (Incoming Caller Stream vs. Officer Microphone).
  - In-browser mixed dual-audio session recorder with automated backend upload upon call termination.

- **Call Recordings Vault**:
  - Retro window modal accessed from the bottom OS taskbar or top command bar.
  - Instant session dual-audio playback of recorded emergency calls.
  - Authenticated audio download capability.
  - Technical session telemetry: gateway, audio encoding, file size, and call duration.
  - Officer dispatch log and case notes editor with persistence.

---

## 🗂️ Project Structure

```
backend/
  app/
    main.py          # FastAPI app factory, middleware, lifespan, static mounts
    config.py        # environment variables, paths, limits, officer token
    security.py      # signed sessions, auth dependencies, rate limits, headers
    models.py        # Pydantic request models
    api.py           # officer auth and caller HTTP APIs
    recordings.py    # authenticated recording upload, playback, and notes API
    websockets.py    # caller/officer WebSocket signaling routers
    pages.py         # HTML page routes and /health
    state.py         # in-memory call manager and atomic claim logic
  tests/             # pytest suite (conftest sets deterministic auth env)
frontend/
  caller/
    index.html
    css/             # base, dialpad, incall, visualizer, controls
    js/              # state, dom, utils, api, ws, webrtc, ivr, ui, call, main
  officer/
    index.html
    css/             # base, command-bar, layout, queue, station, taskbar, vault, auth
    js/              # state, dom, events, utils, api, auth, websocket, queue, call, vault, ui, main
  static/            # IVR prompt and shared assets
  package.json       # marks frontend JS as ES modules for node --check
```

Frontend scripts are native ES modules. The officer console uses a small
`CustomEvent` bus for cross-feature actions (`ws:send`, `ws:reconnect`,
`auth:required`, `auth:show`, `vault:refresh`) so feature modules stay
decoupled and free of circular imports.

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.10+
- Node.js (optional, for tooling)

### 2. Installation
```bash
git clone https://github.com/mindfoldz0-spec/Hellfire_Mayuresh.git
cd Hellfire_Mayuresh
pip install -r requirements.txt
```

### 3. Environment Setup
Copy `.env.example` to `.env` and configure at least:

```ini
sarvam_api=your_sarvam_api_key_here
CERS_OFFICER_TOKEN=replace-with-a-long-random-token
```

Generate a strong officer token with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

If `CERS_OFFICER_TOKEN` is omitted, the server generates a random token at startup and prints it in the console. Set it explicitly for a stable login across restarts.

### 4. Generate Emergency IVR Audio
Synthesize the automated greeting prompt combined with a 1000Hz synchronization tone:

```bash
python generate_ivr.py
```

The synthesized file is written to `frontend/static/ivr_prompt.wav`.

### 5. Start the Server
```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

- **Officer Console**: [http://localhost:8000/officer](http://localhost:8000/officer) — enter the officer access token at the login gate.
- **Caller Web Dialer**: [http://localhost:8000/caller](http://localhost:8000/caller)

### 6. Mobile / HTTPS
Browsers only allow microphone access in a secure context. `http://<LAN_IP>:8000` will **not** work on mobile. Use HTTPS with a certificate trusted by the device (for example a local reverse proxy, Caddy, or `uvicorn --ssl-keyfile ... --ssl-certfile ...`). For calls across restrictive NATs, configure TURN:

```ini
CERS_TURN_URL=turn:turn.example.com:3478
CERS_TURN_USERNAME=cers
CERS_TURN_CREDENTIAL=replace-me
CERS_COOKIE_SECURE=1
```

---

## 🔐 Security Model

- Officer console and recording APIs require a signed, HttpOnly session cookie created by `POST /api/auth/login`.
- The officer access token is compared in constant time; login attempts and call creation are rate-limited.
- Caller sessions are server-generated UUIDs. The dialed number is display metadata only.
- Recording uploads are authenticated, size-limited, extension-restricted, and stored with server-generated filenames.
- Recordings are served through an authenticated API, not a public static mount.
- Calls are released automatically when an officer disconnects; only the assigned officer can end or release a call.
- Static file routes are mounted with Starlette's `StaticFiles`; request path parameters are never joined into filesystem paths.
- Security headers include a Content-Security-Policy, `X-Content-Type-Options`, `X-Frame-Options`, and `Permissions-Policy`.
- CORS is same-origin by default. Set `CERS_ALLOWED_ORIGINS` only if the frontend is hosted elsewhere.

---

## 🧪 Testing & Verification

Run the automated test suite:

```bash
# State, concurrency, disconnect, ownership, and security regression tests
python -m pytest -v
```

Run the full WebSocket & WebRTC signaling integration test (start the server first and set the same token):

```bash
$env:CERS_OFFICER_TOKEN = "your-token"   # PowerShell
python test_e2e_ws.py
```

---

## ⚙️ Configuration Reference

| Variable | Default | Purpose |
|---|---|---|
| `sarvam_api` | — | Sarvam AI key for IVR generation |
| `CERS_OFFICER_TOKEN` | generated | Officer console access token |
| `CERS_SECRET_KEY` | derived | HMAC key for session cookies |
| `CERS_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS |
| `CERS_ALLOWED_ORIGINS` | empty | Comma-separated CORS allowlist |
| `CERS_TURN_URL` | empty | TURN server URL(s), comma-separated |
| `CERS_TURN_USERNAME` | empty | TURN username |
| `CERS_TURN_CREDENTIAL` | empty | TURN credential |
| `CERS_MAX_RECORDING_BYTES` | 25 MB | Upload size limit |
| `CERS_MAX_ACTIVE_CALLS` | 500 | Active call capacity |
| `CERS_CALL_TTL_SECONDS` | 3600 | Retention of ended call state |

---

## 📄 License
MIT License. Created for Cyber Emergency Response Operations.
