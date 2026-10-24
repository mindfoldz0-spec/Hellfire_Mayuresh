/** Console chrome: clocks, retro start menu, and station standby widget. */

import {
  menuAboutBtn,
  menuLogoutBtn,
  menuRefreshBtn,
  refreshQueueBtn,
  standbyPhoneWrapper,
  startMenuPopup,
  systemClock,
  taskbarClock,
  taskbarStartBtn,
} from "./dom.js";
import { logout } from "./auth.js";
import { syncQueue } from "./queue.js";

function updateClocks() {
  const now = new Date();
  if (systemClock) {
    systemClock.textContent = `${now.toISOString().substring(11, 19)} UTC`;
  }
  if (taskbarClock) {
    taskbarClock.textContent = `${now.toTimeString().substring(0, 8)} LOC`;
  }
}

export function initUi() {
  setInterval(updateClocks, 1000);
  updateClocks();

  if (taskbarStartBtn && startMenuPopup) {
    taskbarStartBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      startMenuPopup.classList.toggle("hidden");
    });

    document.addEventListener("click", (event) => {
      if (
        !startMenuPopup.contains(event.target) &&
        event.target !== taskbarStartBtn
      ) {
        startMenuPopup.classList.add("hidden");
      }
    });

    if (menuRefreshBtn) {
      menuRefreshBtn.addEventListener("click", () => {
        startMenuPopup.classList.add("hidden");
        syncQueue();
      });
    }

    if (menuAboutBtn) {
      menuAboutBtn.addEventListener("click", () => {
        startMenuPopup.classList.add("hidden");
        alert(
          "CERS-OS v2.4 (Cyber Emergency Response System)\nProtocol: Atomic Lockout Active\nAudio Codec: Opus WebRTC (48kHz)\nOfficer sessions: signed HttpOnly cookies"
        );
      });
    }

    if (menuLogoutBtn) {
      menuLogoutBtn.addEventListener("click", logout);
    }
  }

  if (standbyPhoneWrapper) {
    standbyPhoneWrapper.addEventListener("click", () => {
      standbyPhoneWrapper.classList.toggle("ringing");
    });
  }

  if (refreshQueueBtn) {
    refreshQueueBtn.addEventListener("click", syncQueue);
  }
}
