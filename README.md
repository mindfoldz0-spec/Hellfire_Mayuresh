# ⚡ CERS: Cyber Emergency Response System (Vecnaverse)

A real-time emergency dispatch console and voice communication platform built for rapid response to cyber threats (ransomware, DDoS, wire fraud, identity hijacking). Features full-duplex WebRTC 2-way voice dispatch, automated AI IVR greeting, multi-officer atomic queue lockout, dual-audio session recording, and a retro CRT terminal intelligence analysis vault.

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
 │ Retro CRT Taskbar & Forensics Vault  │
 └──────────────────────────────────────┘
```

---

## ✨ Features

- **Mobile Caller Web Dialer (`/caller`)**:
  - DTMF tone audio feedback for keypad dialing.
  - Automated playback of the emergency IVR greeting.
  - Mic auto un-mutes precisely after the 1000Hz sync tone ends.
  - Live canvas frequency oscilloscope.

- **Officer Dispatch Console (`/officer`)**:
  - Retro Terminal / 90s OS style aesthetic with `VT323` monospace typography and high-contrast pixel frames.
  - Multi-officer concurrent dispatch queue with real-time atomic claim lockout (`asyncio.Lock()`) — prevents multiple officers from connecting to the same emergency caller.
  - Dual WebRTC audio visualizers (Incoming Caller Stream vs. Officer Microphone).
  - In-browser mixed dual-audio session recorder with automated backend upload upon call termination.

- **Call Forensics & Intelligence Vault**:
  - Retro window modal accessed from the bottom OS taskbar or top command bar.
  - Instant session audio playback of recorded emergency calls.
  - Extracted incident key data: Reported Location / Place, Threat Classification, Ransom Demanded, Countdown Deadline.
  - Diarized AI transcript with interactive **Click-to-Seek** audio scrubbing.
  - Incident log editor with dossier export actions.

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.10+
- Node.js (optional, for tooling)

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/mindfoldz0-spec/Vecnaverse.git
cd Vecnaverse

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Setup
Copy `.env.example` to `.env` and set your Sarvam AI API key:
```ini
sarvam_api=your_sarvam_api_key_here
```

### 4. Generate Emergency IVR Audio
Synthesize the automated greeting prompt combined with a 1000Hz synchronization tone:
```bash
python generate_ivr.py
```
*(The synthesized file will be saved directly to `frontend/static/ivr_prompt.wav`)*

### 5. Start the Server
```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Officer Console**: [http://localhost:8000/officer](http://localhost:8000/officer)
- **Caller Web Dialer**: [http://localhost:8000/caller](http://localhost:8000/caller)

*(For mobile testing, access via your machine's LAN IP: `http://<YOUR_LAN_IP>:8000/caller`)*

---

## 🧪 Testing & Verification

Run the automated test suite:

```bash
# Concurrency & Atomic Lockout Tests (10 Simultaneous Officers)
python -m pytest backend/tests/test_state.py -v

# Full WebSocket & WebRTC Signaling Integration Test
python test_e2e_ws.py
```

---

## 📄 License
MIT License. Created for Cyber Emergency Response Operations.
