/** DTMF tone generation for the caller keypad. */

import { state } from "./state.js";

export const DTMF_FREQUENCIES = {
  "1": [697, 1209],
  "2": [697, 1336],
  "3": [697, 1477],
  "4": [770, 1209],
  "5": [770, 1336],
  "6": [770, 1477],
  "7": [852, 1209],
  "8": [852, 1336],
  "9": [852, 1477],
  "*": [941, 1209],
  "0": [941, 1336],
  "#": [941, 1477],
};

function getToneContext() {
  if (!state.toneCtx) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    state.toneCtx = new Context();
  }
  if (state.toneCtx.state === "suspended") {
    state.toneCtx.resume().catch(() => {});
  }
  return state.toneCtx;
}

export function playTone(freq1, freq2, duration = 0.12) {
  try {
    const ctx = getToneContext();
    if (!ctx) return;
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gainNode = ctx.createGain();

    osc1.frequency.value = freq1;
    osc2.frequency.value = freq2;

    gainNode.gain.setValueAtTime(0.12, ctx.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);

    osc1.connect(gainNode);
    osc2.connect(gainNode);
    gainNode.connect(ctx.destination);

    osc1.start();
    osc2.start();
    osc1.stop(ctx.currentTime + duration);
    osc2.stop(ctx.currentTime + duration);
  } catch (e) {
    // AudioContext muted/unsupported.
  }
}

/**
 * Non-blocking notice for the dialer. window.alert() froze the call UI (and
 * the emergency timer) until the caller tapped OK — unacceptable mid-call.
 *
 * @param {string} message
 * @param {"info"|"success"|"warn"|"error"} tone
 */
export function showToast(message, tone = "info", timeoutMs = 6000) {
  if (!message) return;
  let container = document.getElementById("caller-toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "caller-toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `caller-toast caller-toast-${tone}`;
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
