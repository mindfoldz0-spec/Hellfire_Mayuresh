/** Active emergency call: WebRTC, dual-channel recording, live triage, teardown. */

import { state } from "./state.js";
import {
  activeCallIdEl,
  activeCallTimerEl,
  activeCategoryTag,
  activePriorityBadge,
  activeTriageSop,
  activeTriageSummary,
  activeTriageTranscript,
  callerScope,
  endDispatchBtn,
  incidentNotes,
  liveTriageBtn,
  liveTriageText,
  officerMuteBtn,
  officerMuteText,
  officerScope,
  recordingIndicator,
  remoteCallerAudio,
  stationActive,
  stationIdle,
  tagBtns,
} from "./dom.js";
import { emit } from "./events.js";
import { getRtcConfig, sendBeaconUpload, uploadRecording } from "./api.js";
import { isLiveTriageRunning, startLiveTriage, stopLiveTriage } from "./live-triage.js";
import { callLabel, renderQueue } from "./queue.js";
import { showToast, startOscilloscope } from "./utils.js";

/** One MediaRecorder per audio channel. */
class TrackRecorder {
  constructor(stream, label) {
    this.label = label;
    this.chunks = [];
    this.mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "";
    this.recorder = new MediaRecorder(
      stream,
      this.mimeType ? { mimeType: this.mimeType } : {}
    );
    this.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    };
    this.active = false;
  }

  start() {
    if (this.active) return;
    this.chunks = [];
    this.recorder.start(1000);
    this.active = true;
  }

  /** Stop and resolve with the accumulated blob (or null). */
  stop() {
    return new Promise((resolve) => {
      if (!this.active || this.recorder.state === "inactive") {
        this.active = false;
        resolve(null);
        return;
      }
      const chunks = this.chunks;
      const type = this.recorder.mimeType || "audio/webm";
      this.recorder.onstop = () => {
        this.active = false;
        const blob = new Blob(chunks, { type });
        resolve(blob.size > 0 ? blob : null);
      };
      try {
        this.recorder.stop();
      } catch (error) {
        console.warn(`[Recorder] ${this.label} stop failed:`, error);
        this.active = false;
        resolve(null);
      }
    });
  }

  snapshot() {
    if (!this.chunks.length) return null;
    const blob = new Blob(this.chunks, {
      type: this.recorder.mimeType || "audio/webm",
    });
    return blob.size > 0 ? blob : null;
  }

  discard() {
    this.recorder.onstop = null;
    if (this.recorder.state !== "inactive") {
      try {
        this.recorder.stop();
      } catch (e) {}
    }
    this.chunks = [];
    this.active = false;
  }
}

export async function startActiveCallSession(callId) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: false,
    });
    if (state.currentActiveCallId !== callId) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    state.localMicStream = stream;

    const call = state.callsMap.get(callId);
    stationIdle.classList.add("hidden");
    stationActive.classList.remove("hidden");
    activeCallIdEl.textContent = `EMERGENCY CALL #${
      call ? callLabel(call) : callId.slice(0, 8)
    }`;
    incidentNotes.value = "";
    state.callClientUuid = createClientUuid();
    setOfficerMicEnabled(true);
    startActiveCallTimer();
    setupAudioEnvironment();
    startRecording();
    if (call) renderActiveTriage(call);
    await initWebRTCOfferer(callId);
  } catch (error) {
    console.error("Error starting officer call session:", error);
    showToast(
      "Microphone access is required to accept emergency calls. The call was returned to the queue.",
      "error",
      9000
    );
    if (state.currentActiveCallId) {
      emit("ws:send", {
        action: "RELEASE_CALL",
        call_id: state.currentActiveCallId,
      });
    }
    cleanupActiveCall();
  }
}

function createClientUuid() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `call-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function initWebRTCOfferer(callId) {
  const config = await getRtcConfig();
  if (state.currentActiveCallId !== callId) return;

  state.peerConnection = new RTCPeerConnection(config);

  if (state.localMicStream) {
    state.localMicStream.getTracks().forEach((track) => {
      state.peerConnection.addTrack(track, state.localMicStream);
    });
  }

  state.peerConnection.onicecandidate = (event) => {
    if (event.candidate) {
      emit("ws:send", {
        action: "WEBRTC_SIGNAL",
        call_id: callId,
        payload: { candidate: event.candidate },
      });
    }
  };

  state.peerConnection.ontrack = (event) => {
    console.log("[Officer WebRTC] Received caller remote audio stream!");
    state.remoteStream = event.streams[0];
    remoteCallerAudio.srcObject = state.remoteStream;
    remoteCallerAudio
      .play()
      .catch((error) => console.warn("Remote audio play error:", error));
    connectRemoteAudioNodes();
    startCallerRecording();
  };

  const offer = await state.peerConnection.createOffer();
  await state.peerConnection.setLocalDescription(offer);

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    throw new Error("Dispatch gateway disconnected before WebRTC offer.");
  }
  emit("ws:send", {
    action: "WEBRTC_SIGNAL",
    call_id: callId,
    payload: { offer: state.peerConnection.localDescription },
  });
}

export async function flushPendingCandidates() {
  if (!state.peerConnection || !state.peerConnection.remoteDescription) return;
  const candidates = state.pendingCandidates;
  state.pendingCandidates = [];
  for (const candidate of candidates) {
    try {
      await state.peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
    } catch (error) {
      console.error("Error adding buffered ICE candidate:", error);
    }
  }
}

function setupAudioEnvironment() {
  if (!state.audioContext) {
    state.audioContext = new (window.AudioContext ||
      window.webkitAudioContext)();
  }
  if (state.audioContext.state === "suspended") {
    state.audioContext.resume().catch(() => {});
  }
}

function startRecording() {
  if (!state.audioContext || !state.localMicStream) return;
  try {
    state.mixedDestination = state.audioContext.createMediaStreamDestination();

    state.micSource = state.audioContext.createMediaStreamSource(
      state.localMicStream
    );
    const officerAnalyser = state.audioContext.createAnalyser();
    officerAnalyser.fftSize = 256;
    state.micSource.connect(officerAnalyser);
    state.micSource.connect(state.mixedDestination);
    startOscilloscope(
      officerScope,
      officerAnalyser,
      "#BEF000",
      (id) => (state.officerScopeAnimationId = id)
    );

    // Dual-channel capture: the mixed stream is a convenience reference, while
    // the per-channel tracks are what make the evidence defensible.
    state.trackRecorders = {
      mixed: new TrackRecorder(state.mixedDestination.stream, "mixed"),
      officer: new TrackRecorder(state.localMicStream, "officer"),
    };
    state.trackRecorders.mixed.start();
    state.trackRecorders.officer.start();
    updateRecordingIndicator(true);
    console.log("[Recorder] Mixed + officer channels recording.");
  } catch (error) {
    console.warn("MediaRecorder init failed:", error);
    updateRecordingIndicator(false);
  }
}

function startCallerRecording() {
  if (!state.remoteStream || state.trackRecorders.caller) return;
  try {
    state.trackRecorders.caller = new TrackRecorder(state.remoteStream, "caller");
    state.trackRecorders.caller.start();
    console.log("[Recorder] Caller channel recording.");
  } catch (error) {
    console.warn("[Recorder] Caller channel failed:", error);
  }
}

function connectRemoteAudioNodes() {
  if (!state.audioContext || !state.remoteStream || state.remoteConnected) return;
  try {
    state.remoteSource = state.audioContext.createMediaStreamSource(
      state.remoteStream
    );
    const callerAnalyser = state.audioContext.createAnalyser();
    callerAnalyser.fftSize = 256;
    state.remoteSource.connect(callerAnalyser);
    if (state.mixedDestination) {
      state.remoteSource.connect(state.mixedDestination);
    }
    startOscilloscope(
      callerScope,
      callerAnalyser,
      "#3ddc84",
      (id) => (state.callerScopeAnimationId = id)
    );
    state.remoteConnected = true;
  } catch (error) {
    console.error("Error connecting remote audio nodes:", error);
  }
}

export function updateRecordingIndicator(active) {
  if (!recordingIndicator) return;
  const label = recordingIndicator.querySelector("span:last-child");
  if (label) {
    label.textContent = active ? "AUTO-RECORDER ACTIVE" : "RECORDER IDLE";
  }
  recordingIndicator.classList.toggle("recording-off", !active);
}

function startActiveCallTimer() {
  if (state.activeTimerInterval) clearInterval(state.activeTimerInterval);
  state.activeCallDuration = 0;
  activeCallTimerEl.textContent = "00:00";
  state.activeTimerInterval = setInterval(() => {
    state.activeCallDuration++;
    const mins = String(Math.floor(state.activeCallDuration / 60)).padStart(
      2,
      "0"
    );
    const secs = String(state.activeCallDuration % 60).padStart(2, "0");
    activeCallTimerEl.textContent = `${mins}:${secs}`;
  }, 1000);
}

export function setOfficerMicEnabled(enabled) {
  if (state.localMicStream) {
    state.localMicStream
      .getAudioTracks()
      .forEach((track) => (track.enabled = enabled));
  }
  state.isOfficerMuted = !enabled;
  if (state.isOfficerMuted) {
    officerMuteBtn.classList.add("active");
    officerMuteText.textContent = "[ UNMUTE MIC ]";
  } else {
    officerMuteBtn.classList.remove("active");
    officerMuteText.textContent = "[ MUTE MIC ]";
  }
}

/** Render triage intel for the *currently engaged* call. */
export function renderActiveTriage(call) {
  if (!call) return;
  const score = Number(call.triage_priority_score || 10);
  if (activePriorityBadge) {
    activePriorityBadge.textContent = `\u26a1 PRIORITY: ${score}/100`;
    activePriorityBadge.className = `triage-score-badge ${
      score >= 85
        ? "score-critical"
        : score >= 65
        ? "score-high"
        : score >= 40
        ? "score-elevated"
        : "score-routine"
    }`;
  }
  if (activeCategoryTag) {
    activeCategoryTag.textContent = call.incident_category || "PENDING TRIAGE";
  }
  if (activeTriageSummary) {
    const source = (call.emotions || {}).source;
    const suffix =
      source === "acoustic" ? "  \u26a0 ESTIMATED (no SER model reachable)" : "";
    activeTriageSummary.textContent = `${
      call.incident_summary || "Awaiting caller statement..."
    }${suffix}`;
  }
  if (activeTriageSop) {
    activeTriageSop.innerHTML = "";
    const lines = Array.isArray(call.officer_sop)
      ? call.officer_sop
      : call.officer_sop
      ? [call.officer_sop]
      : [];
    if (lines.length === 0) {
      const li = document.createElement("li");
      li.textContent = "No tactical SOP generated yet.";
      activeTriageSop.appendChild(li);
    }
    lines.forEach((line) => {
      const li = document.createElement("li");
      li.textContent = line;
      activeTriageSop.appendChild(li);
    });
  }
  if (activeTriageTranscript) {
    activeTriageTranscript.textContent =
      (call.transcript || "").trim() || "(no transcript captured yet)";
  }
}

function stopRecordingAndUpload(callId, duration, notes, { beacon = false } = {}) {
  const recorders = state.trackRecorders;
  state.trackRecorders = {};
  const entries = Object.entries(recorders);
  if (entries.length === 0) {
    updateRecordingIndicator(false);
    return;
  }

  const collected = Promise.all(
    entries.map(async ([name, recorder]) => [name, await recorder.stop()])
  ).then((pairs) => Object.fromEntries(pairs.filter(([, blob]) => blob)));

  collected.then((tracks) => {
    updateRecordingIndicator(false);
    if (beacon) {
      sendBeaconUpload(callId, tracks, duration, notes, state.callClientUuid);
    } else {
      uploadRecording(callId, tracks, duration, notes, state.callClientUuid);
    }
  });
}

/** Drop the current recorders without uploading (used on auth failure). */
export function discardRecording() {
  Object.values(state.trackRecorders).forEach((recorder) => recorder.discard());
  state.trackRecorders = {};
  updateRecordingIndicator(false);
}

export async function finishActiveCallSession(notifyServer = true) {
  if (state.isFinishing) return;
  const finishedCallId = state.currentActiveCallId;
  if (!finishedCallId) {
    cleanupActiveCall();
    return;
  }
  state.isFinishing = true;

  const finalDuration = state.activeCallDuration;
  const finalNotes = incidentNotes.value.trim();

  if (notifyServer) {
    emit("ws:send", { action: "END_CALL", call_id: finishedCallId });
  }

  stopRecordingAndUpload(finishedCallId, finalDuration, finalNotes);
  cleanupActiveCall();
  state.isFinishing = false;
}

export function cleanupActiveCall() {
  stopLiveTriage();
  if (liveTriageBtn) liveTriageBtn.classList.remove("active");
  if (liveTriageText) liveTriageText.textContent = "[ \u25b6 LIVE RE-TRIAGE ]";

  if (state.peerConnection) {
    try {
      state.peerConnection.close();
    } catch (e) {}
    state.peerConnection = null;
  }
  state.pendingCandidates = [];

  if (state.micSource) {
    try {
      state.micSource.disconnect();
    } catch (e) {}
    state.micSource = null;
  }
  if (state.remoteSource) {
    try {
      state.remoteSource.disconnect();
    } catch (e) {}
    state.remoteSource = null;
  }

  if (state.localMicStream) {
    state.localMicStream.getTracks().forEach((track) => track.stop());
    state.localMicStream = null;
  }
  state.remoteStream = null;
  state.remoteConnected = false;
  state.mixedDestination = null;
  remoteCallerAudio.srcObject = null;

  if (state.activeTimerInterval) {
    clearInterval(state.activeTimerInterval);
    state.activeTimerInterval = null;
  }
  if (state.callerScopeAnimationId) {
    cancelAnimationFrame(state.callerScopeAnimationId);
    state.callerScopeAnimationId = null;
  }
  if (state.officerScopeAnimationId) {
    cancelAnimationFrame(state.officerScopeAnimationId);
    state.officerScopeAnimationId = null;
  }

  state.currentActiveCallId = null;
  state.activeCallDuration = 0;
  stationActive.classList.add("hidden");
  stationIdle.classList.remove("hidden");
  activeCallTimerEl.textContent = "00:00";
  setOfficerMicEnabled(true);
  updateRecordingIndicator(false);
  renderQueue();
}

function handlePageHide() {
  if (!state.currentActiveCallId) return;
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    try {
      state.ws.send(
        JSON.stringify({
          action: "END_CALL",
          call_id: state.currentActiveCallId,
        })
      );
    } catch (e) {}
  }
  // Best effort: the timeslice chunks already emitted are good enough for a
  // recovery upload, and the client_uuid prevents a duplicate when the normal
  // path also lands.
  const tracks = {};
  Object.entries(state.trackRecorders).forEach(([name, recorder]) => {
    const blob = recorder.snapshot();
    if (blob) tracks[name] = blob;
  });
  if (tracks.mixed) {
    sendBeaconUpload(
      state.currentActiveCallId,
      tracks,
      state.activeCallDuration,
      incidentNotes.value.trim(),
      state.callClientUuid
    );
  }
  Object.values(state.trackRecorders).forEach((recorder) => recorder.discard());
  state.trackRecorders = {};
}

function toggleLiveTriage() {
  if (isLiveTriageRunning()) {
    stopLiveTriage();
    if (liveTriageBtn) liveTriageBtn.classList.remove("active");
    if (liveTriageText) liveTriageText.textContent = "[ \u25b6 LIVE RE-TRIAGE ]";
    showToast("Live triage stopped.", "info");
    return;
  }
  if (startLiveTriage()) {
    if (liveTriageBtn) liveTriageBtn.classList.add("active");
    if (liveTriageText) liveTriageText.textContent = "[ \u23f8 STOP LIVE TRIAGE ]";
  }
}

export function initCallControls() {
  officerMuteBtn.addEventListener("click", () => {
    setOfficerMicEnabled(state.isOfficerMuted);
  });

  tagBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tag = btn.dataset.tag;
      if (!incidentNotes.value.includes(tag)) {
        incidentNotes.value = `${tag} ${incidentNotes.value}`.trim();
      }
    });
  });

  endDispatchBtn.addEventListener("click", () => {
    finishActiveCallSession(true);
  });

  if (liveTriageBtn) {
    liveTriageBtn.addEventListener("click", toggleLiveTriage);
  }

  // Best-effort save if the officer closes/navigates away mid-call.
  window.addEventListener("pagehide", handlePageHide);
}
