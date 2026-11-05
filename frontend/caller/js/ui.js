/** Caller UI state: status pill, timer, mute control, and screen switching. */

import { state } from "./state.js";
import {
  callStatusPill,
  callStatusText,
  callTimer,
  dialpadScreen,
  incallScreen,
  languageSelect,
  micIconOff,
  micIconOn,
  muteLabel,
  officerCard,
  skipIvrBtn,
  tabHelplines,
  tabLanguage,
  tabPrivacy,
  textFallback,
  toggleMuteBtn,
} from "./dom.js";
import { showToast } from "./utils.js";
import { extendStatement } from "./vad.js";

const LANGUAGES = ["en-IN", "hi-IN", "mr-IN"];
const LANGUAGE_LABELS = {
  "en-IN": "English",
  "hi-IN": "Hindi",
  "mr-IN": "Marathi",
};

export function setCallStatus(text, statusClass) {
  callStatusText.textContent = text;
  callStatusPill.className = `status-pill ${statusClass}`;
}

export function setMicEnabled(enabled) {
  if (state.localStream) {
    state.localStream.getAudioTracks().forEach((track) => {
      track.enabled = enabled;
    });
  }
  state.isMuted = !enabled;

  if (state.isMuted) {
    toggleMuteBtn.classList.add("active");
    muteLabel.textContent = "Unmute";
    micIconOn.classList.add("hidden");
    micIconOff.classList.remove("hidden");
  } else {
    toggleMuteBtn.classList.remove("active");
    muteLabel.textContent = "Mute";
    micIconOn.classList.remove("hidden");
    micIconOff.classList.add("hidden");
  }
}

export function startCallTimer() {
  if (state.timerInterval) clearInterval(state.timerInterval);
  state.callSeconds = 0;
  callTimer.textContent = "00:00";
  state.timerInterval = setInterval(() => {
    state.callSeconds++;
    const mins = String(Math.floor(state.callSeconds / 60)).padStart(2, "0");
    const secs = String(state.callSeconds % 60).padStart(2, "0");
    callTimer.textContent = `${mins}:${secs}`;
  }, 1000);
}

export function stopCallTimer() {
  if (state.timerInterval) {
    clearInterval(state.timerInterval);
    state.timerInterval = null;
  }
  callTimer.textContent = "00:00";
}

export function resetCallUi() {
  officerCard.classList.add("hidden");
  callTimer.textContent = "00:00";
  setMicEnabled(true);
  incallScreen.classList.remove("active");
  dialpadScreen.classList.add("active");
  skipIvrBtn.classList.add("hidden");
  if (textFallback) textFallback.classList.add("hidden");
}

function applyLanguage(code) {
  state.language = LANGUAGES.includes(code) ? code : "en-IN";
  if (languageSelect) languageSelect.value = state.language;
}

export function initQuickActions() {
  if (languageSelect) {
    languageSelect.value = state.language;
    languageSelect.addEventListener("change", () => {
      applyLanguage(languageSelect.value);
      showToast(
        `Language set to ${LANGUAGE_LABELS[state.language]}. The greeting follows it.`,
        "info"
      );
    });
  }

  if (tabLanguage) {
    tabLanguage.addEventListener("click", () => {
      const next = (LANGUAGES.indexOf(state.language) + 1) % LANGUAGES.length;
      applyLanguage(LANGUAGES[next]);
      showToast(`Language: ${LANGUAGE_LABELS[state.language]}`, "info");
    });
  }

  if (tabHelplines) {
    tabHelplines.addEventListener("click", () => {
      showToast(
        "Cyber Crime Helpline 1930 · Emergency 112 · Police 100 · Report online at cybercrime.gov.in",
        "info",
        9000
      );
    });
  }

  if (tabPrivacy) {
    tabPrivacy.addEventListener("click", () => {
      showToast(
        "This call is recorded and transcribed so police can act on your report. " +
          "It is stored in the station evidence vault and is only accessible to authenticated officers.",
        "info",
        11000
      );
    });
  }

  // `*` during the statement buys another ten seconds, like a phone IVR.
  document.addEventListener("keydown", (event) => {
    if (event.key === "*" && state.vadActive) {
      event.preventDefault();
      extendStatement();
    }
  });
}
