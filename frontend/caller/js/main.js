/** Caller dialer bootstrap and input wiring. */

import { state } from "./state.js";
import {
  callBtn,
  clearBtn,
  hangupBtn,
  keyBtns,
  phoneInput,
  toggleMuteBtn,
} from "./dom.js";
import { DTMF_FREQUENCIES, playTone } from "./utils.js";
import { setMicEnabled } from "./ui.js";
import { startEmergencyCall, terminateCall } from "./call.js";

function bindKeypad() {
  keyBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      if (phoneInput.value.length < 8) {
        phoneInput.value += key;
      }
      const freqs = DTMF_FREQUENCIES[key] || [800, 1200];
      playTone(freqs[0], freqs[1]);
    });
  });
}

function bindCallControls() {
  clearBtn.addEventListener("click", () => {
    phoneInput.value = phoneInput.value.slice(0, -1);
  });

  callBtn.addEventListener("click", async () => {
    state.dialedNumber = phoneInput.value.trim() || "2007";
    await startEmergencyCall(state.dialedNumber);
  });

  toggleMuteBtn.addEventListener("click", () => {
    setMicEnabled(state.isMuted);
  });

  hangupBtn.addEventListener("click", () => {
    terminateCall(true);
  });
}

bindKeypad();
bindCallControls();
