/**
 * Live in-call re-triage.
 *
 * The pre-dispatch statement only describes the first ten seconds of a call.
 * This module taps the *caller's* remote stream while the officer is engaged,
 * encodes 5-second PCM windows to 16 kHz mono WAV, and posts them to
 * `/api/calls/{id}/triage/window`. The server merges the refreshed score and
 * transcript and broadcasts `CALL_TRIAGE_UPDATED`.
 *
 * Uses ScriptProcessor (deprecated but universally available) with a muted
 * sink, so capture never plays the caller's voice back into the room.
 */

import { state } from "./state.js";
import { postTriageWindow } from "./api.js";
import { showToast } from "./utils.js";

const WINDOW_MS = 5000;
const TARGET_SAMPLE_RATE = 16000;

let sourceNode = null;
let processorNode = null;
let sinkNode = null;
let windowTimer = null;
let buffers = [];
let windowIndex = 0;
let running = false;
let failureNotified = false;

export function isLiveTriageRunning() {
  return running;
}

export function startLiveTriage() {
  if (running) return true;
  if (!state.remoteStream) {
    showToast("No caller audio yet - live triage starts once they connect.", "warn");
    return false;
  }
  if (!state.audioContext) {
    showToast("Audio engine is not ready for live triage.", "warn");
    return false;
  }

  try {
    const context = state.audioContext;
    sourceNode = context.createMediaStreamSource(state.remoteStream);
    processorNode = context.createScriptProcessor(4096, 1, 1);
    processorNode.onaudioprocess = (event) => {
      if (!running) return;
      buffers.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };

    sinkNode = context.createGain();
    sinkNode.gain.value = 0;
    sinkNode.connect(context.destination);

    sourceNode.connect(processorNode);
    processorNode.connect(sinkNode);

    buffers = [];
    windowIndex = 0;
    failureNotified = false;
    running = true;
    windowTimer = setInterval(flushWindow, WINDOW_MS);
    showToast("Live triage running - call audio is re-scored every 5s.", "success");
    return true;
  } catch (error) {
    console.error("[LiveTriage] Could not start:", error);
    stopLiveTriage();
    showToast("Could not start live triage on this audio stream.", "error");
    return false;
  }
}

export function stopLiveTriage() {
  running = false;
  if (windowTimer) {
    clearInterval(windowTimer);
    windowTimer = null;
  }
  if (processorNode) {
    try {
      processorNode.disconnect();
    } catch (e) {}
    processorNode.onaudioprocess = null;
    processorNode = null;
  }
  if (sourceNode) {
    try {
      sourceNode.disconnect();
    } catch (e) {}
    sourceNode = null;
  }
  if (sinkNode) {
    try {
      sinkNode.disconnect();
    } catch (e) {}
    sinkNode = null;
  }
  buffers = [];
}

async function flushWindow() {
  if (!running || buffers.length === 0) return;
  const chunks = buffers;
  buffers = [];
  const callId = state.currentActiveCallId;
  if (!callId) return;

  const rate = state.audioContext ? state.audioContext.sampleRate : TARGET_SAMPLE_RATE;
  const wav = encodeWav(chunks, rate, TARGET_SAMPLE_RATE);
  if (!wav) return;

  windowIndex += 1;
  try {
    await postTriageWindow(callId, wav, windowIndex);
  } catch (error) {
    console.warn("[LiveTriage] Window rejected:", error.message);
    if (!failureNotified) {
      failureNotified = true;
      showToast("Live triage is unavailable; dispatch scoring stays as-is.", "warn");
    }
  }
}

/** Flatten float chunks, resample to 16 kHz, and build a mono 16-bit WAV. */
function encodeWav(chunks, inputRate, outputRate) {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  if (total === 0) return null;

  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }

  const samples =
    outputRate < inputRate ? downsample(merged, inputRate, outputRate) : merged;
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (position, text) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(position + i, text.charCodeAt(i));
    }
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, outputRate, true);
  view.setUint32(28, outputRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let dataOffset = 44;
  for (let i = 0; i < samples.length; i += 1, dataOffset += 2) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(
      dataOffset,
      sample < 0 ? sample * 0x8000 : sample * 0x7fff,
      true
    );
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function downsample(buffer, inputRate, outputRate) {
  const ratio = inputRate / outputRate;
  const length = Math.round(buffer.length / ratio);
  const result = new Float32Array(length);
  let resultOffset = 0;
  let bufferOffset = 0;

  while (resultOffset < result.length) {
    const nextOffset = Math.round((resultOffset + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let i = bufferOffset; i < nextOffset && i < buffer.length; i += 1) {
      sum += buffer[i];
      count += 1;
    }
    result[resultOffset] = count > 0 ? sum / count : 0;
    resultOffset += 1;
    bufferOffset = nextOffset;
  }
  return result;
}
