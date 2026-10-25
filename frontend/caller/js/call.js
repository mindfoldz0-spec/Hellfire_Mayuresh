/** Emergency call lifecycle: session creation, signaling, and teardown. */

import { state } from "./state.js";
import {
  dialpadScreen,
  incallNumberTitle,
  incallScreen,
  officerCard,
  officerNameDisplay,
  visualizerCaption,
} from "./dom.js";
import { createCallSession } from "./api.js";
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

export async function startEmergencyCall(number) {
  if (state.sessionId) return;
  try {
    // 1. Request microphone access before creating a session so we do not
    // leave an abandoned call in the queue if permission is denied.
    state.localStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: false,
    });
    setMicEnabled(false);

    // 2. Create a server-side call session. The dialed number is metadata;
    // the session ID is an unguessable UUID.
    const created = await createCallSession(number);
    state.sessionId = created.call_id;
    state.ivrFinished = false;
    state.statementSent = false;
    state.vadActive = false;

    // 3. Switch UI to active call.
    dialpadScreen.classList.remove("active");
    incallScreen.classList.add("active");
    incallNumberTitle.textContent = number;
    setCallStatus("CONNECTING GATEWAY...", "status-connecting");
    visualizerCaption.textContent = "ESTABLISHING SECURE WEBSOCKET...";

    // 4. Setup the audio visualizer.
    setupVisualizer();

    // 5. Connect WebSocket.
    connectCallerSocket(state.sessionId, {
      onOpen: playIVRSequence,
      onMessage: handleWebSocketMessage,
      onClose: () => {
        if (state.isTerminating) return;
        if (state.sessionId) {
          console.warn("[Caller WS] Socket disconnected while session active. Reconnecting...");
          setTimeout(() => {
            if (state.sessionId && !state.isTerminating && (!state.ws || state.ws.readyState !== WebSocket.OPEN)) {
              connectCallerSocket(state.sessionId, {
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
    alert(
      err.message ||
        "Microphone permission is required to place an emergency call."
    );

    // Roll back anything that was partially set up so a retry starts clean
    // and the abandoned server session does not linger in the queue.
    closeSocket();
    if (state.localStream) {
      state.localStream.getTracks().forEach((track) => track.stop());
      state.localStream = null;
    }
    closePeerConnection();
    state.sessionId = null;
    resetCallUi();
  }
}

import { cancelVadAndHold, stopHoldMusic } from "./vad.js";

export async function handleWebSocketMessage(msg) {
  console.log("[Caller WS Received]", msg);

  if (msg.event === "OFFICER_ASSIGNED") {
    cancelIvr();
    stopHoldMusic();
    setMicEnabled(true);
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
    // The officer disconnected or released the call. Keep the caller in the
    // queue and reset WebRTC so another officer can pick it up.
    closePeerConnection();
    officerCard.classList.add("hidden");
    setCallStatus("WAITING FOR DISPATCH...", "status-connecting");
    visualizerCaption.textContent = "MICROPHONE ACTIVE - WAITING FOR OFFICER";
    stopCallTimer();
    if (state.ivrFinished) {
      setMicEnabled(true);
    }
  } else if (msg.event === "CALL_ENDED") {
    alert("Call ended by the dispatch station.");
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

  state.sessionId = null;
  state.ivrFinished = false;
  state.statementSent = false;
  state.vadActive = false;
  state.isTerminating = false;
}
