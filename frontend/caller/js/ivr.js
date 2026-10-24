/** Automated IVR ringing, greeting, and beep sequence. */

import { state } from "./state.js";
import { ivrAudio, skipIvrBtn, visualizerCaption } from "./dom.js";
import { sendAction } from "./ws.js";
import { setCallStatus, setMicEnabled } from "./ui.js";
import { connectMicToVisualizer } from "./visualizer.js";

export function playIVRSequence() {
  setCallStatus("RINGING 2007 DISPATCH...", "status-connecting");
  visualizerCaption.textContent = "CONNECTING TO EMERGENCY GATEWAY...";
  skipIvrBtn.classList.remove("hidden");

  ivrAudio.currentTime = 0;
  ivrAudio.volume = 1.0;

  const timeUpdateHandler = () => {
    const duration = Number.isFinite(ivrAudio.duration) ? ivrAudio.duration : 0;
    const beepStart = duration > 3 ? Math.max(2.7, duration - 0.5) : 10.5;
    if (ivrAudio.currentTime < 2.7) {
      setCallStatus("RINGING DISPATCH...", "status-connecting");
      visualizerCaption.textContent = "RINGING 2007 EMERGENCY GATEWAY...";
    } else if (ivrAudio.currentTime < beepStart) {
      setCallStatus("AUTOMATED EMERGENCY ADVISORY", "status-connecting");
      visualizerCaption.textContent = "LISTEN TO INSTRUCTIONS (MIC MUTED)";
    } else {
      setCallStatus("TONE BEEP - PREPARE TO SPEAK", "status-connecting");
      visualizerCaption.textContent = "DESCRIBE SITUATION AFTER BEEP";
    }
  };

  state.ivrTimeUpdateHandler = timeUpdateHandler;
  ivrAudio.addEventListener("timeupdate", timeUpdateHandler);

  ivrAudio.onended = () => finishIvr("ended");

  ivrAudio
    .play()
    .then(() => {
      if (state.ivrFinished) return;
      sendAction({ action: "IVR_STARTED" });
      // Defensive fallback in case the media element never fires "ended".
      const duration = Number.isFinite(ivrAudio.duration) ? ivrAudio.duration : 0;
      const fallbackDelay = duration > 0 ? duration * 1000 + 2500 : 30000;
      clearTimeout(state.ivrFallbackTimer);
      state.ivrFallbackTimer = setTimeout(
        () => finishIvr("timeout"),
        fallbackDelay
      );
    })
    .catch((error) => {
      console.warn("IVR audio playback blocked:", error);
      // Autoplay policies can block audio. Do not leave the caller muted and
      // the queue stuck forever; let the user proceed after a short delay.
      clearTimeout(state.ivrFallbackTimer);
      state.ivrFallbackTimer = setTimeout(
        () => finishIvr("playback_failed"),
        1500
      );
    });

  skipIvrBtn.onclick = () => finishIvr("skipped");
}

import { cancelVadAndHold, startVadAndRecording } from "./vad.js";

export function finishIvr(reason) {
  if (state.ivrFinished) return;
  state.ivrFinished = true;
  clearTimeout(state.ivrFallbackTimer);
  state.ivrFallbackTimer = null;
  if (state.ivrTimeUpdateHandler) {
    ivrAudio.removeEventListener("timeupdate", state.ivrTimeUpdateHandler);
    state.ivrTimeUpdateHandler = null;
  }
  try {
    ivrAudio.pause();
  } catch (e) {}
  skipIvrBtn.classList.add("hidden");

  console.log(`[IVR] Sequence finished (${reason}); unmuting mic & starting VAD.`);
  setMicEnabled(true);
  connectMicToVisualizer();

  setCallStatus("MICROPHONE LIVE - DESCRIBE INCIDENT", "status-connecting");
  visualizerCaption.textContent = "MICROPHONE ACTIVE - SPEAK YOUR EMERGENCY MESSAGE";

  sendAction({ action: "IVR_FINISHED" });

  // Start voice activity detection and statement recording
  startVadAndRecording(state.localStream);
}

/** Stop IVR playback without sending IVR_FINISHED (used on hangup). */
export function cancelIvr() {
  clearTimeout(state.ivrFallbackTimer);
  state.ivrFallbackTimer = null;
  if (state.ivrTimeUpdateHandler) {
    ivrAudio.removeEventListener("timeupdate", state.ivrTimeUpdateHandler);
    state.ivrTimeUpdateHandler = null;
  }
  try {
    ivrAudio.pause();
    ivrAudio.currentTime = 0;
  } catch (e) {}
  skipIvrBtn.classList.add("hidden");
  cancelVadAndHold();
}
