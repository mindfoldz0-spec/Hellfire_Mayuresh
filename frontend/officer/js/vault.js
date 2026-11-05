import { state } from "./state.js";
import {
  closeIntelModalBtn,
  custodyNote,
  dbarAnger,
  dbarDistress,
  dbarFear,
  dbarNervousness,
  dbarNeutral,
  deleteRecordingBtn,
  dispositionSelect,
  downloadAudioBtn,
  dvalAnger,
  dvalDistress,
  dvalFear,
  dvalNervousness,
  dvalNeutral,
  fActionableSop,
  fAmountDemanded,
  fAmountLost,
  fCallerContact,
  fCallerLang,
  fCallerLocation,
  fCallerName,
  fCoercion,
  fDestinationUpi,
  fDistressScoreBadge,
  fDominantEmotionBadge,
  fFactsTimeline,
  fMaliciousTools,
  fPsychSummary,
  fScamType,
  fSuspectAgency,
  fSuspectHandles,
  fSuspectIdentity,
  fSuspectPhones,
  fVictimBank,
  forensicAnalyzeBtn,
  forensicBtnText,
  forensicCategoryTag,
  forensicDossierPanel,
  forensicPriorityBadge,
  forensicVulnerabilityBadge,
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
  loadMoreBtn,
  maxIntelModalBtn,
  menuIntelBtn,
  minIntelModalBtn,
  modalRecordingsList,
  openIntelBtn,
  packetBtn,
  refreshVaultBtn,
  retroWindowModal,
  saveIntelNotesBtn,
  sidebarCount,
  startMenuPopup,
  trackSelect,
  vaultBadgeCount,
  verifyAudioBtn,
} from "./dom.js";
import { emit, on } from "./events.js";
import {
  analyzeRecordingForensics,
  deleteRecording,
  fetchRecordingDetail,
  fetchRecordings,
  recordingPacketUrl,
  saveRecordingNotes,
  verifyRecording,
} from "./api.js";
import { showToast } from "./utils.js";

const NOT_DISCLOSED = "Not Disclosed";
const PAGE_SIZE = 40;

const TRACK_LABELS = {
  mixed: "Mixed reference",
  officer: "Officer channel",
  caller: "Caller channel",
};

/** Join a list-valued field for display, with a fallback label. */
function joinList(value, fallback = "None logged") {
  if (Array.isArray(value)) {
    const items = value.filter((item) => String(item || "").trim());
    return items.length ? items.join(", ") : fallback;
  }
  if (typeof value === "string" && value.trim()) return value.trim();
  return fallback;
}

function textOr(value, fallback = NOT_DISCLOSED) {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number") return String(value);
  return fallback;
}

function recordingLabel(rec) {
  return rec.dialed_number || String(rec.call_id || "").slice(0, 8);
}

/** Load (or append) a page of the archive. */
export async function fetchRecordingsVault({ reset = false } = {}) {
  if (reset) {
    state.cachedRecordings = [];
    state.recordingsOffset = 0;
    state.selectedRecording = null;
  }
  try {
    const page = await fetchRecordings({
      q: state.recordingsQuery,
      limit: PAGE_SIZE,
      offset: state.recordingsOffset,
    });
    if (!page) return;
    state.recordingsTotal = page.total;
    state.cachedRecordings = reset
      ? page.items
      : [...state.cachedRecordings, ...page.items];
    state.recordingsOffset = state.cachedRecordings.length;

    if (vaultBadgeCount) vaultBadgeCount.textContent = String(page.total);
    if (sidebarCount) sidebarCount.textContent = String(page.total);
    if (loadMoreBtn) loadMoreBtn.classList.toggle("hidden", !page.has_more);

    renderArchiveList(state.cachedRecordings);
    if (!state.selectedRecording && state.cachedRecordings.length > 0) {
      selectRecordingForVault(state.cachedRecordings[0]);
    }
    if (state.selectedRecording && reset) {
      intelEmptyPrompt.classList.remove("hidden");
      intelDetailView.classList.add("hidden");
    }
  } catch (error) {
    console.error("Error fetching recordings:", error);
  }
}

export function renderArchiveList(recordings) {
  if (!modalRecordingsList) return;
  modalRecordingsList.innerHTML = "";
  if (!recordings || recordings.length === 0) {
    const notice = document.createElement("div");
    notice.className = "empty-list-notice";
    notice.textContent = state.recordingsQuery
      ? "No archived calls match that search."
      : "No recorded calls found in archive.";
    modalRecordingsList.appendChild(notice);
    return;
  }

  recordings.forEach((rec) => {
    const item = document.createElement("div");
    const isSelected =
      state.selectedRecording && state.selectedRecording.meta_name === rec.meta_name;
    item.className = `recording-tree-item ${isSelected ? "selected" : ""}`;
    if (rec.audio_available === false) item.classList.add("no-audio");

    const top = document.createElement("div");
    top.className = "tree-item-top";
    const id = document.createElement("span");
    id.className = "tree-item-id";
    id.textContent = `🚨 #${recordingLabel(rec)}`;
    const tag = document.createElement("span");
    tag.className = "tree-item-tag";
    tag.textContent = rec.audio_available === false ? "NO AUDIO" : "STORED";
    if (rec.audio_available === false) tag.classList.add("tag-no-audio");
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
    const meta = document.createElement("span");
    const bits = [];
    if (rec.priority_score) bits.push(`⚡${rec.priority_score}`);
    if (rec.has_analysis) bits.push("🧠");
    if (rec.disposition) bits.push(rec.disposition);
    meta.textContent = bits.join(" ") || `⏱️ ${rec.duration || 0}s`;
    sub.appendChild(timestamp);
    sub.appendChild(meta);

    item.appendChild(top);
    item.appendChild(sub);
    item.addEventListener("click", () => selectRecordingForVault(rec));
    modalRecordingsList.appendChild(item);
  });
}

/** Fetch the full record, then paint the detail pane. */
export async function selectRecordingForVault(summary) {
  state.selectedRecording = { ...summary };
  renderArchiveList(state.cachedRecordings);

  intelEmptyPrompt.classList.add("hidden");
  intelDetailView.classList.remove("hidden");
  intelCallId.textContent = `EMERGENCY CALL #${recordingLabel(summary)}`;
  if (intelStatusBadge) intelStatusBadge.textContent = "[ LOADING RECORD... ]";

  try {
    const detail = await fetchRecordingDetail(summary.meta_name);
    state.selectedRecording = { ...summary, ...detail };
    const index = state.cachedRecordings.findIndex(
      (item) => item.meta_name === summary.meta_name
    );
    if (index >= 0) {
      state.cachedRecordings[index] = {
        ...state.cachedRecordings[index],
        has_analysis: Boolean(detail.forensic_analysis),
      };
    }
    renderDetail(state.selectedRecording);
  } catch (error) {
    console.error("Could not load recording detail:", error);
    showToast(error.message || "Could not load that recording.", "error");
    if (intelStatusBadge) intelStatusBadge.textContent = "[ LOAD FAILED ]";
  }
}

function renderDetail(rec) {
  if (intelStatusBadge) {
    intelStatusBadge.textContent =
      rec.audio_available === false ? "[ AUDIO MISSING ]" : "[ RECORDING STORED ]";
  }
  if (intelAudioFilename) {
    intelAudioFilename.textContent =
      rec.audio_available === false
        ? `${rec.filename || "recording"} (file not found)`
        : rec.filename || "recording";
  }
  if (intelTimestamp) {
    const date = rec.timestamp ? new Date(rec.timestamp * 1000) : null;
    intelTimestamp.textContent =
      date && !Number.isNaN(date.getTime())
        ? `📅 ${date.toLocaleString()}`
        : "📅 Unknown";
  }
  if (intelDuration) intelDuration.textContent = `⏱️ Duration: ${rec.duration || 0}s`;
  if (intelOfficer) {
    intelOfficer.textContent = `👮 Handled by: ${rec.officer_id || "UNASSIGNED"}`;
  }
  if (intelGatewayVal) {
    intelGatewayVal.textContent = `${rec.dialed_number || "2007"} (Emergency Line)`;
  }
  if (intelCodecVal) {
    intelCodecVal.textContent = rec.mime_type || "WebM / Opus";
  }
  if (intelFilesizeVal) {
    intelFilesizeVal.textContent = rec.size_bytes
      ? `${Math.max(1, Math.round(rec.size_bytes / 1024))} KB`
      : "0 KB";
  }
  if (intelOutcomeVal) {
    intelOutcomeVal.textContent = rec.disposition || "COMPLETED & ARCHIVED";
  }
  if (intelNotesTextarea) intelNotesTextarea.value = rec.notes || "";
  if (dispositionSelect) dispositionSelect.value = rec.disposition || "";

  // Channel selector + integrity summary
  const trackUrls = rec.track_urls || {};
  const trackNames = Object.keys(trackUrls);
  if (trackSelect) {
    trackSelect.innerHTML = "";
    (trackNames.length ? trackNames : ["mixed"]).forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = TRACK_LABELS[name] || name;
      if (!trackUrls[name]) option.disabled = true;
      trackSelect.appendChild(option);
    });
    trackSelect.value = trackNames[0] || "mixed";
  }
  setPlayerTrack(rec, trackSelect ? trackSelect.value : "mixed");

  if (custodyNote) {
    const chainHash = (rec.chain || {}).hash || "";
    const hashes = rec.sha256 ? Object.keys(rec.sha256).length : 0;
    custodyNote.textContent = hashes
      ? `🔒 ${hashes} hashed track(s) · chain ${chainHash.slice(0, 12) || "—"}…`
      : "🔒 No integrity hash recorded (legacy capture).";
  }

  // Actions
  const url = safeRecordingUrl(rec);
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
  if (packetBtn) {
    packetBtn.href = recordingPacketUrl(rec.meta_name);
    packetBtn.classList.remove("hidden");
  }

  // Forensic dossier
  if (rec.forensic_analysis) {
    renderForensicDossier(rec.forensic_analysis);
    if (forensicBtnText) forensicBtnText.textContent = "[ 🔄 RE-ANALYZE INCIDENT & EMOTIONS ]";
    if (forensicAnalyzeBtn) forensicAnalyzeBtn.classList.add("analyzed");
  } else {
    if (forensicDossierPanel) forensicDossierPanel.classList.add("hidden");
    if (forensicBtnText) forensicBtnText.textContent = "[ 🔍 FULL AI INCIDENT & EMOTION AUDIT ]";
    if (forensicAnalyzeBtn) forensicAnalyzeBtn.classList.remove("analyzed");
  }
}

function safeRecordingUrl(rec) {
  const url = typeof rec.url === "string" ? rec.url : "";
  return url.startsWith("/api/recordings/") || url.startsWith("https://") ? url : "";
}

function setPlayerTrack(rec, track) {
  if (!intelAudioPlayer) return;
  const urls = rec.track_urls || {};
  const target = urls[track] || safeRecordingUrl(rec);
  if (target) {
    intelAudioPlayer.src = target;
  } else {
    intelAudioPlayer.removeAttribute("src");
    intelAudioPlayer.load();
  }
}

/**
 * Render the dossier using the canonical schema produced by
 * ``backend.app.ai._normalize_forensics``. Those key names are the contract —
 * do not rename here without updating the backend and its contract test.
 */
export function renderForensicDossier(forensics) {
  if (!forensicDossierPanel || !forensics) return;

  const verdict = forensics.triage_verdict || {};
  const psych = forensics.psychological_evaluation || {};
  if (forensicCategoryTag) {
    forensicCategoryTag.textContent = textOr(verdict.category, "CYBER INCIDENT");
  }
  if (forensicPriorityBadge) {
    const prio =
      typeof verdict.priority_score === "number" ? verdict.priority_score : 45;
    forensicPriorityBadge.textContent = `⚡ PRIORITY: ${prio}/100`;
    forensicPriorityBadge.className = `dossier-prio-badge ${
      prio >= 80 ? "score-critical" : prio >= 50 ? "score-high" : "score-medium"
    }`;
  }
  if (forensicVulnerabilityBadge) {
    const vulnerability = textOr(
      verdict.vulnerability_assessment || psych.vulnerability_level,
      "UNKNOWN"
    ).toUpperCase();
    forensicVulnerabilityBadge.textContent = `${vulnerability} VULNERABILITY`;
  }

  const caller = forensics.caller_dossier || {};
  if (fCallerName) fCallerName.textContent = textOr(caller.name);
  if (fCallerContact) fCallerContact.textContent = textOr(caller.contact, "Emergency Line");
  if (fCallerLocation) fCallerLocation.textContent = textOr(caller.location);
  if (fCallerLang) fCallerLang.textContent = textOr(caller.language_spoken, "Not detected");

  const suspect = forensics.suspect_profile || {};
  if (fSuspectIdentity) fSuspectIdentity.textContent = textOr(suspect.claimed_identity, "Unknown");
  if (fSuspectAgency) {
    fSuspectAgency.textContent = textOr(suspect.impersonated_agency, "Unknown / Not Disclosed");
  }
  if (fSuspectPhones) fSuspectPhones.textContent = joinList(suspect.phone_numbers);
  if (fSuspectHandles) fSuspectHandles.textContent = joinList(suspect.digital_handles);

  const finance = forensics.financial_impact || {};
  if (fAmountLost) fAmountLost.textContent = textOr(finance.amount_lost, "₹0");
  if (fAmountDemanded) fAmountDemanded.textContent = textOr(finance.amount_demanded, "None reported");
  if (fDestinationUpi) fDestinationUpi.textContent = joinList(finance.destination_upi_ids, "None reported");
  if (fVictimBank) fVictimBank.textContent = textOr(finance.victim_bank, "Unknown");

  const vector = forensics.attack_vector || {};
  if (fScamType) fScamType.textContent = textOr(vector.scam_type, "Unclassified");
  if (fCoercion) fCoercion.textContent = textOr(vector.coercion_technique, "Not identified");
  if (fMaliciousTools) fMaliciousTools.textContent = joinList(vector.malicious_tools);

  const emo = forensics.emotions || {};
  const dominant = String(emo.dominant || "unknown").toUpperCase();
  const estimated = emo.source === "acoustic";
  if (fDominantEmotionBadge) {
    fDominantEmotionBadge.textContent = estimated
      ? `DOMINANT: ${dominant} (ESTIMATED)`
      : `DOMINANT: ${dominant}`;
    fDominantEmotionBadge.title = estimated
      ? "No SER model was reachable; these values are a prosody-based estimate, not a measured emotion."
      : `Emotion source: ${emo.source || "unknown"}`;
  }
  const rawDistress = emo.vocal_distress_score;
  const distressScore =
    typeof rawDistress === "number"
      ? rawDistress <= 1
        ? Math.round(rawDistress * 100)
        : Math.round(rawDistress)
      : 0;
  if (fDistressScoreBadge) {
    fDistressScoreBadge.textContent = `ACOUSTIC DISTRESS: ${distressScore}%`;
  }

  const formatPct = (val) => {
    if (typeof val !== "number") return 0;
    return val <= 1 ? Math.min(100, Math.round(val * 100)) : Math.min(100, Math.round(val));
  };

  [
    [dbarFear, dvalFear, emo.fear],
    [dbarDistress, dvalDistress, emo.distress],
    [dbarAnger, dvalAnger, emo.anger],
    [dbarNervousness, dvalNervousness, emo.nervousness],
    [dbarNeutral, dvalNeutral, emo.neutral],
  ].forEach(([bar, label, value]) => {
    const pct = formatPct(value);
    if (bar) bar.style.width = `${pct}%`;
    if (label) label.textContent = `${pct}%`;
  });

  if (fPsychSummary) {
    const stateLabel = textOr(psych.dominant_state, "Undetermined");
    const summary = textOr(psych.summary, "No assessment available.");
    fPsychSummary.textContent = `[${stateLabel}] ${summary}`;
  }

  if (fFactsTimeline) {
    fFactsTimeline.innerHTML = "";
    const facts =
      Array.isArray(forensics.key_facts_timeline) && forensics.key_facts_timeline.length > 0
        ? forensics.key_facts_timeline
        : ["Caller connected to emergency line and reported fraudulent incident."];
    facts.forEach((fact) => {
      const li = document.createElement("li");
      li.textContent = fact;
      fFactsTimeline.appendChild(li);
    });
  }

  if (fActionableSop) {
    fActionableSop.innerHTML = "";
    const sops =
      Array.isArray(forensics.actionable_investigation_sop) &&
      forensics.actionable_investigation_sop.length > 0
        ? forensics.actionable_investigation_sop
        : [
            "Direct victim to 1930 / cybercrime.gov.in immediately.",
            "Log caller phone number and freeze any reported fraud UPI handles via NPCI portal.",
            "Advise victim to disconnect any suspicious remote-access software.",
          ];
    sops.forEach((sop) => {
      const li = document.createElement("li");
      li.textContent = sop;
      fActionableSop.appendChild(li);
    });
  }

  forensicDossierPanel.classList.remove("hidden");
}

async function handleForensicAudit() {
  if (!state.selectedRecording || !state.selectedRecording.meta_name) {
    showToast("Please select a recording from the archive first.", "warn");
    return;
  }

  if (forensicAnalyzeBtn) forensicAnalyzeBtn.disabled = true;
  if (forensicBtnText) {
    forensicBtnText.textContent = "[ ⏳ AUDITING RECORDING & RUNNING SER SPECTRUM... ]";
  }

  try {
    const analysis = await analyzeRecordingForensics(state.selectedRecording.meta_name);
    state.selectedRecording.forensic_analysis = analysis;
    renderForensicDossier(analysis);
    if (forensicBtnText) {
      forensicBtnText.textContent = "[ 🔄 RE-ANALYZE INCIDENT & EMOTIONS ]";
    }
    if (forensicAnalyzeBtn) forensicAnalyzeBtn.classList.add("analyzed");
    if (forensicDossierPanel) {
      forensicDossierPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    const cached = state.cachedRecordings.find(
      (rec) => rec.meta_name === state.selectedRecording.meta_name
    );
    if (cached) cached.has_analysis = true;
    renderArchiveList(state.cachedRecordings);
  } catch (error) {
    console.error("Forensic analysis error:", error);
    showToast(error.message || "Could not complete forensic analysis.", "error");
    if (forensicBtnText) {
      forensicBtnText.textContent = "[ 🔍 FULL AI INCIDENT & EMOTION AUDIT ]";
    }
  } finally {
    if (forensicAnalyzeBtn) forensicAnalyzeBtn.disabled = false;
  }
}

async function handleVerify() {
  const rec = state.selectedRecording;
  if (!rec || !rec.meta_name) {
    showToast("Select a recording first.", "warn");
    return;
  }
  if (verifyAudioBtn) verifyAudioBtn.disabled = true;
  try {
    const report = await verifyRecording(rec.meta_name);
    const parts = Object.entries(report.tracks || {}).map(
      ([name, status]) => `${name}: ${status}`
    );
    if (custodyNote) {
      custodyNote.textContent = report.intact
        ? `✅ Evidence intact · ${parts.join(" · ")}`
        : `⚠ VERIFY FAILED · ${parts.join(" · ")}`;
    }
    showToast(
      report.intact
        ? "Evidence verified: all hashes match."
        : `Evidence verification failed: ${parts.join(", ")}`,
      report.intact ? "success" : "error",
      9000
    );
  } catch (error) {
    showToast(error.message || "Could not verify the recording.", "error");
  } finally {
    if (verifyAudioBtn) verifyAudioBtn.disabled = false;
  }
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
  fetchRecordingsVault({ reset: true });
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
    showToast("Select a recording before saving notes.", "warn");
    return;
  }
  saveIntelNotesBtn.disabled = true;
  try {
    const disposition = dispositionSelect ? dispositionSelect.value : "";
    const data = await saveRecordingNotes(
      state.selectedRecording.meta_name,
      intelNotesTextarea.value,
      disposition
    );
    state.selectedRecording.notes = data.notes || intelNotesTextarea.value;
    state.selectedRecording.disposition = data.disposition || disposition;
    if (intelOutcomeVal) {
      intelOutcomeVal.textContent = state.selectedRecording.disposition || "COMPLETED & ARCHIVED";
    }
    const cached = state.cachedRecordings.find(
      (rec) => rec.meta_name === state.selectedRecording.meta_name
    );
    if (cached) {
      cached.notes_preview = state.selectedRecording.notes.slice(0, 120);
      cached.disposition = state.selectedRecording.disposition;
    }
    renderArchiveList(state.cachedRecordings);
    showToast("Officer dispatch notes saved.", "success");
  } catch (error) {
    showToast(error.message || "Could not save notes.", "error");
  } finally {
    saveIntelNotesBtn.disabled = false;
  }
}

async function handleDeleteRecording() {
  const rec = state.selectedRecording;
  if (!rec || !rec.meta_name) {
    showToast("Select a recording before deleting.", "warn");
    return;
  }
  const label = rec.filename || rec.meta_name;
  if (!window.confirm(`Permanently delete this recording and all of its audio?\n\n${label}`)) {
    return;
  }
  if (deleteRecordingBtn) deleteRecordingBtn.disabled = true;
  try {
    await deleteRecording(rec.meta_name);
    state.cachedRecordings = state.cachedRecordings.filter(
      (item) => item.meta_name !== rec.meta_name
    );
    state.selectedRecording = null;
    if (intelDetailView) intelDetailView.classList.add("hidden");
    if (intelEmptyPrompt) intelEmptyPrompt.classList.remove("hidden");
    if (intelAudioPlayer) {
      intelAudioPlayer.pause();
      intelAudioPlayer.removeAttribute("src");
      intelAudioPlayer.load();
    }
    if (vaultBadgeCount) vaultBadgeCount.textContent = String(state.recordingsTotal - 1);
    if (sidebarCount) sidebarCount.textContent = String(state.recordingsTotal - 1);
    renderArchiveList(state.cachedRecordings);
    showToast("Recording deleted.", "success");
  } catch (error) {
    showToast(error.message || "Could not delete recording.", "error");
  } finally {
    if (deleteRecordingBtn) deleteRecordingBtn.disabled = false;
  }
}

let searchTimer = null;

export function initVault() {
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
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.recordingsQuery = intelSearchInput.value.trim();
        fetchRecordingsVault({ reset: true });
      }, 250);
    });
  }

  if (trackSelect && intelAudioPlayer) {
    trackSelect.addEventListener("change", () => {
      if (state.selectedRecording) {
        setPlayerTrack(state.selectedRecording, trackSelect.value);
      }
    });
  }

  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", () => fetchRecordingsVault());
  }
  if (saveIntelNotesBtn) {
    saveIntelNotesBtn.addEventListener("click", handleSaveNotes);
  }
  if (refreshVaultBtn) {
    refreshVaultBtn.addEventListener("click", () =>
      fetchRecordingsVault({ reset: true })
    );
  }
  if (forensicAnalyzeBtn) {
    forensicAnalyzeBtn.addEventListener("click", handleForensicAudit);
  }
  if (deleteRecordingBtn) {
    deleteRecordingBtn.addEventListener("click", handleDeleteRecording);
  }
  if (verifyAudioBtn) {
    verifyAudioBtn.addEventListener("click", handleVerify);
  }
  if (packetBtn) {
    packetBtn.addEventListener("click", (event) => {
      if (!state.selectedRecording) {
        event.preventDefault();
        showToast("Select a recording first.", "warn");
      }
    });
  }

  on("vault:refresh", () => fetchRecordingsVault({ reset: true }));
}
