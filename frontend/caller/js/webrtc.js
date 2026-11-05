/** Caller-side WebRTC peer connection. */

import { state } from "./state.js";
import { remoteAudio, visualizerCaption } from "./dom.js";
import { getRtcConfig } from "./api.js";
import { sendAction } from "./ws.js";
import { startCallTimer, setCallStatus } from "./ui.js";
import {
  connectRemoteToVisualizer,
  disconnectRemoteVisualizer,
} from "./visualizer.js";

export async function initPeerConnection() {
  const config = await getRtcConfig();
  state.peerConnection = new RTCPeerConnection(config);

  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => {
      state.peerConnection.addTrack(track, state.localStream);
    });
  }

  state.peerConnection.onicecandidate = (event) => {
    if (event.candidate) {
      sendAction({
        action: "WEBRTC_SIGNAL",
        payload: { candidate: event.candidate },
      });
    }
  };

  state.peerConnection.ontrack = (event) => {
    console.log("[Caller WebRTC] Remote Officer audio track received!");
    remoteAudio.srcObject = event.streams[0];
    remoteAudio.play().catch((e) => console.warn("Remote audio play error:", e));

    // Feed the officer's audio into the same visualizer as the mic so the
    // oscilloscope reflects the full two-way conversation.
    connectRemoteToVisualizer(event.streams[0]);

    setCallStatus("OFFICER CONNECTED (2-WAY)", "status-active");
    visualizerCaption.textContent = "TWO-WAY AUDIO STREAM ACTIVE";
    startCallTimer();
  };

  state.peerConnection.onconnectionstatechange = () => {
    console.log(
      "[Caller WebRTC] State:",
      state.peerConnection?.connectionState
    );
    if (
      state.peerConnection?.connectionState === "disconnected" ||
      state.peerConnection?.connectionState === "failed"
    ) {
      setCallStatus("CONNECTION LOST", "status-connecting");
    }
  };
}

export async function flushPendingCandidates() {
  if (!state.peerConnection || !state.peerConnection.remoteDescription) return;
  const candidates = state.pendingCandidates;
  state.pendingCandidates = [];
  for (const candidate of candidates) {
    try {
      await state.peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
    } catch (e) {
      console.error("Error adding buffered ICE candidate:", e);
    }
  }
}

export function closePeerConnection() {
  if (state.peerConnection) {
    try {
      state.peerConnection.close();
    } catch (e) {}
    state.peerConnection = null;
  }
  state.pendingCandidates = [];
  disconnectRemoteVisualizer();
  remoteAudio.srcObject = null;
}
