/** HTTP calls used by the caller dialer. */

import { state } from "./state.js";

export async function createCallSession(number, callerNumber = "") {
  const response = await fetch("/api/calls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dialed_number: number, caller_number: callerNumber }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Could not create an emergency call session.");
  }
  return response.json();
}

/** Fallback when there is no microphone: submit a typed statement. */
export async function sendTextStatement(callId, token, text) {
  const formData = new FormData();
  formData.append("file", text);
  const response = await fetch(`/api/calls/${encodeURIComponent(callId)}/statement`, {
    method: "POST",
    headers: { "X-Call-Token": token || "" },
    body: formData,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not send the text report.");
  }
  return data;
}

export async function getRtcConfig() {
  if (!state.rtcConfigPromise) {
    state.rtcConfigPromise = fetch("/api/rtc-config")
      .then((response) => (response.ok ? response.json() : null))
      .then((config) => {
        if (config && Array.isArray(config.iceServers) && config.iceServers.length) {
          return { iceServers: config.iceServers };
        }
        return {
          iceServers: [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
          ],
        };
      })
      .catch(() => ({
        iceServers: [
          { urls: "stun:stun.l.google.com:19302" },
          { urls: "stun:stun1.l.google.com:19302" },
        ],
      }));
  }
  return state.rtcConfigPromise;
}
