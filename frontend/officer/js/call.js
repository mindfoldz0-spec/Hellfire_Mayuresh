/** Active emergency call: WebRTC, mixed-stream recording, and teardown. */

import { state } from "./state.js";
import {
  activeCallIdEl,
  activeCallTimerEl,
  callerScope,
  endDispatchBtn,
  incidentNotes,
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
import {
  getRtcConfig,
  sendBeaconUpload,
  uploadRecording,
} from "./api.js";
import { callLabel, renderQueue } from "./queue.js";
import { startOscilloscope } from "./utils.js";

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
    setOfficerMicEnabled(true);
    startActiveCallTimer();
    setupAudioEnvironment();
    startRecording();
    await initWebRTCOfferer(callId);
  } catch (error) {
    console.error("Error starting officer call session:", error);
    alert(
      "Microphone access is required to accept emergency calls. The call will be returned to the queue."
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

    const options = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? { mimeType: "audio/webm;codecs=opus" }
      : {};
    state.recordedChunks = [];
    state.mediaRecorder = new MediaRecorder(
      state.mixedDestination.stream,
      options
    );
    state.mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        state.recordedChunks.push(event.data);
      }
    };
    state.mediaRecorder.start(1000);
    updateRecordingIndicator(true);
    console.log("[Recorder] Dual-audio session recorder started.");
  } catch (error) {
    console.warn("MediaRecorder init failed:", error);
    updateRecordingIndicator(false);
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

function stopRecordingAndUpload(callId, duration, notes, { beacon = false } = {}) {
  const recorder = state.mediaRecorder;
  state.mediaRecorder = null;
  if (!recorder || recorder.state === "inactive") {
    updateRecordingIndicator(false);
    return;
  }

  const mimeType = recorder.mimeType || "audio/webm";
  recorder.onstop = async () => {
    const chunks = state.recordedChunks;
    state.recordedChunks = [];
    const blob = new Blob(chunks, { type: mimeType });
    updateRecordingIndicator(false);
    if (beacon) {
      sendBeaconUpload(callId, blob, duration, notes);
    } else {
      await uploadRecording(callId, blob, duration, notes);
    }
  };

  try {
    recorder.stop();
  } catch (error) {
    console.warn("Could not stop recorder:", error);
    updateRecordingIndicator(false);
  }
}

/** Drop the current recorder without uploading it (used on auth failure). */
export function discardRecording() {
  const recorder = state.mediaRecorder;
  state.mediaRecorder = null;
  if (recorder && recorder.state !== "inactive") {
    recorder.onstop = null;
    try {
      recorder.stop();
    } catch (e) {}
  }
  state.recordedChunks = [];
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
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    const mimeType = state.mediaRecorder.mimeType || "audio/webm";
    const blob = new Blob(state.recordedChunks, { type: mimeType });
    sendBeaconUpload(
      state.currentActiveCallId,
      blob,
      state.activeCallDuration,
      incidentNotes.value.trim()
    );
    try {
      state.mediaRecorder.stop();
    } catch (e) {}
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

  // Best-effort save if the officer closes/navigates away mid-call.
  window.addEventListener("pagehide", handlePageHide);
}
