/** Caller UI state: status pill, timer, mute control, and screen switching. */

import { state } from "./state.js";
import {
  callStatusPill,
  callStatusText,
  callTimer,
  dialpadScreen,
  incallScreen,
  micIconOff,
  micIconOn,
  muteLabel,
  officerCard,
  skipIvrBtn,
  toggleMuteBtn,
} from "./dom.js";

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
}
