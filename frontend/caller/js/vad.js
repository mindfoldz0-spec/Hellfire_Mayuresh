/**
 * Voice Activity Detection (VAD) and Emergency Audio Statement Recorder.
 *
 * Captures 16 kHz PCM audio directly via Web Audio API, encodes standard WAV,
 * detects 2.5s of silence after speech, plays the recorded confirmation plus
 * hold music, and streams the WAV to the AI triage engine.
 *
 * The statement window is a *default* 10s, not a hard cap: the caller can add
 * another 10s (`+10s MORE TIME` / the `*` key), and a visible countdown warns
 * before the cut.
 */

import { state } from "./state.js";
import {
  recordedAudio,
  holdAudio,
  visualizerCaption,
  fastSubmitBtn,
  statementCountdown,
  extendStatementBtn,
} from "./dom.js";
import { sendAction } from "./ws.js";
import { setCallStatus } from "./ui.js";

const SILENCE_THRESHOLD_RMS = 0.015;
const SILENCE_DURATION_MS = 2500; // 2.5s silence detection after speech
const INITIAL_MAX_WAIT_MS = 5000;  // 5.0s wait if user stays completely silent
const DEFAULT_STATEMENT_MS = 10000; // default statement window (extendable)
const EXTENSION_MS = 10000;         // each "+10s" adds this much
const MAX_EXTENSIONS = 6;           // ceiling so the queue cannot be held forever
const TARGET_SAMPLE_RATE = 16000;

let pcmProcessorNode = null;
let pcmSourceNode = null;
let mutedSinkNode = null;
let vadInterval = null;
let countdownInterval = null;
let pcmBuffers = [];
let activeAudioCtx = null;

export function startVadAndRecording(stream) {
  if (!stream) {
    console.warn("[VAD] Cannot start: microphone stream is missing.");
    return;
  }

  // Always reset and start fresh
  cancelVadAndHold();
  state.vadActive = true;
  state.statementSent = false;
  state.statementStartedAt = Date.now();
  state.statementMaxMs = DEFAULT_STATEMENT_MS;
  state.extensions = 0;
  pcmBuffers = [];
  let hasSpoken = false;
  let silenceStartTime = null;

  // 1. Setup AudioContext safely
  let audioCtx = state.audioCtx;
  if (!audioCtx || audioCtx.state === "closed") {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    state.audioCtx = audioCtx;
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
  activeAudioCtx = audioCtx;

  // 2. Show Fast Submit button so caller can immediately skip waiting
  if (fastSubmitBtn) {
    fastSubmitBtn.classList.remove("hidden");
    fastSubmitBtn.onclick = () => {
      console.log("[VAD] Caller triggered manual fast submission.");
      finalizeStatement(audioCtx ? audioCtx.sampleRate : 16000);
    };
  }
  if (extendStatementBtn) {
    extendStatementBtn.classList.remove("hidden");
    extendStatementBtn.onclick = () => extendStatement();
  }
  if (statementCountdown) statementCountdown.classList.remove("hidden");

  // 3. Setup PCM Recorder & Analyser safely (single source node to avoid Android crashes)
  let analyser = state.analyserNode;
  try {
    if (!pcmSourceNode) {
      pcmSourceNode = state.micVisualizerSource || audioCtx.createMediaStreamSource(stream);
    }

    if (!analyser) {
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.3;
      pcmSourceNode.connect(analyser);
    }

    pcmProcessorNode = audioCtx.createScriptProcessor(4096, 1, 1);
    pcmProcessorNode.onaudioprocess = (event) => {
      if (!state.vadActive) return;
      const input = event.inputBuffer.getChannelData(0);
      pcmBuffers.push(new Float32Array(input));
    };

    pcmSourceNode.connect(pcmProcessorNode);

    // A ScriptProcessorNode only fires while it is connected to the audio
    // graph. It used to be wired straight to `audioCtx.destination`, which
    // played the caller's microphone back through their own speakers (an
    // audible echo, and a feedback loop with the officer's voice). A muted
    // gain node keeps the callback running without any output.
    if (!mutedSinkNode) {
      mutedSinkNode = audioCtx.createGain();
      mutedSinkNode.gain.value = 0;
      mutedSinkNode.connect(audioCtx.destination);
    }
    pcmProcessorNode.connect(mutedSinkNode);
    console.log(`[VAD] Native PCM recording active at ${audioCtx.sampleRate}Hz.`);
  } catch (err) {
    console.error("[VAD] Error setting up audio processor graph:", err);
  }

  visualizerCaption.textContent = "LISTENING... DESCRIBE YOUR EMERGENCY MESSAGE NOW";

  const buffer = new Float32Array(analyser ? analyser.fftSize : 512);

  vadInterval = setInterval(() => {
    if (!state.vadActive) {
      if (vadInterval) clearInterval(vadInterval);
      return;
    }

    let rms = 0;
    if (analyser) {
      try {
        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (let i = 0; i < buffer.length; i++) {
          sum += buffer[i] * buffer[i];
        }
        rms = Math.sqrt(sum / buffer.length);
      } catch (e) {}
    }

    const now = Date.now();

    if (rms > SILENCE_THRESHOLD_RMS) {
      if (!hasSpoken) {
        hasSpoken = true;
        console.log("[VAD] Caller speech onset detected! RMS:", rms.toFixed(4));
        setCallStatus("RECORDING STATEMENT (SPEAKING...)", "status-connecting");
      }
      silenceStartTime = null;
      visualizerCaption.textContent = "RECORDING LIVE DISTRESS STATEMENT...";
    } else if (hasSpoken) {
      if (!silenceStartTime) {
        silenceStartTime = now;
      } else {
        const silenceElapsed = now - silenceStartTime;
        const remainingSec = Math.max(0, Math.ceil((SILENCE_DURATION_MS - silenceElapsed) / 1000));
        visualizerCaption.textContent = `SILENCE DETECTED (${remainingSec}s UNTIL CONNECTING)...`;

        if (silenceElapsed >= SILENCE_DURATION_MS) {
          console.log("[VAD] Silence detected after speech. Concluding statement.");
          finalizeStatement(audioCtx.sampleRate);
          return;
        }
      }
    }
  }, 100);

  // Countdown so the window never closes without warning.
  countdownInterval = setInterval(() => {
    if (!state.vadActive || !statementCountdown) return;
    const remaining = Math.max(
      0,
      Math.ceil((state.statementMaxMs - (Date.now() - state.statementStartedAt)) / 1000)
    );
    statementCountdown.textContent = `STATEMENT WINDOW: ${remaining}s LEFT`;
    statementCountdown.classList.toggle("warn", remaining <= 3);
  }, 250);

  // Safety timer 1: if caller says nothing at all for 5s, conclude
  state.vadSilenceTimer = setTimeout(() => {
    if (state.vadActive && !hasSpoken) {
      console.log("[VAD] Initial silence timeout reached (5s). Concluding.");
      finalizeStatement(audioCtx.sampleRate);
    }
  }, INITIAL_MAX_WAIT_MS);

  armMaxTimer();
}

/** (Re)arm the statement window, preserving any time already used. */
function armMaxTimer() {
  clearTimeout(state.vadMaxTimer);
  const elapsed = Date.now() - state.statementStartedAt;
  const remaining = Math.max(1000, state.statementMaxMs - elapsed);
  state.vadMaxTimer = setTimeout(() => {
    if (state.vadActive) {
      console.log("[VAD] Statement window elapsed. Concluding.");
      finalizeStatement(activeAudioCtx ? activeAudioCtx.sampleRate : 16000);
    }
  }, remaining);
}

/** Give the caller ten more seconds (button or `*` key). */
export function extendStatement() {
  if (!state.vadActive) return;
  if (state.extensions >= MAX_EXTENSIONS) {
    visualizerCaption.textContent = "MAXIMUM STATEMENT LENGTH REACHED - CONNECTING...";
    return;
  }
  state.extensions += 1;
  state.statementMaxMs += EXTENSION_MS;
  armMaxTimer();
  visualizerCaption.textContent = `EXTENDED - ${Math.round(
    state.statementMaxMs / 1000
  )}s TOTAL RECORDING WINDOW`;
}

function hideStatementControls() {
  if (statementCountdown) {
    statementCountdown.classList.add("hidden");
    statementCountdown.textContent = "";
  }
  if (extendStatementBtn) extendStatementBtn.classList.add("hidden");
}

function finalizeStatement(originalSampleRate) {
  if (!state.vadActive) return;
  state.vadActive = false;
  state.statementSent = true;

  if (vadInterval) {
    clearInterval(vadInterval);
    vadInterval = null;
  }
  if (countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
  clearTimeout(state.vadSilenceTimer);
  clearTimeout(state.vadMaxTimer);
  state.vadSilenceTimer = null;
  state.vadMaxTimer = null;

  if (fastSubmitBtn) {
    fastSubmitBtn.classList.add("hidden");
  }
  hideStatementControls();

  // Disconnect PCM processor
  if (pcmProcessorNode) {
    try {
      pcmProcessorNode.disconnect();
      pcmProcessorNode.onaudioprocess = null;
    } catch (e) {}
    pcmProcessorNode = null;
  }
  if (pcmSourceNode) {
    try {
      pcmSourceNode.disconnect();
    } catch (e) {}
    pcmSourceNode = null;
  }

  console.log("[VAD] Finalizing statement audio...");

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

  // 2. Encode collected PCM buffers into standard 16kHz mono WAV Blob
  const wavBlob = buildWavBlob(pcmBuffers, originalSampleRate, TARGET_SAMPLE_RATE);
  if (wavBlob) {
    console.log(`[VAD] Generated native 16kHz WAV Blob: ${wavBlob.size} bytes`);
    transmitTriageAudio(wavBlob);
  } else {
    console.warn("[VAD] No PCM buffers captured to build WAV.");
  }
}

/** Flatten float buffers, resample to 16kHz, and build standard RIFF WAV. */
function buildWavBlob(buffers, inputRate, outputRate) {
  if (!buffers || buffers.length === 0) return null;

  let totalLength = 0;
  for (let i = 0; i < buffers.length; i++) {
    totalLength += buffers[i].length;
  }
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (let i = 0; i < buffers.length; i++) {
    merged.set(buffers[i], offset);
    offset += buffers[i].length;
  }

  const downsampled = downsampleBuffer(merged, inputRate, outputRate);

  const wavBuffer = new ArrayBuffer(44 + downsampled.length * 2);
  const view = new DataView(wavBuffer);

  function writeString(viewOffset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(viewOffset + i, str.charCodeAt(i));
    }
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + downsampled.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true); // PCM subchunk size
  view.setUint16(20, 1, true); // Audio format 1 = PCM
  view.setUint16(22, 1, true); // 1 channel = mono
  view.setUint32(24, outputRate, true); // Sample rate (16000)
  view.setUint32(28, outputRate * 2, true); // Byte rate (16000 * 1 * 2)
  view.setUint16(32, 2, true); // Block align (1 * 2)
  view.setUint16(34, 16, true); // Bits per sample (16)
  writeString(36, "data");
  view.setUint32(40, downsampled.length * 2, true);

  let dataOffset = 44;
  for (let i = 0; i < downsampled.length; i++, dataOffset += 2) {
    const s = Math.max(-1, Math.min(1, downsampled[i]));
    view.setInt16(dataOffset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  return new Blob([wavBuffer], { type: "audio/wav" });
}

function downsampleBuffer(buffer, inputRate, outputRate) {
  if (outputRate >= inputRate) return buffer;
  const ratio = inputRate / outputRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
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

async function transmitTriageAudio(wavBlob) {
  if (!wavBlob || wavBlob.size === 0) {
    console.warn("[VAD] No WAV audio captured to transmit.");
    return;
  }

  console.log(`[VAD] Transmitting native WAV audio (${wavBlob.size} bytes) for AI triage...`);

  sendAction({ action: "IVR_FINISHED" });

  const lang = encodeURIComponent(state.language || "en-IN");

  try {
    const formData = new FormData();
    formData.append("file", wavBlob, "statement.wav");

    const resp = await fetch(
      `/api/calls/${encodeURIComponent(state.sessionId)}/triage?lang=${lang}`,
      { method: "POST", headers: { "X-Call-Token": state.callToken || "" }, body: formData }
    );

    if (resp.ok) {
      console.log("[VAD] Triage audio uploaded successfully via HTTP.");
      return;
    } else {
      console.warn("[VAD] HTTP triage upload returned status:", resp.status);
    }
  } catch (err) {
    console.warn("[VAD] HTTP triage upload failed, falling back to WS:", err);
  }

  const reader = new FileReader();
  reader.onloadend = () => {
    const base64Data = reader.result.split(",")[1];
    sendAction({
      action: "TRIAGE_AUDIO",
      mime: "audio/wav",
      audio: base64Data,
    });
  };
  reader.readAsDataURL(wavBlob);
}

export function cancelVadAndHold() {
  state.vadActive = false;
  state.statementSent = false;
  if (vadInterval) {
    clearInterval(vadInterval);
    vadInterval = null;
  }
  if (countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
  clearTimeout(state.vadSilenceTimer);
  clearTimeout(state.vadMaxTimer);
  state.vadSilenceTimer = null;
  state.vadMaxTimer = null;

  if (fastSubmitBtn) {
    fastSubmitBtn.classList.add("hidden");
  }
  hideStatementControls();

  if (pcmProcessorNode) {
    try {
      pcmProcessorNode.disconnect();
      pcmProcessorNode.onaudioprocess = null;
    } catch (e) {}
    pcmProcessorNode = null;
  }
  pcmSourceNode = null;
  pcmBuffers = [];
  stopHoldMusic();
}
