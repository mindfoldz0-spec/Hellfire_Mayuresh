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

/**
 * Paginated, projected archive listing.
 * @returns {Promise<{items: Array, total: number, has_more: boolean}>}
 */
export async function fetchRecordings({ q = "", limit = 40, offset = 0 } = {}) {
  const params = new URLSearchParams({
    q,
    limit: String(limit),
    offset: String(offset),
  });
  const response = await apiFetch(`/api/recordings?${params.toString()}`);
  if (!response.ok) return null;
  return response.json();
}

export async function fetchRecordingDetail(metaName) {
  const response = await apiFetch(
    `/api/recordings/${encodeURIComponent(metaName)}/detail`
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not load the recording.");
  }
  return data;
}

export async function verifyRecording(metaName) {
  const response = await apiFetch(
    `/api/recordings/${encodeURIComponent(metaName)}/verify`
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not verify the recording.");
  }
  return data;
}

export function recordingPacketUrl(metaName) {
  return `/api/recordings/${encodeURIComponent(metaName)}/packet.pdf`;
}

export async function saveRecordingNotes(metaName, notes, disposition = "") {
  const response = await apiFetch(
    `/api/recordings/${encodeURIComponent(metaName)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes, disposition }),
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

export async function deleteRecording(metaName) {
  const response = await apiFetch(
    `/api/recordings/${encodeURIComponent(metaName)}`,
    { method: "DELETE" }
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not delete the recording.");
  }
  return data;
}

export async function fetchMetrics() {
  const response = await apiFetch("/api/metrics");
  if (!response.ok) return null;
  return response.json();
}

function appendTracks(formData, tracks, callId) {
  const names = {
    mixed: "audio_file",
    officer: "officer_audio",
    caller: "caller_audio",
  };
  let appended = 0;
  Object.entries(names).forEach(([track, field]) => {
    const blob = tracks[track];
    if (!blob || blob.size === 0) return;
    const extension = extensionForMime(blob.type || "audio/webm");
    formData.append(field, blob, `recording_${callId}.${track}.${extension}`);
    appended += 1;
  });
  return appended;
}

export async function uploadRecording(callId, tracks, duration, notes, clientUuid) {
  if (!callId || !tracks || !tracks.mixed || tracks.mixed.size === 0) return;
  try {
    const formData = new FormData();
    if (!appendTracks(formData, tracks, callId)) return;
    formData.append("duration", String(duration));
    formData.append("notes", notes);
    formData.append("client_uuid", clientUuid);

    const response = await apiFetch(
      `/api/recordings/${encodeURIComponent(callId)}`,
      { method: "POST", body: formData }
    );

    if (response.ok) {
      const body = await response.json().catch(() => ({}));
      console.log(
        `[Recording] ${body.status === "duplicate" ? "Already stored" : "Uploaded"} call ${callId}`
      );
      emit("vault:refresh");
    } else {
      const error = await response.json().catch(() => ({}));
      console.error("Recording upload rejected:", error.detail || response.status);
    }
  } catch (error) {
    console.error("Error uploading recording:", error);
  }
}

export function sendBeaconUpload(callId, tracks, duration, notes, clientUuid) {
  if (!callId || !tracks || !tracks.mixed || tracks.mixed.size === 0) return;
  try {
    const formData = new FormData();
    if (!appendTracks(formData, tracks, callId)) return;
    formData.append("duration", String(duration));
    formData.append("notes", notes);
    formData.append("client_uuid", clientUuid);
    navigator.sendBeacon(
      `/api/recordings/${encodeURIComponent(callId)}`,
      formData
    );
  } catch (error) {
    console.warn("Could not send final recording:", error);
  }
}

/** Upload one live in-call audio window for re-triage. */
export async function postTriageWindow(callId, wavBlob, windowIndex) {
  const formData = new FormData();
  formData.append("file", wavBlob, "window.wav");
  formData.append("window", String(windowIndex));
  const response = await apiFetch(
    `/api/calls/${encodeURIComponent(callId)}/triage/window`,
    { method: "POST", body: formData }
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Live triage window rejected.");
  }
  return response.json();
}
