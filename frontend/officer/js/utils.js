/** Audio and formatting helpers for the officer console. */

import { state } from "./state.js";

export function playIncomingPing() {
  try {
    if (!state.pingContext) {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) return;
      state.pingContext = new Context();
    }
    if (state.pingContext.state === "suspended") {
      state.pingContext.resume().catch(() => {});
    }
    const ctx = state.pingContext;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(1760, ctx.currentTime + 0.18);
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.25);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
  } catch (e) {}
}

let escalationTimer = null;

/**
 * Repeat the alert tone while a high-priority call sits unclaimed.
 * Silenced by claiming, ending, or after ~50s so it cannot ring forever.
 */
export function startEscalationAlarm() {
  stopEscalationAlarm();
  let beats = 0;
  escalationTimer = setInterval(() => {
    playIncomingPing();
    beats += 1;
    if (beats >= 20) stopEscalationAlarm();
  }, 2500);
}

export function stopEscalationAlarm() {
  if (escalationTimer) {
    clearInterval(escalationTimer);
    escalationTimer = null;
  }
}

export function startOscilloscope(canvas, analyser, strokeColor, saveAnimId) {
  const ctx = canvas.getContext("2d");
  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);

  function draw() {
    const animId = requestAnimationFrame(draw);
    saveAnimId(animId);

    analyser.getByteTimeDomainData(dataArray);

    const width = canvas.width;
    const height = canvas.height;

    ctx.fillStyle = "rgba(4, 8, 14, 0.4)";
    ctx.fillRect(0, 0, width, height);

    ctx.lineWidth = 2;
    ctx.strokeStyle = strokeColor;
    ctx.shadowColor = strokeColor;
    ctx.shadowBlur = 6;

    ctx.beginPath();
    const sliceWidth = (width * 1.0) / bufferLength;
    let x = 0;

    for (let i = 0; i < bufferLength; i++) {
      const v = dataArray[i] / 128.0;
      const y = (v * height) / 2;
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
      x += sliceWidth;
    }

    ctx.lineTo(width, height / 2);
    ctx.stroke();
  }

  draw();
}

export function extensionForMime(mimeType) {
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("mp4")) return "m4a";
  return "webm";
}

/**
 * Non-blocking console notice. Replaces window.alert(), which froze the whole
 * dispatch UI (including the active call timer and WebSocket event processing)
 * until the officer dismissed a modal.
 *
 * @param {string} message
 * @param {"info"|"success"|"warn"|"error"} tone
 */
export function showToast(message, tone = "info", timeoutMs = 5000) {
  if (!message) return;
  let container = document.getElementById("cers-toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "cers-toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `cers-toast cers-toast-${tone}`;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");
  toast.textContent = String(message);

  const dismiss = () => {
    toast.classList.add("leaving");
    setTimeout(() => toast.remove(), 200);
  };
  toast.addEventListener("click", dismiss);
  container.appendChild(toast);
  if (timeoutMs > 0) setTimeout(dismiss, timeoutMs);
  return toast;
}
