/** Authenticated HTTP helpers for the officer console. */

import { state } from "./state.js";
import { emit } from "./events.js";
import { extensionForMime } from "./utils.js";

export async function apiFetch(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    emit("auth:required", {
      message: "Session expired. Please sign in again.",
    });
    throw new Error("Authentication required.");
  }
  return response;
}

export async function getRtcConfig() {
  if (!state.rtcConfigPromise) {
    state.rtcConfigPromise = fetch("/api/rtc-config")
      .then((response) => (response.ok ? response.json() : null))
      .then((config) => {
        if (config && Array.isArray(config.iceServers) && config.iceServers.length) {
          return { iceServers: config.iceServers };
        }
        return { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };
      })
      .catch(() => ({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      }));
  }
  return state.rtcConfigPromise;
}

export async function fetchRecordings() {
  const response = await apiFetch("/api/recordings");
  if (!response.ok) return null;
  return response.json();
}

export async function saveRecordingNotes(metaName, notes) {
  const response = await apiFetch(
    `/api/recordings/${encodeURIComponent(metaName)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes }),
    }
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not save notes.");
  }
  return data;
}

export async function analyzeRecordingForensics(metaName) {
  const response = await apiFetch(
    `/api/recordings/${encodeURIComponent(metaName)}/analyze`,
    {
      method: "POST",
    }
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Forensic AI analysis failed.");
  }
  return data;
}

export async function uploadRecording(callId, audioBlob, duration, notes) {
  if (!callId || !audioBlob || audioBlob.size === 0) return;
  const extension = extensionForMime(audioBlob.type || "audio/webm");
  try {
    const formData = new FormData();
    formData.append("audio_file", audioBlob, `recording_${callId}.${extension}`);
    formData.append("duration", String(duration));
    formData.append("notes", notes);

    const response = await apiFetch(
      `/api/recordings/${encodeURIComponent(callId)}`,
      { method: "POST", body: formData }
    );

    if (response.ok) {
      console.log(`[Recording] Uploaded recording for call ${callId}`);
      emit("vault:refresh");
    } else {
      const error = await response.json().catch(() => ({}));
      console.error("Recording upload rejected:", error.detail || response.status);
    }
  } catch (error) {
    console.error("Error uploading recording:", error);
  }
}

export function sendBeaconUpload(callId, audioBlob, duration, notes) {
  if (!callId || !audioBlob || audioBlob.size === 0) return;
  try {
    const extension = extensionForMime(audioBlob.type || "audio/webm");
    const formData = new FormData();
    formData.append("audio_file", audioBlob, `recording_${callId}.${extension}`);
    formData.append("duration", String(duration));
    formData.append("notes", notes);
    navigator.sendBeacon(
      `/api/recordings/${encodeURIComponent(callId)}`,
      formData
    );
  } catch (error) {
    console.warn("Could not send final recording:", error);
  }
}
