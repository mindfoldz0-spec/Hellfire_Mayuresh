/** HTTP calls used by the caller dialer. */

import { state } from "./state.js";

export async function createCallSession(number) {
  const response = await fetch("/api/calls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dialed_number: number }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Could not create an emergency call session.");
  }
  return response.json();
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
