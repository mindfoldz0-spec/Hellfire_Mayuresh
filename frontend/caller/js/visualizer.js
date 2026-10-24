/** Canvas oscilloscope for caller microphone and officer audio. */

import { state } from "./state.js";
import { canvasCtx, visualizerCanvas } from "./dom.js";

export function setupVisualizer() {
  if (!state.audioCtx) {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (state.audioCtx.state === "suspended") {
    state.audioCtx.resume().catch(() => {});
  }

  if (state.analyserNode) {
    try {
      state.analyserNode.disconnect();
    } catch (e) {}
  }
  state.analyserNode = state.audioCtx.createAnalyser();
  state.analyserNode.fftSize = 256;
  state.analyserNode.smoothingTimeConstant = 0.8;

  drawVisualizer();
}

export function connectMicToVisualizer() {
  if (!state.localStream || !state.audioCtx || !state.analyserNode) return;
  try {
    if (state.micVisualizerSource) {
      state.micVisualizerSource.disconnect();
    }
    state.micVisualizerSource = state.audioCtx.createMediaStreamSource(
      state.localStream
    );
    state.micVisualizerSource.connect(state.analyserNode);
  } catch (e) {
    console.error("Error connecting mic to visualizer:", e);
  }
}

export function connectRemoteToVisualizer(stream) {
  if (!state.audioCtx || !state.analyserNode || !stream) return;
  try {
    if (state.remoteVisualizerSource) {
      state.remoteVisualizerSource.disconnect();
    }
    state.remoteVisualizerSource =
      state.audioCtx.createMediaStreamSource(stream);
    state.remoteVisualizerSource.connect(state.analyserNode);
  } catch (e) {
    console.warn("Could not connect remote audio to visualizer:", e);
  }
}

export function disconnectRemoteVisualizer() {
  if (state.remoteVisualizerSource) {
    try {
      state.remoteVisualizerSource.disconnect();
    } catch (e) {}
    state.remoteVisualizerSource = null;
  }
}

function drawVisualizer() {
  const analyser = state.analyserNode;
  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);

  function render() {
    state.animationFrameId = requestAnimationFrame(render);
    analyser.getByteTimeDomainData(dataArray);

    const width = visualizerCanvas.width;
    const height = visualizerCanvas.height;

    canvasCtx.clearRect(0, 0, width, height);

    canvasCtx.lineWidth = 2.5;
    canvasCtx.strokeStyle = state.isMuted ? "#FF3B30" : "#007AFF";
    canvasCtx.shadowColor = state.isMuted ? "rgba(255, 59, 48, 0.4)" : "rgba(0, 122, 255, 0.35)";
    canvasCtx.shadowBlur = 4;

    canvasCtx.beginPath();
    const sliceWidth = (width * 1.0) / bufferLength;
    let x = 0;

    for (let i = 0; i < bufferLength; i++) {
      const v = dataArray[i] / 128.0;
      const y = (v * height) / 2;
      if (i === 0) {
        canvasCtx.moveTo(x, y);
      } else {
        canvasCtx.lineTo(x, y);
      }
      x += sliceWidth;
    }

    canvasCtx.lineTo(width, height / 2);
    canvasCtx.stroke();
  }

  render();
}

export function teardownVisualizer() {
  if (state.animationFrameId) {
    cancelAnimationFrame(state.animationFrameId);
    state.animationFrameId = null;
  }
  if (state.analyserNode) {
    try {
      state.analyserNode.disconnect();
    } catch (e) {}
    state.analyserNode = null;
  }
  if (state.micVisualizerSource) {
    try {
      state.micVisualizerSource.disconnect();
    } catch (e) {}
    state.micVisualizerSource = null;
  }
  disconnectRemoteVisualizer();
}
