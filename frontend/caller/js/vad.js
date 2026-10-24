/**
 * Voice Activity Detection (VAD) and Emergency Audio Statement Recorder.
 * Captures speech after the IVR beep, detects 5s silence, triggers
 * recorded confirmation prompt + hold music loop, and streams audio to AI backend.
 */

import { state } from "./state.js";
import { recordedAudio, holdAudio, visualizerCaption } from "./dom.js";
import { sendAction } from "./ws.js";
import { setCallStatus } from "./ui.js";

const SILENCE_THRESHOLD_RMS = 0.014;
const SILENCE_DURATION_MS = 5000;
const INITIAL_MAX_WAIT_MS = 12000;
const MAX_STATEMENT_MS = 25000;

export function startVadAndRecording(stream) {
  if (!stream || state.vadActive || state.statementSent) return;

  state.vadActive = true;
  state.triageChunks = [];
  let hasSpoken = false;
  let silenceStartTime = null;

  // 1. Setup MediaRecorder
  let mimeType = "audio/webm;codecs=opus";
  if (!MediaRecorder.isTypeSupported(mimeType)) {
    if (MediaRecorder.isTypeSupported("audio/webm")) {
      mimeType = "audio/webm";
    } else if (MediaRecorder.isTypeSupported("audio/mp4")) {
      mimeType = "audio/mp4";
    } else {
      mimeType = "";
    }
  }

  try {
    const recorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        state.triageChunks.push(event.data);
      }
    };

    recorder.start(250); // Slice into 250ms chunks
    state.mediaRecorder = recorder;
    console.log("[VAD] Statement recording started with mime:", mimeType || "default");
  } catch (err) {
    console.error("[VAD] Failed to initialize MediaRecorder:", err);
  }

  // 2. Setup VAD Audio Energy Analyzer
  let audioCtx = state.audioCtx;
  if (!audioCtx || audioCtx.state === "closed") {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    state.audioCtx = audioCtx;
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }

  const source = audioCtx.createMediaStreamSource(stream);
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);

  const buffer = new Float32Array(analyser.fftSize);

  visualizerCaption.textContent = "LISTENING... SPEAK YOUR DISTRESS MESSAGE NOW";

  // Check audio volume every 100ms
  const vadInterval = setInterval(() => {
    if (!state.vadActive) {
      clearInterval(vadInterval);
      return;
    }

    analyser.getFloatTimeDomainData(buffer);
    let sum = 0;
    for (let i = 0; i < buffer.length; i++) {
      sum += buffer[i] * buffer[i];
    }
    const rms = Math.sqrt(sum / buffer.length);

    const now = Date.now();

    if (rms > SILENCE_THRESHOLD_RMS) {
      // Voice detected
      if (!hasSpoken) {
        hasSpoken = true;
        console.log("[VAD] Speech onset detected! RMS:", rms.toFixed(4));
        setCallStatus("RECORDING STATEMENT (SPEAKING...)", "status-connecting");
      }
      silenceStartTime = null;
      visualizerCaption.textContent = "RECORDING LIVE DISTRESS STATEMENT...";
    } else {
      // Below threshold (silence or quiet ambient noise)
      if (hasSpoken) {
        if (!silenceStartTime) {
          silenceStartTime = now;
        } else {
          const silenceElapsed = now - silenceStartTime;
          const remainingSec = Math.max(0, Math.ceil((SILENCE_DURATION_MS - silenceElapsed) / 1000));
          visualizerCaption.textContent = `SILENCE DETECTED (${remainingSec}s UNTIL CONNECTING)...`;

          if (silenceElapsed >= SILENCE_DURATION_MS) {
            console.log("[VAD] 5 seconds of silence detected. Finalizing statement.");
            clearInterval(vadInterval);
            finalizeStatement(mimeType);
            return;
          }
        }
      }
    }
  }, 100);

  // Safety timer if caller says nothing for 12s
  state.vadSilenceTimer = setTimeout(() => {
    if (state.vadActive && !hasSpoken) {
      console.log("[VAD] Initial silence timeout reached without speech.");
      clearInterval(vadInterval);
      finalizeStatement(mimeType);
    }
  }, INITIAL_MAX_WAIT_MS);

  // Hard maximum statement duration (25s)
  state.vadMaxTimer = setTimeout(() => {
    if (state.vadActive) {
      console.log("[VAD] Maximum statement length reached (25s).");
      clearInterval(vadInterval);
      finalizeStatement(mimeType);
    }
  }, MAX_STATEMENT_MS);
}

function finalizeStatement(mimeType) {
  if (!state.vadActive) return;
  state.vadActive = false;
  state.statementSent = true;

  clearTimeout(state.vadSilenceTimer);
  clearTimeout(state.vadMaxTimer);
  state.vadSilenceTimer = null;
  state.vadMaxTimer = null;

  console.log("[VAD] Finalizing audio statement...");

  // 1. Play confirmation prompt
  setCallStatus("MESSAGE RECORDED - CONNECTING OFFICER", "status-connecting");
  visualizerCaption.textContent = "YOUR MESSAGE HAS BEEN RECORDED. PLEASE HOLD...";

  try {
    recordedAudio.currentTime = 0;
    recordedAudio.volume = 1.0;
    recordedAudio.onended = () => {
      startHoldMusic();
    };
    recordedAudio.play().catch((err) => {
      console.warn("Recorded prompt playback failed:", err);
      startHoldMusic();
    });
  } catch (err) {
    console.warn("Error playing recorded prompt:", err);
    startHoldMusic();
  }

  // 2. Stop MediaRecorder and package Blob
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.onstop = () => {
      const blob = new Blob(state.triageChunks, {
        type: mimeType || "audio/webm",
      });
      console.log(`[VAD] Audio blob ready: ${blob.size} bytes`);
      transmitTriageAudio(blob);
    };
    try {
      state.mediaRecorder.stop();
    } catch (e) {
      console.warn("Error stopping MediaRecorder:", e);
    }
  } else if (state.triageChunks.length > 0) {
    const blob = new Blob(state.triageChunks, {
      type: mimeType || "audio/webm",
    });
    transmitTriageAudio(blob);
  }
}

function startHoldMusic() {
  try {
    holdAudio.currentTime = 0;
    holdAudio.volume = 0.6;
    holdAudio.play().catch((err) => console.warn("Hold music autoplay blocked:", err));
    visualizerCaption.textContent = "OFFICER CONNECTING... (HOLD MUSIC PLAYING)";
  } catch (err) {
    console.warn("Hold music error:", err);
  }
}

export function stopHoldMusic() {
  try {
    if (holdAudio) {
      holdAudio.pause();
      holdAudio.currentTime = 0;
    }
    if (recordedAudio) {
      recordedAudio.pause();
      recordedAudio.currentTime = 0;
    }
  } catch (e) {}
}

function transmitTriageAudio(blob) {
  if (!blob || blob.size === 0) {
    console.warn("[VAD] No audio captured to transmit.");
    return;
  }

  const reader = new FileReader();
  reader.onloadend = () => {
    const base64Data = reader.result.split(",")[1];
    console.log(`[VAD] Transmitting ${base64Data.length} chars of base64 audio for triage...`);
    sendAction({
      action: "TRIAGE_AUDIO",
      mime: blob.type || "audio/webm",
      audio: base64Data,
    });
  };
  reader.readAsDataURL(blob);
}

export function cancelVadAndHold() {
  state.vadActive = false;
  clearTimeout(state.vadSilenceTimer);
  clearTimeout(state.vadMaxTimer);
  state.vadSilenceTimer = null;
  state.vadMaxTimer = null;

  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    try {
      state.mediaRecorder.stop();
    } catch (e) {}
  }
  state.mediaRecorder = null;
  state.triageChunks = [];
  stopHoldMusic();
}
