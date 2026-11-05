/** Emergency call lifecycle: session creation, signalling, and teardown. */

import { state } from "./state.js";
import {
  dialpadScreen,
  incallNumberTitle,
  incallScreen,
  officerCard,
  officerNameDisplay,
  textFallback,
  textStatement,
  sendTextBtn,
  visualizerCaption,
  visualizerWrapper,
  incallGrid,
} from "./dom.js";
import { createCallSession, sendTextStatement } from "./api.js";
import { closeSocket, connectCallerSocket, sendAction } from "./ws.js";
import { cancelIvr, playIVRSequence } from "./ivr.js";
import {
  closePeerConnection,
  flushPendingCandidates,
  initPeerConnection,
} from "./webrtc.js";
import { setupVisualizer, teardownVisualizer } from "./visualizer.js";
import {
  resetCallUi,
  setCallStatus,
  setMicEnabled,
  stopCallTimer,
} from "./ui.js";
import { showToast } from "./utils.js";
import { cancelVadAndHold, stopHoldMusic } from "./vad.js";

export async function startEmergencyCall(number) {
  if (state.sessionId) return;

  // Ask for the microphone first, but do not dead-end the caller if it is
  // unavailable: fall through to a typed statement instead.
  try {
    state.localStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: false,
    });
    setMicEnabled(false);
    state.textOnly = false;
  } catch (micError) {
    console.warn("Microphone unavailable, switching to text mode:", micError);
    state.localStream = null;
    state.textOnly = true;
  }

  try {
    const created = await createCallSession(number, "");
    state.sessionId = created.call_id;
    state.callToken = created.call_token || null;
    state.ivrFinished = false;
    state.statementSent = false;
    state.vadActive = false;

    dialpadScreen.classList.remove("active");
    incallScreen.classList.add("active");
    incallNumberTitle.textContent = number;

    if (state.textOnly) {
      showTextMode();
    } else {
      setCallStatus("CONNECTING GATEWAY...", "status-connecting");
      visualizerCaption.textContent = "ESTABLISHING SECURE WEBSOCKET...";
      setupVisualizer();
    }

    connectCallerSocket(state.sessionId, {
      token: state.callToken,
      onOpen: () => {
        if (state.textOnly) {
          sendAction({ action: "IVR_FINISHED" });
        } else {
          playIVRSequence();
        }
      },
      onMessage: handleWebSocketMessage,
      onClose: () => {
        if (state.isTerminating) return;
        if (state.sessionId) {
          console.warn("[Caller WS] Socket disconnected while session active. Reconnecting...");
          setTimeout(() => {
            if (
              state.sessionId &&
              !state.isTerminating &&
              (!state.ws || state.ws.readyState !== WebSocket.OPEN)
            ) {
              connectCallerSocket(state.sessionId, {
                token: state.callToken,
                onOpen: () => console.log("[Caller WS] Reconnected successfully!"),
                onMessage: handleWebSocketMessage,
                onClose: () => terminateCall(false),
              });
            }
          }, 1200);
        } else {
          terminateCall(false);
        }
      },
    });
  } catch (err) {
    console.error("Emergency call setup error:", err);
    showToast(
      err.message || "Could not reach the emergency gateway. Please retry.",
      "error",
      9000
    );

    closeSocket();
    if (state.localStream) {
      state.localStream.getTracks().forEach((track) => track.stop());
      state.localStream = null;
    }
    closePeerConnection();
    state.sessionId = null;
    state.callToken = null;
    resetCallUi();
  }
}

function showTextMode() {
  setCallStatus("TEXT MODE - TYPE YOUR EMERGENCY", "status-waiting");
  if (visualizerWrapper) visualizerWrapper.classList.add("hidden");
  if (incallGrid) incallGrid.classList.add("hidden");
  if (textFallback) textFallback.classList.remove("hidden");
  if (textStatement) {
    textStatement.value = "";
    textStatement.focus();
  }
  if (sendTextBtn) {
    sendTextBtn.onclick = submitTextReport;
  }
  visualizerCaption.textContent = "MICROPHONE UNAVAILABLE - TYPE YOUR REPORT";
}

async function submitTextReport() {
  const text = (textStatement && textStatement.value.trim()) || "";
  if (text.length < 10) {
    showToast("Please describe the emergency in at least a short sentence.", "warn");
    return;
  }
  if (sendTextBtn) sendTextBtn.disabled = true;
  try {
    await sendTextStatement(state.sessionId, state.callToken, text);
    setCallStatus("REPORT SENT - WAITING FOR OFFICER", "status-waiting");
    visualizerCaption.textContent = "YOUR TEXT REPORT IS BEING TRIAGED";
    if (textFallback) textFallback.classList.add("hidden");
    showToast("Report sent. An officer is being assigned.", "success");
  } catch (error) {
    showToast(error.message || "Could not send the report.", "error");
  } finally {
    if (sendTextBtn) sendTextBtn.disabled = false;
  }
}

export async function handleWebSocketMessage(msg) {
  console.log("[Caller WS Received]", msg);

  if (msg.event === "OFFICER_ASSIGNED") {
    cancelIvr();
    stopHoldMusic();
    if (!state.textOnly) {
      setMicEnabled(true);
    }
    officerCard.classList.remove("hidden");
    officerNameDisplay.textContent = msg.officer_id || "OFFICER ON DUTY";
    setCallStatus("OFFICER CONNECTING...", "status-connecting");
    visualizerCaption.textContent = "SYNCHRONIZING WEBRTC AUDIO...";
  } else if (msg.event === "WEBRTC_SIGNAL") {
    stopHoldMusic();
    const payload = msg.payload || {};
    if (payload.offer) {
      if (!state.peerConnection) {
        await initPeerConnection();
      }
      await state.peerConnection.setRemoteDescription(
        new RTCSessionDescription(payload.offer)
      );
      await flushPendingCandidates();
      const answer = await state.peerConnection.createAnswer();
      await state.peerConnection.setLocalDescription(answer);
      sendAction({
        action: "WEBRTC_SIGNAL",
        payload: { answer: state.peerConnection.localDescription },
      });
    } else if (payload.candidate) {
      if (!state.peerConnection || !state.peerConnection.remoteDescription) {
        state.pendingCandidates.push(payload.candidate);
      } else {
        try {
          await state.peerConnection.addIceCandidate(
            new RTCIceCandidate(payload.candidate)
          );
        } catch (e) {
          console.error("Error adding ICE candidate:", e);
        }
      }
    }
  } else if (msg.event === "CALL_RELEASED") {
    closePeerConnection();
    officerCard.classList.add("hidden");
    setCallStatus("WAITING FOR DISPATCH...", "status-connecting");
    visualizerCaption.textContent = "MICROPHONE ACTIVE - WAITING FOR OFFICER";
    stopCallTimer();
    if (state.ivrFinished && !state.textOnly) {
      setMicEnabled(true);
    }
  } else if (msg.event === "CALL_ENDED") {
    showToast("Call ended by the dispatch station.", "warn");
    terminateCall(false);
  }
}

export function terminateCall(notifyServer = true) {
  if (state.isTerminating) return;
  state.isTerminating = true;

  if (notifyServer) {
    sendAction({ action: "END_CALL" });
  }

  cancelVadAndHold();
  closeSocket();
  cancelIvr();
  closePeerConnection();

  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => track.stop());
    state.localStream = null;
  }

  stopCallTimer();
  teardownVisualizer();
  resetCallUi();

  if (textFallback) textFallback.classList.add("hidden");
  if (visualizerWrapper) visualizerWrapper.classList.remove("hidden");
  if (incallGrid) incallGrid.classList.remove("hidden");

  state.sessionId = null;
  state.callToken = null;
  state.ivrFinished = false;
  state.statementSent = false;
  state.vadActive = false;
  state.textOnly = false;
  state.isTerminating = false;
}
