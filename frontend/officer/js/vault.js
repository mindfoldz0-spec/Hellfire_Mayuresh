import { state } from "./state.js";
import {
  closeIntelModalBtn,
  dbarAnger,
  dbarDistress,
  dbarFear,
  dbarNervousness,
  dbarNeutral,
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
import {
  analyzeRecordingForensics,
  fetchRecordings,
  saveRecordingNotes,
} from "./api.js";

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

  // Forensic dossier state management
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

export function renderForensicDossier(forensics) {
  if (!forensicDossierPanel || !forensics) return;

  // 1. Triage Verdict & Header Badges
  const verdict = forensics.triage_verdict || {};
  if (forensicCategoryTag) {
    forensicCategoryTag.textContent = verdict.category || "CYBER EXTORTION";
  }
  if (forensicPriorityBadge) {
    const prio = typeof verdict.priority_score === "number" ? verdict.priority_score : 85;
    forensicPriorityBadge.textContent = `⚡ PRIORITY: ${prio}/100`;
    forensicPriorityBadge.className = `dossier-prio-badge ${
      prio >= 80 ? "score-critical" : prio >= 50 ? "score-high" : "score-medium"
    }`;
  }
  if (forensicVulnerabilityBadge) {
    forensicVulnerabilityBadge.textContent =
      verdict.vulnerability_assessment || "VULNERABILITY DETECTED";
  }

  // 2. Caller / Victim Dossier
  const caller = forensics.caller_dossier || {};
  if (fCallerName) fCallerName.textContent = caller.name || "Not Disclosed";
  if (fCallerContact) fCallerContact.textContent = caller.contact_number || "Emergency Line";
  if (fCallerLocation) fCallerLocation.textContent = caller.location_city || "Not Disclosed";
  if (fCallerLang) fCallerLang.textContent = caller.spoken_language || "English / Hindi / Marathi";

  // 3. Suspect Profile & Impersonation
  const suspect = forensics.suspect_profile || {};
  if (fSuspectIdentity) fSuspectIdentity.textContent = suspect.claimed_identity || "Unknown";
  if (fSuspectAgency) fSuspectAgency.textContent = suspect.claimed_agency || "Unknown / Not Disclosed";
  if (fSuspectPhones) {
    const phones = Array.isArray(suspect.phone_numbers) && suspect.phone_numbers.length > 0
      ? suspect.phone_numbers.join(", ")
      : "None logged";
    fSuspectPhones.textContent = phones;
  }
  if (fSuspectHandles) {
    const handles = Array.isArray(suspect.digital_handles) && suspect.digital_handles.length > 0
      ? suspect.digital_handles.join(", ")
      : "None logged";
    fSuspectHandles.textContent = handles;
  }

  // 4. Financial Damages & Traces
  const finance = forensics.financial_impact || {};
  if (fAmountLost) fAmountLost.textContent = finance.amount_lost_inr || "₹0";
  if (fAmountDemanded) fAmountDemanded.textContent = finance.amount_demanded_inr || "None reported";
  if (fDestinationUpi) {
    const upis = Array.isArray(finance.destination_upi_ids) && finance.destination_upi_ids.length > 0
      ? finance.destination_upi_ids.join(", ")
      : "None reported";
    fDestinationUpi.textContent = upis;
  }
  if (fVictimBank) fVictimBank.textContent = finance.victim_bank_name || "Unknown / Not Disclosed";

  // 5. Attack Vector & Coercion Mechanism
  const vector = forensics.attack_vector || {};
  if (fScamType) fScamType.textContent = vector.scam_type || "Digital Impersonation";
  if (fCoercion) fCoercion.textContent = vector.coercion_mechanism || "Psychological Coercion";
  if (fMaliciousTools) {
    const tools = Array.isArray(vector.malicious_tools_or_apks) && vector.malicious_tools_or_apks.length > 0
      ? vector.malicious_tools_or_apks.join(", ")
      : "None logged";
    fMaliciousTools.textContent = tools;
  }

  // 6. Speech Emotion Spectrum (AWS SER + Acoustic Prosody)
  const emo = forensics.emotions || {};
  const dominant = (emo.dominant || "Distressed").toUpperCase();
  if (fDominantEmotionBadge) {
    fDominantEmotionBadge.textContent = `DOMINANT: ${dominant}`;
  }
  const distressScore = typeof emo.distress_score === "number" ? emo.distress_score : 80;
  if (fDistressScoreBadge) {
    fDistressScoreBadge.textContent = `ACOUSTIC DISTRESS: ${distressScore}%`;
  }

  function formatPct(val) {
    if (typeof val !== "number") return 0;
    return val <= 1 ? Math.min(100, Math.round(val * 100)) : Math.min(100, Math.round(val));
  }

  const fearPct = formatPct(emo.fear);
  const distressPct = formatPct(emo.distress);
  const angerPct = formatPct(emo.anger);
  const nervPct = formatPct(emo.nervousness);
  const neutralPct = formatPct(emo.neutral);

  if (dbarFear) dbarFear.style.width = `${fearPct}%`;
  if (dvalFear) dvalFear.textContent = `${fearPct}%`;

  if (dbarDistress) dbarDistress.style.width = `${distressPct}%`;
  if (dvalDistress) dvalDistress.textContent = `${distressPct}%`;

  if (dbarAnger) dbarAnger.style.width = `${angerPct}%`;
  if (dvalAnger) dvalAnger.textContent = `${angerPct}%`;

  if (dbarNervousness) dbarNervousness.style.width = `${nervPct}%`;
  if (dvalNervousness) dvalNervousness.textContent = `${nervPct}%`;

  if (dbarNeutral) dbarNeutral.style.width = `${neutralPct}%`;
  if (dvalNeutral) dvalNeutral.textContent = `${neutralPct}%`;

  // 7. Psychological Assessment Paragraph
  if (fPsychSummary) {
    fPsychSummary.textContent =
      forensics.psychological_evaluation ||
      "Caller exhibited high distress patterns with marked prosodic tremors indicative of acute coercion pressure.";
  }

  // 8. Key Spoken Facts Timeline
  if (fFactsTimeline) {
    fFactsTimeline.innerHTML = "";
    const facts = Array.isArray(forensics.key_facts_timeline) && forensics.key_facts_timeline.length > 0
      ? forensics.key_facts_timeline
      : ["Caller connected to emergency line and reported fraudulent incident."];
    facts.forEach((fact) => {
      const li = document.createElement("li");
      li.textContent = fact;
      fFactsTimeline.appendChild(li);
    });
  }

  // 9. Actionable Police Investigation SOP Checklist
  if (fActionableSop) {
    fActionableSop.innerHTML = "";
    const sops = Array.isArray(forensics.actionable_investigation_sop) && forensics.actionable_investigation_sop.length > 0
      ? forensics.actionable_investigation_sop
      : [
          "Direct victim to 1930 / cybercrime.gov.in immediately.",
          "Log caller phone number and freeze any reported fraud UPI handles via NPCI portal.",
          "Advise victim to disconnect any suspicious remote-access software."
        ];
    sops.forEach((sop) => {
      const li = document.createElement("li");
      li.textContent = sop;
      fActionableSop.appendChild(li);
    });
  }

  // Make dossier visible
  forensicDossierPanel.classList.remove("hidden");
}

async function handleForensicAudit() {
  if (!state.selectedRecording || !state.selectedRecording.meta_name) {
    alert("Please select a recording from the archive first.");
    return;
  }

  if (forensicAnalyzeBtn) forensicAnalyzeBtn.disabled = true;
  if (forensicBtnText) {
    forensicBtnText.textContent = "[ ⏳ AUDITING RECORDING & RUNNING SER SPECTRUM... ]";
  }

  try {
    const analysis = await analyzeRecordingForensics(state.selectedRecording.meta_name);
    state.selectedRecording.forensic_analysis = analysis;
    const cached = state.cachedRecordings.find(
      (rec) => rec.meta_name === state.selectedRecording.meta_name
    );
    if (cached) cached.forensic_analysis = analysis;

    renderForensicDossier(analysis);
    if (forensicBtnText) {
      forensicBtnText.textContent = "[ 🔄 RE-ANALYZE INCIDENT & EMOTIONS ]";
    }
    if (forensicAnalyzeBtn) forensicAnalyzeBtn.classList.add("analyzed");

    if (forensicDossierPanel) {
      forensicDossierPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  } catch (error) {
    console.error("Forensic analysis error:", error);
    alert(error.message || "Could not complete forensic analysis.");
    if (forensicBtnText) {
      forensicBtnText.textContent = "[ 🔍 FULL AI INCIDENT & EMOTION AUDIT ]";
    }
  } finally {
    if (forensicAnalyzeBtn) forensicAnalyzeBtn.disabled = false;
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
  if (forensicAnalyzeBtn) {
    forensicAnalyzeBtn.addEventListener("click", handleForensicAudit);
  }

  on("vault:refresh", fetchRecordingsVault);
}
