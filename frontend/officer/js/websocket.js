/** Officer WebSocket connection, reconnect policy, and server event routing. */

import { state } from "./state.js";
import { setGatewayStatus } from "./dom.js";
import { on } from "./events.js";
import { playIncomingPing } from "./utils.js";
import { renderQueue } from "./queue.js";
import {
  finishActiveCallSession,
  flushPendingCandidates,
  startActiveCallSession,
} from "./call.js";

export function sendAction(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

let heartbeatInterval = null;

function startHeartbeat() {
  stopHeartbeat();
  heartbeatInterval = setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ action: "PING" }));
    }
  }, 15000); // 15s keepalive ping prevents Render 55s idle timeout
}

function stopHeartbeat() {
  if (heartbeatInterval) {
    clearInterval(heartbeatInterval);
    heartbeatInterval = null;
  }
}

export function disconnectWebSocket() {
  state.shouldReconnect = false;
  stopHeartbeat();
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
  if (state.ws) {
    const socket = state.ws;
    state.ws = null;
    try {
      socket.close();
    } catch (e) {}
  }
}

export function connectWebSocket() {
  if (!state.officerId) return;
  state.shouldReconnect = true;
  const generation = ++state.socketGeneration;

  if (state.ws) {
    const oldSocket = state.ws;
    state.ws = null;
    try {
      oldSocket.close();
    } catch (e) {}
  }

  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/officer/${encodeURIComponent(
    state.officerId
  )}`;
  const socket = new WebSocket(wsUrl);
  state.ws = socket;

  socket.onopen = () => {
    if (socket !== state.ws) return;
    setGatewayStatus(`ONLINE (${state.officerId})`, "pulse-green");
    console.log(`[Officer WS] Connected as ${state.officerId}`);
    startHeartbeat();
  };

  socket.onmessage = (event) => {
    if (socket !== state.ws) return;
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (error) {
      console.error("[Officer WS] Error parsing message:", error);
      return;
    }
    if (message.event === "PONG") {
      return; // Keepalive acknowledged
    }
    // Serialize handlers so SDP/ICE messages cannot interleave.
    state.messageChain = state.messageChain
      .then(() => handleServerEvent(message))
      .catch((error) => console.error("[Officer WS] Handler error:", error));
  };

  socket.onclose = () => {
    if (socket !== state.ws) return;
    stopHeartbeat();
    state.ws = null;
    setGatewayStatus("DISCONNECTED - RETRYING...", "pulse-red");
    if (state.shouldReconnect && generation === state.socketGeneration) {
      if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
      state.reconnectTimer = setTimeout(() => {
        state.reconnectTimer = null;
        if (state.shouldReconnect && generation === state.socketGeneration) {
          connectWebSocket();
        }
      }, 1200);
    }
  };

  socket.onerror = (error) => {
    console.error("[Officer WS] Error:", error);
  };
}

export function reconnectWebSocket() {
  if (state.officerId) {
    connectWebSocket();
  }
}

async function handleServerEvent(msg) {
  if (msg.event === "PONG") return;
  console.log("[Officer Received Event]", msg);

  if (msg.event === "QUEUE_SNAPSHOT") {
    state.callsMap.clear();
    (msg.calls || []).forEach((call) => state.callsMap.set(call.call_id, call));
    renderQueue();
  } else if (msg.event === "NEW_CALL") {
    if (msg.call) {
      state.callsMap.set(msg.call.call_id, msg.call);
      renderQueue();
      playIncomingPing();
    }
  } else if (msg.event === "CALL_TRIAGED") {
    if (msg.call) {
      state.callsMap.set(msg.call.call_id, msg.call);
      renderQueue();
      if ((msg.call.triage_priority_score || 0) >= 85) {
        playIncomingPing();
      }
    }
  } else if (msg.event === "STATUS_CHANGED") {
    if (state.callsMap.has(msg.call_id)) {
      state.callsMap.get(msg.call_id).status = msg.status;
      renderQueue();
    }
  } else if (msg.event === "CALL_CLAIMED") {
    if (state.callsMap.has(msg.call_id)) {
      const call = state.callsMap.get(msg.call_id);
      call.status = "ENGAGED";
      call.officer_id = msg.officer_id;
      renderQueue();
    }
  } else if (msg.event === "CALL_RELEASED") {
    if (state.callsMap.has(msg.call_id)) {
      const call = state.callsMap.get(msg.call_id);
      call.status = "WAITING";
      call.officer_id = null;
      call.engaged_at = null;
      renderQueue();
    }
    if (state.currentActiveCallId === msg.call_id) {
      await finishActiveCallSession(false);
    }
  } else if (msg.event === "CLAIM_RESULT") {
    if (msg.success) {
      if (state.currentActiveCallId) {
        // The server enforces one call per officer, but keep the UI safe
        // if a stale claim response arrives.
        sendAction({ action: "RELEASE_CALL", call_id: msg.call_id });
        return;
      }
      state.currentActiveCallId = msg.call_id;
      renderQueue();
      await startActiveCallSession(msg.call_id);
    } else {
      alert(`Cannot claim call: ${msg.reason}`);
    }
  } else if (msg.event === "WEBRTC_SIGNAL") {
    const payload = msg.payload || {};
    if (payload.answer) {
      if (!state.peerConnection) return;
      await state.peerConnection.setRemoteDescription(
        new RTCSessionDescription(payload.answer)
      );
      await flushPendingCandidates();
    } else if (payload.candidate) {
      if (!state.peerConnection || !state.peerConnection.remoteDescription) {
        state.pendingCandidates.push(payload.candidate);
      } else {
        try {
          await state.peerConnection.addIceCandidate(
            new RTCIceCandidate(payload.candidate)
          );
        } catch (error) {
          console.error("Error adding ICE candidate:", error);
        }
      }
    }
  } else if (msg.event === "CALL_ENDED") {
    if (state.currentActiveCallId === msg.call_id) {
      await finishActiveCallSession(false);
    }
    state.callsMap.delete(msg.call_id);
    renderQueue();
  } else if (msg.event === "ERROR") {
    console.error("[Officer WS] Server error:", msg.message);
    alert(msg.message || "The dispatch gateway rejected that action.");
  }
}

// Modules request socket actions through the event bus to avoid circular
// imports between queue/call and this connection module.
on("ws:send", (payload) => sendAction(payload));
on("ws:reconnect", () => reconnectWebSocket());
