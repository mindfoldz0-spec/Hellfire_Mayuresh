/** Cached DOM references and small DOM helpers for the officer console. */

export const officerSelect = document.getElementById("officer-select");
export const gatewayStatus = document.getElementById("gateway-status");
export const systemClock = document.getElementById("system-clock");
export const queueCountBadge = document.getElementById("queue-count-badge");
export const refreshQueueBtn = document.getElementById("refresh-queue-btn");
export const queueEmptyState = document.getElementById("queue-empty-state");
export const queueList = document.getElementById("queue-list");

export const stationIdle = document.getElementById("station-idle");
export const standbyPhoneWrapper = document.getElementById("standby-phone-wrapper");
export const idleTitle = document.getElementById("idle-title");
export const idleSub = document.getElementById("idle-sub");
export const stationActive = document.getElementById("station-active");
export const activeCallIdEl = document.getElementById("active-call-id");
export const activeCallTimerEl = document.getElementById("active-call-timer");
export const incidentNotes = document.getElementById("incident-notes");
export const officerMuteBtn = document.getElementById("officer-mute-btn");
export const officerMuteText = document.getElementById("officer-mute-text");
export const endDispatchBtn = document.getElementById("end-dispatch-btn");
export const tagBtns = document.querySelectorAll(".tag-btn");

export const callerScope = document.getElementById("caller-scope");
export const officerScope = document.getElementById("officer-scope");
export const remoteCallerAudio = document.getElementById("remote-caller-audio");
export const recordingIndicator = document.getElementById("recording-indicator");

export const taskbarClock = document.getElementById("taskbar-clock");
export const taskbarStartBtn = document.getElementById("taskbar-start-btn");
export const startMenuPopup = document.getElementById("start-menu-popup");
export const menuIntelBtn = document.getElementById("menu-intel-btn");
export const menuRefreshBtn = document.getElementById("menu-refresh-btn");
export const menuAboutBtn = document.getElementById("menu-about-btn");
export const menuLogoutBtn = document.getElementById("menu-logout-btn");

export const openIntelBtn = document.getElementById("open-intel-btn");
export const headerVaultBtn = document.getElementById("header-vault-btn");
export const closeIntelModalBtn = document.getElementById("close-intel-modal-btn");
export const minIntelModalBtn = document.getElementById("min-intel-modal-btn");
export const maxIntelModalBtn = document.getElementById("max-intel-modal-btn");
export const retroWindowModal = document.getElementById("retro-window-modal");
export const intelModalBackdrop = document.getElementById("intel-modal-backdrop");
export const vaultBadgeCount = document.getElementById("vault-badge-count");
export const sidebarCount = document.getElementById("sidebar-count");
export const modalRecordingsList = document.getElementById("modal-recordings-list");
export const intelSearchInput = document.getElementById("intel-search-input");

export const intelEmptyPrompt = document.getElementById("intel-empty-prompt");
export const intelDetailView = document.getElementById("intel-detail-view");
export const intelCallId = document.getElementById("intel-call-id");
export const intelStatusBadge = document.getElementById("intel-status-badge");
export const intelTimestamp = document.getElementById("intel-timestamp");
export const intelDuration = document.getElementById("intel-duration");
export const intelOfficer = document.getElementById("intel-officer");
export const downloadAudioBtn = document.getElementById("download-audio-btn");
export const intelAudioPlayer = document.getElementById("intel-audio-player");
export const intelAudioFilename = document.getElementById("intel-audio-filename");
export const intelGatewayVal = document.getElementById("intel-gateway-val");
export const intelCodecVal = document.getElementById("intel-codec-val");
export const intelFilesizeVal = document.getElementById("intel-filesize-val");
export const intelOutcomeVal = document.getElementById("intel-outcome-val");
export const intelNotesTextarea = document.getElementById("intel-notes-textarea");
export const saveIntelNotesBtn = document.getElementById("save-intel-notes-btn");
export const refreshVaultBtn = document.getElementById("refresh-vault-btn");

export const authOverlay = document.getElementById("auth-overlay");
export const authOfficerSelect = document.getElementById("auth-officer-select");
export const authTokenInput = document.getElementById("auth-token-input");
export const authLoginBtn = document.getElementById("auth-login-btn");
export const authError = document.getElementById("auth-error");

// Deep AI Forensic & Emotional Intelligence Dossier Elements
export const forensicAnalyzeBtn = document.getElementById("forensic-analyze-btn");
export const forensicBtnText = document.getElementById("forensic-btn-text");
export const forensicDossierPanel = document.getElementById("forensic-dossier-panel");
export const forensicCategoryTag = document.getElementById("forensic-category-tag");
export const forensicPriorityBadge = document.getElementById("forensic-priority-badge");
export const forensicVulnerabilityBadge = document.getElementById("forensic-vulnerability-badge");

export const fCallerName = document.getElementById("f-caller-name");
export const fCallerContact = document.getElementById("f-caller-contact");
export const fCallerLocation = document.getElementById("f-caller-location");
export const fCallerLang = document.getElementById("f-caller-lang");

export const fSuspectIdentity = document.getElementById("f-suspect-identity");
export const fSuspectAgency = document.getElementById("f-suspect-agency");
export const fSuspectPhones = document.getElementById("f-suspect-phones");
export const fSuspectHandles = document.getElementById("f-suspect-handles");

export const fAmountLost = document.getElementById("f-amount-lost");
export const fAmountDemanded = document.getElementById("f-amount-demanded");
export const fDestinationUpi = document.getElementById("f-destination-upi");
export const fVictimBank = document.getElementById("f-victim-bank");

export const fScamType = document.getElementById("f-scam-type");
export const fCoercion = document.getElementById("f-coercion");
export const fMaliciousTools = document.getElementById("f-malicious-tools");

export const fDominantEmotionBadge = document.getElementById("f-dominant-emotion-badge");
export const fDistressScoreBadge = document.getElementById("f-distress-score-badge");

export const dbarFear = document.getElementById("dbar-fear");
export const dvalFear = document.getElementById("dval-fear");
export const dbarDistress = document.getElementById("dbar-distress");
export const dvalDistress = document.getElementById("dval-distress");
export const dbarAnger = document.getElementById("dbar-anger");
export const dvalAnger = document.getElementById("dval-anger");
export const dbarNervousness = document.getElementById("dbar-nervousness");
export const dvalNervousness = document.getElementById("dval-nervousness");
export const dbarNeutral = document.getElementById("dbar-neutral");
export const dvalNeutral = document.getElementById("dval-neutral");

export const fPsychSummary = document.getElementById("f-psych-summary");
export const fFactsTimeline = document.getElementById("f-facts-timeline");
export const fActionableSop = document.getElementById("f-actionable-sop");

export const gatewayDot =
  gatewayStatus?.parentElement?.querySelector(".live-dot") || null;

export function setGatewayStatus(text, dotClass) {
  if (gatewayStatus) gatewayStatus.textContent = text;
  if (gatewayDot) gatewayDot.className = `live-dot ${dotClass}`;
}
