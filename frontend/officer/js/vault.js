/** Recordings vault: archive listing, playback, and officer notes. */

import { state } from "./state.js";
import {
  closeIntelModalBtn,
  downloadAudioBtn,
  headerVaultBtn,
  intelAudioFilename,
  intelAudioPlayer,
  intelCallId,
  intelCodecVal,
  intelDetailView,
  intelDuration,
  intelEmptyPrompt,
  intelFilesizeVal,
  intelGatewayVal,
  intelModalBackdrop,
  intelNotesTextarea,
  intelOfficer,
  intelOutcomeVal,
  intelSearchInput,
  intelStatusBadge,
  intelTimestamp,
  maxIntelModalBtn,
  menuIntelBtn,
  minIntelModalBtn,
  modalRecordingsList,
  openIntelBtn,
  refreshVaultBtn,
  retroWindowModal,
  saveIntelNotesBtn,
  sidebarCount,
  startMenuPopup,
  vaultBadgeCount,
} from "./dom.js";
import { emit, on } from "./events.js";
import { fetchRecordings, saveRecordingNotes } from "./api.js";

export async function fetchRecordingsVault() {
  try {
    const recordings = await fetchRecordings();
    if (!Array.isArray(recordings)) return;
    state.cachedRecordings = recordings;
    const count = state.cachedRecordings.length;
    if (vaultBadgeCount) vaultBadgeCount.textContent = String(count);
    if (sidebarCount) sidebarCount.textContent = String(count);
    renderArchiveList(state.cachedRecordings);
    if (state.cachedRecordings.length > 0 && !state.selectedRecording) {
      selectRecordingForVault(state.cachedRecordings[0]);
    }
  } catch (error) {
    console.error("Error fetching recordings:", error);
  }
}

function recordingLabel(rec) {
  return rec.dialed_number || String(rec.call_id || "").slice(0, 8);
}

function safeRecordingUrl(rec) {
  const url = typeof rec.url === "string" ? rec.url : "";
  return url.startsWith("/api/recordings/") || url.startsWith("https://") ? url : "";
}

export function renderArchiveList(recordings) {
  if (!modalRecordingsList) return;
  modalRecordingsList.innerHTML = "";
  if (!recordings || recordings.length === 0) {
    const notice = document.createElement("div");
    notice.className = "empty-list-notice";
    notice.textContent = "No recorded calls found in archive.";
    modalRecordingsList.appendChild(notice);
    return;
  }

  recordings.forEach((rec) => {
    const item = document.createElement("div");
    const isSelected =
      state.selectedRecording &&
      state.selectedRecording.meta_name === rec.meta_name;
    item.className = `recording-tree-item ${isSelected ? "selected" : ""}`;

    const top = document.createElement("div");
    top.className = "tree-item-top";
    const id = document.createElement("span");
    id.className = "tree-item-id";
    id.textContent = `🚨 #${recordingLabel(rec)}`;
    const tag = document.createElement("span");
    tag.className = "tree-item-tag";
    tag.textContent = "STORED";
    top.appendChild(id);
    top.appendChild(tag);

    const sub = document.createElement("div");
    sub.className = "tree-item-sub";
    const timestamp = document.createElement("span");
    const date = rec.timestamp ? new Date(rec.timestamp * 1000) : null;
    timestamp.textContent =
      date && !Number.isNaN(date.getTime())
        ? date.toLocaleTimeString()
        : "Unknown time";
    const duration = document.createElement("span");
    duration.textContent = `⏱️ ${rec.duration ? `${rec.duration}s` : "Audio"}`;
    sub.appendChild(timestamp);
    sub.appendChild(duration);

    item.appendChild(top);
    item.appendChild(sub);
    item.addEventListener("click", () => selectRecordingForVault(rec));
    modalRecordingsList.appendChild(item);
  });
}

export function selectRecordingForVault(rec) {
  state.selectedRecording = rec;
  renderArchiveList(state.cachedRecordings);

  intelEmptyPrompt.classList.add("hidden");
  intelDetailView.classList.remove("hidden");

  intelCallId.textContent = `EMERGENCY CALL #${recordingLabel(rec)}`;
  if (intelStatusBadge) intelStatusBadge.textContent = "[ RECORDING STORED ]";
  if (intelAudioFilename) intelAudioFilename.textContent = rec.filename || "recording";
  if (intelTimestamp) {
    const date = rec.timestamp ? new Date(rec.timestamp * 1000) : null;
    intelTimestamp.textContent =
      date && !Number.isNaN(date.getTime())
        ? `📅 ${date.toLocaleString()}`
        : "📅 Unknown";
  }
  if (intelDuration) {
    intelDuration.textContent = `⏱️ Duration: ${rec.duration || 0}s`;
  }
  if (intelOfficer) {
    intelOfficer.textContent = `👮 Handled by: ${rec.officer_id || "UNASSIGNED"}`;
  }

  const url = safeRecordingUrl(rec);
  if (intelAudioPlayer) {
    if (url) {
      intelAudioPlayer.src = url;
    } else {
      intelAudioPlayer.removeAttribute("src");
      intelAudioPlayer.load();
    }
  }
  if (downloadAudioBtn) {
    if (url) {
      downloadAudioBtn.href = `${url}?download=1`;
      downloadAudioBtn.setAttribute("download", rec.filename || "recording");
      downloadAudioBtn.classList.remove("hidden");
    } else {
      downloadAudioBtn.removeAttribute("href");
      downloadAudioBtn.classList.add("hidden");
    }
  }

  if (intelGatewayVal) {
    intelGatewayVal.textContent = `${rec.dialed_number || "2007"} (Emergency Line)`;
  }
  if (intelCodecVal) {
    intelCodecVal.textContent = rec.mime_type || "WebM / Opus (Mixed Stream)";
  }
  if (intelFilesizeVal) {
    intelFilesizeVal.textContent = rec.size_bytes
      ? `${Math.max(1, Math.round(rec.size_bytes / 1024))} KB`
      : "0 KB";
  }
  if (intelOutcomeVal) intelOutcomeVal.textContent = "COMPLETED & ARCHIVED";
  if (intelNotesTextarea) intelNotesTextarea.value = rec.notes || "";
}

export function openVaultModal() {
  if (!state.officerId) {
    emit("auth:show", { message: "Sign in to open the recordings vault." });
    return;
  }
  if (intelModalBackdrop) {
    intelModalBackdrop.classList.remove("hidden");
    intelModalBackdrop.style.setProperty("display", "flex", "important");
  }
  fetchRecordingsVault();
}

export function closeVaultModal() {
  if (intelModalBackdrop) {
    intelModalBackdrop.classList.add("hidden");
    intelModalBackdrop.style.removeProperty("display");
  }
  if (intelAudioPlayer) intelAudioPlayer.pause();
}

async function handleSaveNotes() {
  if (!state.selectedRecording || !state.selectedRecording.meta_name) {
    alert("Select a recording before saving notes.");
    return;
  }
  saveIntelNotesBtn.disabled = true;
  try {
    const data = await saveRecordingNotes(
      state.selectedRecording.meta_name,
      intelNotesTextarea.value
    );
    state.selectedRecording.notes = data.notes || intelNotesTextarea.value;
    const cached = state.cachedRecordings.find(
      (rec) => rec.meta_name === state.selectedRecording.meta_name
    );
    if (cached) cached.notes = state.selectedRecording.notes;
    alert("Officer dispatch notes saved.");
  } catch (error) {
    alert(error.message || "Could not save notes.");
  } finally {
    saveIntelNotesBtn.disabled = false;
  }
}

export function initVault() {
  window.openVaultModal = openVaultModal;
  window.closeVaultModal = closeVaultModal;

  if (openIntelBtn) openIntelBtn.addEventListener("click", openVaultModal);
  if (headerVaultBtn) headerVaultBtn.addEventListener("click", openVaultModal);
  if (closeIntelModalBtn) {
    closeIntelModalBtn.addEventListener("click", closeVaultModal);
  }
  if (minIntelModalBtn) {
    minIntelModalBtn.addEventListener("click", closeVaultModal);
  }

  if (menuIntelBtn) {
    menuIntelBtn.addEventListener("click", () => {
      if (startMenuPopup) startMenuPopup.classList.add("hidden");
      openVaultModal();
    });
  }

  if (maxIntelModalBtn && retroWindowModal) {
    maxIntelModalBtn.addEventListener("click", () => {
      const isMax = retroWindowModal.classList.toggle("maximized");
      maxIntelModalBtn.innerHTML = isMax
        ? `<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="1" width="6" height="6"></rect><rect x="1" y="3" width="6" height="6" fill="var(--surface-alt)"></rect></svg>`
        : `<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="1" width="8" height="8"></rect></svg>`;
    });
  }

  if (intelModalBackdrop) {
    intelModalBackdrop.addEventListener("click", (event) => {
      if (event.target === intelModalBackdrop) {
        closeVaultModal();
      }
    });
  }

  if (intelSearchInput) {
    intelSearchInput.addEventListener("input", () => {
      const query = intelSearchInput.value.toLowerCase().trim();
      renderArchiveList(
        state.cachedRecordings.filter(
          (rec) =>
            String(rec.call_id || "")
              .toLowerCase()
              .includes(query) ||
            String(rec.dialed_number || "")
              .toLowerCase()
              .includes(query) ||
            (rec.notes && rec.notes.toLowerCase().includes(query))
        )
      );
    });
  }

  if (saveIntelNotesBtn) {
    saveIntelNotesBtn.addEventListener("click", handleSaveNotes);
  }
  if (refreshVaultBtn) {
    refreshVaultBtn.addEventListener("click", fetchRecordingsVault);
  }

  on("vault:refresh", fetchRecordingsVault);
}
