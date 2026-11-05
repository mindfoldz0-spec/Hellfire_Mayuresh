# 🚨 CERS: Cyber Emergency Response System
### Real-Time Speech Emotion Analysis & Incident Triage for Cybercrime Investigation

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![WebRTC](https://img.shields.io/badge/Audio-WebRTC%20Opus%2048kHz-333333?logo=webrtc&logoColor=white)](https://webrtc.org/)
[![AWS EC2](https://img.shields.io/badge/SER%20Microservice-AWS%20EC2%20c7i--flex.large-FF9900?logo=amazon-aws&logoColor=white)](https://aws.amazon.com/)
[![Groq](https://img.shields.io/badge/LLM%20Inference-Groq%20Qwen3.8--27b-F55036?logo=groq&logoColor=white)](https://groq.com/)
[![Sarvam AI](https://img.shields.io/badge/Indic%20Speech-Sarvam%20AI%20saaras%3Av4-6366F1)](https://www.sarvam.ai/)
[![Supabase](https://img.shields.io/badge/Vault%20Storage-Supabase%20S3-3ECF8E?logo=supabase&logoColor=white)](https://supabase.com/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## 📌 1. What is CERS?

**CERS (Cyber Emergency Response System)** is an AI-powered emergency dispatch console and forensic intelligence platform built specifically for **Cybercrime Police Stations** and emergency hotlines. 

When victims of cybercrime (such as **Digital Arrest extortion**, **phishing**, **UPI bank draining**, or **cyber blackmail**) call the helpline, emergency officers must quickly assess life distress, identify impostors, and prioritize critical cases under intense call pressure.

CERS solves this challenge by fusing **Real-Time Speech Emotion Recognition (SER)** with **Multilingual Incident Semantic Triage**:
1. **Live Speech Emotion Analysis**: Detects vocal tremors, panic, fear, anger, distress, and jitter directly from caller audio in real time.
2. **Dynamic 60/40 Call Prioritization**: Ranks incoming calls on an active queue (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) using a composite priority score (1–100).
3. **Forensic Call Dossier Vault**: Automatically transcribes calls across Hindi, Marathi, and English, extracts key spoken entities (Caller name, suspect impersonation, phone numbers, fake UPI IDs, amounts lost/demanded), and generates immediate law enforcement SOPs and FIR checklists.

---

## 🏗️ 2. High-Level Architecture

```
                       [ 📱 Distressed Caller ]
                                  │
                                  ▼ (WebRTC Dual-Audio / DTMF)
            ┌───────────────────────────────────────────────┐
            │       CERS Dispatch Gateway (Render / FastAPI)│
            └───────┬───────────────────────────────┬───────┘
                    │                               │
       (Live Indic Speech Stream)       (Audio WAV Stream)
                    ▼                               ▼
     ┌────────────────────────────┐   ┌────────────────────────────┐
     │      Sarvam AI Cloud       │   │  Dedicated AWS SER Server  │
     │   STT: saaras:v4 (Indic)   │   │  EC2: c7i-flex.large       │
     │   TTS: bulbul:v3 (IVR)     │   │  Model: Wav2Vec2 XLS-R     │
     └──────────────┬─────────────┘   └─────────────┬──────────────┘
                    │                               │
                    │  (Transcript + Emotion Stats) │
                    ▼                               ▼
            ┌───────────────────────────────────────────────┐
            │        Groq Cloud LPU (qwen3.8-27b)           │
            │  • Multilingual Translation (Marathi/Hindi)   │
            │  • 60/40 Composite Priority Fusion (1-100)    │
            │  • Entity Extraction (UPI, Bank, Suspect)     │
            │  • Police Investigation SOP & FIR Checklist   │
            └───────────────────────┬───────────────────────┘
                                    │
                                    ▼ (Authenticated WebSocket)
            ┌───────────────────────────────────────────────┐
            │     👮 Officer Retro Dispatch Console         │
            │  • Atomic Call Lockout Queue                  │
            │  • Live Dual-Track Audio Oscilloscopes        │
            │  • Deep AI Forensic & Emotion Vault           │
            └───────────────────────────────────────────────┘
                                    │
                                    ▼ (Archived Sessions)
            ┌───────────────────────────────────────────────┐
            │         Supabase Cloud S3 Storage             │
            │  • Opus Audio Dual-Stream Recordings          │
            │  • JSON Forensic Intelligence Metadata        │
            └───────────────────────────────────────────────┘
```

---

## ⚙️ 3. Technologies & Models Used (Where & Why)

| Component / Layer | Technology / Model Used | Where It Is Deployed / Hosted | Exact Role in CERS |
|---|---|---|---|
| **Speech Emotion Recognition (SER)** | **`Wav2Vec2 XLS-R 300M`** + Acoustic Prosody (RMS Loudness, Zero-Crossing, Pitch Tremor) | **AWS EC2 (Mumbai `ap-south-1`)** – private microservice, bearer-authenticated | Analyzes vocal acoustics and neural emotion embeddings in ~740ms. Quantifies **Fear, Distress, Anger, Nervousness/Jitter, and Neutrality** without human bias. |
| **Multilingual Cyber Triage & Forensics** | **`qwen/qwen3.8-27b`** via **Groq LPU Acceleration** | **Groq Cloud Infrastructure** | Runs sub-second semantic triage. Translates Hindi and Marathi emergency statements into English, extracts criminal entities (UPI VPAs, bank accounts, suspect names, phone numbers), and drafts law enforcement SOP checklists. |
| **Indic Speech-to-Text (STT)** | **`saaras:v4`** | **Sarvam AI Cloud** | State-of-the-art automatic speech recognition supporting code-mixed Indian English, Hindi, and Marathi with sub-400ms turnaround. |
| **Automated Police IVR Greeting** | **`bulbul:v3`** + 1000Hz Wave Tone Generator | **Sarvam AI Cloud** / Local Generator | Synthesizes official cyber helpline voice greeting combined with automated 1000Hz beep tone to guide callers. |
| **Emergency Dispatch Web Gateway** | **FastAPI + Uvicorn (Python 3.10+)** | **Render Cloud PaaS** / Local Server | Serves static assets, manages WebSocket signaling, enforces `asyncio.Lock()` atomic officer call claims, and handles authenticated APIs. |
| **Real-Time Voice Streaming** | **WebRTC (Opus 48kHz Stereo)** | Google STUN Cluster (`stun.l.google.com:19302`) | Provides full-duplex, low-latency, encrypted peer-to-peer audio communication between victim and officer. |
| **Persistent Evidence Storage** | **Supabase Storage (S3-Compatible)** | **Supabase Cloud (`ap-southeast-1`)** | Securely archives dual-channel `.webm` call recordings and `.json` forensic metadata dossiers with presigned playback URLs. |

---

## 🌟 4. Core Features

### 📞 1. Citizen Emergency Caller (`/caller`)
- **Keypad DTMF Feedback**: Realistic dialing tones for emergency numbers.
- **Automated IVR Guidance**: Plays police warning prompt followed by a 1000Hz tone before unmuting caller microphone.
- **Voice Activity Detection (VAD)**: In-browser energy VAD detects speech pauses and streams audio segments cleanly.
- **Visible recording consent**: the dialer states that the call is recorded and used as police evidence before the caller proceeds.
- **Spoken language**: English / Hindi / Marathi selection drives the IVR greeting and the per-call ASR language.
- **Statement window with countdown**: a visible timer warns before the 10s window closes, and the caller can add ten seconds at a time (button or `*`).
- **Typed-statement fallback**: if the microphone is denied or unavailable, the caller can type the report instead of hitting a dead end; it is triaged and queued the same way.
- **Live Frequency Oscilloscope**: Visual feedback of outgoing audio on a CRT canvas.

### 👮 2. Officer Dispatch Console (`/officer`)
- **Retro Terminal Aesthetic**: Cyberpunk / 90s OS workstation UI styled with `VT323` monospace typography and live scanlines.
- **Multi-Officer Atomic Queue**: `asyncio.Lock()` prevents race conditions; two officers can never accidentally pick up the same caller.
- **Real-Time Priority Sorting**: Calls are automatically ordered by the 60/40 Composite Priority score (1–100).
- **Escalation alarm**: a call scoring >= 85 that nobody claims within 30s is flagged, re-alerted with a repeating tone, and highlighted until it is picked up.
- **Live re-triage**: while engaged, the console re-scores the call from live caller audio every 5 seconds and streams the running transcript into the station panel.
- **Dual-channel recording**: separate officer and caller tracks plus a mixed reference, each SHA-256 hashed and chained to the previous record, with one-click verification and purge.
- **Printable case packet**: `GET /api/recordings/{meta}/packet.pdf` renders the dossier, SOP and evidence hashes as an A4 document for the case file.
- **Dispatch metrics panel**: call volume, median/p90 time-to-claim per priority band, incident mix, heuristic-fallback rate and per-provider health.
- **Keyboard control**: `A` accept, `V` vault, `I` metrics, `T` night theme, `M` mute, `Esc` end call.
- **Night theme**: high-contrast tokens for overnight shifts, honouring `prefers-reduced-motion`.
- **Dual Visualizers**: Real-time canvas visualizers comparing incoming caller distress wave vs. officer microphone audio.

### 🛡️ 3. Deep Post-Call Forensic & Emotion Vault
Selecting any recorded call in the archive allows officers to click **`[ 🔍 FULL AI INCIDENT & EMOTION AUDIT ]`**:
1. **Multi-Sensory Emotion Spectrum**:
   - 🔴 **Fear Meter** (`0% - 100%`)
   - 🟣 **Distress Meter** (`0% - 100%`)
   - 🟠 **Anger Meter** (`0% - 100%`)
   - 🟡 **Jitter / Tremor Meter** (`0% - 100%`)
   - ⚪ **Calm / Neutral Meter** (`0% - 100%`)
   - Dominant emotion badge & Acoustic Distress Score.
2. **Victim / Caller Dossier**: Extracted Name, Contact number, City/Location, and Dialect.
3. **Suspect Traces**: Impersonated Agency (CBI, FedEx, Police), claimed identity, phone numbers, and digital handles.
4. **Financial Impact**: Exact INR amount debited/demanded (highlighted in bold red), fraudulent destination UPI VPAs, and compromised bank names.
5. **Attack Vector**: Scam classification (Digital Arrest, Job Task scam, etc.), coercion mechanism, and malicious APKs/tools identified (`AnyDesk`, `CBI-Safety.apk`).
6. **Key Facts Timeline**: Chronological timeline of spoken statements.
7. **Actionable Police SOP**: Checklist for immediate law enforcement intervention (1930 portal ticket, freeze UPI via NPCI, Sec 91 CrPC telecom notice).
8. **Responsible AI Guardrail**: Prominently displays legal advisory that emotion metrics are indicative triage signals, not definitive evidence of guilt or intent.

---

## 🚀 5. Deployment Overview

| Environment | Host Platform | URL / Endpoint |
|---|---|---|
| **Full Web Application** | **Render** | `https://vecnaverse.onrender.com` (or local `http://localhost:8000`) |
| **Speech Emotion Model (SER)** | **AWS EC2 (private microservice, Mumbai)** | `https://<your-ec2-host>:8000/predict` (Bearer-authenticated) |
| **Evidence Vault Storage** | **Supabase Storage** | `https://<project-ref>.supabase.co/storage/v1/object/<bucket>/` |
| **LLM Inference** | **Groq Cloud LPU** | `api.groq.com/openai/v1` |
| **Speech Intelligence** | **Sarvam AI API** | `api.sarvam.ai` |

---

## 🛠️ 6. Local Quickstart

### Prerequisites
- Python 3.10+
- Active API keys for Sarvam AI, Groq, and Supabase.

### 1. Clone & Install
```bash
git clone https://github.com/mindfoldz0-spec/Hellfire_Mayuresh.git
cd Hellfire_Mayuresh
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Create a `.env` file from `.env.example`:
```ini
# Sarvam AI (Indic Speech)
sarvam_api=your_sarvam_api_key
SARVAM_LANGUAGE_CODE=unknown

# Groq Cloud (Qwen3.8 27B)
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=qwen/qwen3.8-27b

# AWS SER Speech Emotion Recognition Microservice
AWS_EMOTION_URL=http://<your-ec2-ip>:8000/predict
# Shared bearer token for the server-to-server SER call (set the same on EC2)
CERS_SER_API_KEY=generate-a-long-random-token

# Supabase Storage
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_or_anon_key
SUPABASE_BUCKET=recordings

# Officer Security — sign in with your NAME + this password
CERS_OFFICER_PASSWORD=generate-a-long-random-password
CERS_OFFICER_TOKEN=
CERS_SECRET_KEY=generate-a-long-random-secret   # optional separate HMAC key
```

> There is no password baked into the code and the published sample values
> no longer start the gateway. Use per-operator accounts (`CERS_OFFICERS`)
> when more than one officer can handle calls — then each recording's
> `officer_id` is a verified identity. Each officer may accept several
> secrets (`CERS_OFFICERS="Mayuresh Alpha:secretA|secretB,..."`), and the
> login page offers the configured names as a dropdown.

### 3. Generate IVR Audio
```bash
python generate_ivr.py
```

### 4. Run Development Server
```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
- **Officer Workstation**: [http://localhost:8000/officer](http://localhost:8000/officer)
- **Caller Dialer**: [http://localhost:8000/caller](http://localhost:8000/caller)

Sign in on the officer console with **your name** as the operator ID and your
secret as the password. With `CERS_OFFICERS` set, the names appear as a
dropdown and each officer must use their own secret; otherwise the shared
`CERS_OFFICER_PASSWORD` from `.env` applies. (There is no default password.)
The name you enter is what appears in the queue lock, the recording archive and
every audit trail entry — so use the name you want on the record.

### 5. Run Verification Test Suite
```bash
python -m pytest                       # offline unit/integration suite
python -m ruff check .                 # lint
python -m mypy                         # type check (backend/app)
node scripts/check_frontend.mjs        # JS syntax, imports, DOM bindings, CSP
python scripts/stamp_assets.py --check # asset cache-busting is current
```
CI runs exactly these (`.github/workflows/ci.yml`) with a 70% coverage floor.
*(Runs the offline unit/integration suite: authentication, caller capability tokens, WebSocket signalling, call-state transitions, recording storage and the AI dossier schema contract. Supabase and the AI providers are stubbed, so no network access or API credits are needed.)*

The e2e dispatch flow is separate (marked `e2e`, skipped by default) and runs
against a live gateway — CI runs it too, in per-officer mode:
```bash
CERS_OFFICERS="OFFICER_01:pass1,OFFICER_02:pass2" python -m uvicorn backend.app.main:app --port 8010 &
CERS_E2E_BASE_URL=http://127.0.0.1:8010 \
CERS_E2E_OFFICERS="OFFICER_01:pass1,OFFICER_02:pass2" \
  python -m pytest -o addopts='' -m e2e backend/tests/test_e2e_ws.py
```
With a shared-password gateway, set `CERS_OFFICER_TOKEN` instead of
`CERS_E2E_OFFICERS`.

---

## 🛠️ 7. Operations, limits and known constraints

Read this before deploying; it is the difference between a demo and a desk tool.

| Area | Current behaviour | Constraint / mitigation |
|---|---|---|
| **Call state** | In-memory `CallManager`; atomic claiming is a single-process `asyncio.Lock`. | Run **exactly one worker**. The app takes an OS file lock and refuses to start a second one unless `CERS_ALLOW_MULTI_WORKER=1`; `/health` reports `worker_mode` (`single` vs `multi-unsafe`) and `auth_mode` so monitoring can see both. A restart loses in-flight calls (recordings on disk are unaffected). |
| **Evidence storage** | Local disk under `backend/recordings`, mirrored to Supabase when configured. | The Supabase bucket is optional; the vault degrades gracefully (backoff instead of a retry storm). Back up with `python scripts/backup_vault.py` and verify with `--verify`. |
| **AI dependencies** | Sarvam (ASR), AWS SER / HF Space (emotion), Groq (semantic). | All go through a retry + circuit-breaker layer. Every triage reports `ai_status`; when no SER model answers, the emotion block is labelled **ESTIMATED** in the console and the PDF. `CERS_AI_OFFLINE=1` forces the deterministic path. |
| **Emotion accuracy** | Measured only when the AWS/HF SER service responds. | The fallback is a documented prosody heuristic, never presented as a measurement. |
| **Retention** | `CERS_RECORDING_RETENTION_DAYS=0` (keep forever). | Set a non-zero value only with a station policy in mind; deletion is permanent and also removes the Supabase objects. |
| **Recording channels** | Mixed reference + separate officer/caller tracks. | Browsers that refuse two simultaneous `MediaRecorder`s still produce the mixed track; the vault shows which channels exist. |

### Interpreting the console labels

- **PRIORITY: n/100** - fused acoustic + semantic score.
- **(ESTIMATED)** - no speech-emotion model was reachable; the bars are a prosody/severity heuristic.
- **ESCALATED - UNCLAIMED** - the call crossed the SLA while still waiting.
- **VERIFY EVIDENCE** - recomputes the stored SHA-256 for every track and shows `MISMATCH` if the audio changed after capture.

## ⚖️ Responsible AI Advisory

> **Compliance Note:** In accordance with responsible AI standards for law enforcement, Speech Emotion Recognition (SER) metrics, distress scores, and AI-extracted incident entities generated by CERS are **indicative operational signals** designed solely for emergency call triage and prioritizing officer assistance. They do not constitute definitive legal proof of a person's honesty, intent, or culpability.

---

## 📄 License
This project is open-source under the [MIT License](LICENSE). Built for Cyber Emergency Response and Law Enforcement Incident Triage.
