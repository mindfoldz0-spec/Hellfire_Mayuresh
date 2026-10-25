/**
 * Voice Activity Detection (VAD) and Emergency Audio Statement Recorder.
 * Captures 16kHz PCM audio directly via Web Audio API, encodes standard WAV,
 * detects 5s silence after speech, plays recorded confirmation + hold music,
 * and streams native WAV audio to the AI triage engine.
 */

import { state } from "./state.js";
import { recordedAudio, holdAudio, visualizerCaption } from "./dom.js";
import { sendAction } from "./ws.js";
import { setCallStatus } from "./ui.js";

const SILENCE_THRESHOLD_RMS = 0.015;
const SILENCE_DURATION_MS = 3000; // 3.0s silence detection
const INITIAL_MAX_WAIT_MS = 6000;
const MAX_STATEMENT_MS = 14000;
const TARGET_SAMPLE_RATE = 16000;

let pcmProcessorNode = null;
let pcmSourceNode = null;
let pcmBuffers = [];

export function startVadAndRecording(stream) {
  if (!stream || state.vadActive || state.statementSent) return;

  state.vadActive = true;
  pcmBuffers = [];
  let hasSpoken = false;
  let silenceStartTime = null;

  // 1. Setup AudioContext
  let audioCtx = state.audioCtx;
  if (!audioCtx || audioCtx.state === "closed") {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    state.audioCtx = audioCtx;
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }

  // 2. Setup PCM Recorder using ScriptProcessorNode (captures raw PCM without codec artifacts)
  try {
    pcmSourceNode = audioCtx.createMediaStreamSource(stream);
    // 4096 buffer size, 1 input channel (mono), 1 output channel
    pcmProcessorNode = audioCtx.createScriptProcessor(4096, 1, 1);

    pcmProcessorNode.onaudioprocess = (event) => {
      if (!state.vadActive) return;
      const input = event.inputBuffer.getChannelData(0);
      pcmBuffers.push(new Float32Array(input));
    };

    pcmSourceNode.connect(pcmProcessorNode);
    // Connect to destination so onaudioprocess fires reliably across browsers
    pcmProcessorNode.connect(audioCtx.destination);
    console.log(`[VAD] Native PCM recording active at ${audioCtx.sampleRate}Hz.`);
  } catch (err) {
    console.error("[VAD] Error setting up PCM processor:", err);
  }

  // 3. Setup VAD Analyser for Real-Time RMS Volume & Silence Detection
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  const analyserSource = audioCtx.createMediaStreamSource(stream);
  analyserSource.connect(analyser);

  const buffer = new Float32Array(analyser.fftSize);
  visualizerCaption.textContent = "LISTENING... DESCRIBE YOUR EMERGENCY MESSAGE NOW";

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
      // Speech detected
      if (!hasSpoken) {
        hasSpoken = true;
        console.log("[VAD] Caller speech onset detected! RMS:", rms.toFixed(4));
        setCallStatus("RECORDING STATEMENT (SPEAKING...)", "status-connecting");
      }
      silenceStartTime = null;
      visualizerCaption.textContent = "RECORDING LIVE DISTRESS STATEMENT...";
    } else {
      // Below threshold (silence)
      if (hasSpoken) {
        if (!silenceStartTime) {
          silenceStartTime = now;
        } else {
          const silenceElapsed = now - silenceStartTime;
          const remainingSec = Math.max(0, Math.ceil((SILENCE_DURATION_MS - silenceElapsed) / 1000));
          visualizerCaption.textContent = `SILENCE DETECTED (${remainingSec}s UNTIL CONNECTING)...`;

          if (silenceElapsed >= SILENCE_DURATION_MS) {
            console.log("[VAD] 5 seconds of silence detected. Concluding statement.");
            clearInterval(vadInterval);
            finalizeStatement(audioCtx.sampleRate);
            return;
          }
        }
      }
    }
  }, 100);

  // Safety timer if caller says nothing for 14s
  state.vadSilenceTimer = setTimeout(() => {
    if (state.vadActive && !hasSpoken) {
      console.log("[VAD] Initial silence timeout reached.");
      clearInterval(vadInterval);
      finalizeStatement(audioCtx.sampleRate);
    }
  }, INITIAL_MAX_WAIT_MS);

  // Hard maximum statement duration (25s)
  state.vadMaxTimer = setTimeout(() => {
    if (state.vadActive) {
      console.log("[VAD] Maximum statement length reached (25s).");
      clearInterval(vadInterval);
      finalizeStatement(audioCtx.sampleRate);
    }
  }, MAX_STATEMENT_MS);
}

function finalizeStatement(originalSampleRate) {
  if (!state.vadActive) return;
  state.vadActive = false;
  state.statementSent = true;

  clearTimeout(state.vadSilenceTimer);
  clearTimeout(state.vadMaxTimer);
  state.vadSilenceTimer = null;
  state.vadMaxTimer = null;

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

  // Merge float chunks
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

  // Downsample to 16kHz
  const downsampled = downsampleBuffer(merged, inputRate, outputRate);

  // Encode 16-bit PCM WAV
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

  // Write PCM 16-bit samples
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

function transmitTriageAudio(wavBlob) {
  if (!wavBlob || wavBlob.size === 0) {
    console.warn("[VAD] No WAV audio captured to transmit.");
    return;
  }

  const reader = new FileReader();
  reader.onloadend = () => {
    const base64Data = reader.result.split(",")[1];
    console.log(`[VAD] Transmitting ${base64Data.length} chars of base64 WAV audio for AI triage...`);
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
  clearTimeout(state.vadSilenceTimer);
  clearTimeout(state.vadMaxTimer);
  state.vadSilenceTimer = null;
  state.vadMaxTimer = null;

  if (pcmProcessorNode) {
    try {
      pcmProcessorNode.disconnect();
      pcmProcessorNode.onaudioprocess = null;
    } catch (e) {}
    pcmProcessorNode = null;
  }
  pcmBuffers = [];
  stopHoldMusic();
}
