/** Caller WebSocket connection and serialized message handling. */

import { state } from "./state.js";

let callerHeartbeatInterval = null;

function startCallerHeartbeat() {
  stopCallerHeartbeat();
  callerHeartbeatInterval = setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ action: "PING" }));
    }
  }, 5000);
}

function stopCallerHeartbeat() {
  if (callerHeartbeatInterval) {
    clearInterval(callerHeartbeatInterval);
    callerHeartbeatInterval = null;
  }
}

export function connectCallerSocket(sessionId, { onOpen, onMessage, onClose } = {}) {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/caller/${encodeURIComponent(sessionId)}`;
  const socket = new WebSocket(wsUrl);
  state.ws = socket;

  socket.onopen = () => {
    console.log("[Caller WS] Connected to backend gateway");
    startCallerHeartbeat();
    onOpen?.();
  };

  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (e) {
      console.error("[Caller WS] Parse error:", e);
      return;
    }
    if (message.event === "PONG") {
      return; // Keepalive ack
    }
    // Serialize async handlers so SDP/ICE messages cannot interleave.
    state.messageChain = state.messageChain
      .then(() => onMessage?.(message))
      .catch((e) => console.error("[Caller WS] Handler error:", e));
  };

  socket.onclose = () => {
    stopCallerHeartbeat();
    if (state.ws === socket) {
      state.ws = null;
    }
    console.log("[Caller WS] Disconnected");
    onClose?.();
  };

  socket.onerror = (err) => {
    console.error("[Caller WS] Error:", err);
  };

  return socket;
}

export function sendAction(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

export function closeSocket() {
  stopCallerHeartbeat();
  const socket = state.ws;
  state.ws = null;
  if (socket) {
    try {
      socket.close();
    } catch (e) {}
  }
}

