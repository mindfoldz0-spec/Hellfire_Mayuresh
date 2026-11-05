/** Officer authentication gate, session checks, and station switching. */

import { state } from "./state.js";
import {
  authError,
  authLoginBtn,
  authOfficerSelect,
  authOverlay,
  authTokenInput,
  officerSelect,
  setGatewayStatus,
  startMenuPopup,
} from "./dom.js";
import { on } from "./events.js";
import { connectWebSocket, disconnectWebSocket } from "./websocket.js";
import { cleanupActiveCall, discardRecording } from "./call.js";
import { renderQueue } from "./queue.js";
import { showToast } from "./utils.js";

export function showAuthOverlay(message = "") {
  disconnectWebSocket();
  authOverlay.classList.remove("hidden");
  setGatewayStatus("AUTH REQUIRED", "pulse-red");
  if (message) {
    authError.textContent = message;
    authError.classList.remove("hidden");
  } else {
    authError.classList.add("hidden");
  }
  // The name typed in the command bar is the one being signed in.
  if (authOfficerSelect && !authOfficerSelect.value.trim()) {
    authOfficerSelect.value = officerSelect.value.trim();
  }
  authTokenInput.focus();
}

export function hideAuthOverlay() {
  authOverlay.classList.add("hidden");
  authError.classList.add("hidden");
}

export function handleUnauthorized(
  message = "Session expired. Please sign in again."
) {
  state.officerId = null;
  discardRecording();
  cleanupActiveCall();
  state.callsMap.clear();
  renderQueue();
  showAuthOverlay(message);
}

export async function authenticate(
  token,
  selectedOfficer,
  { silent = false } = {}
) {
  if (!token) {
    if (!silent) {
      authError.textContent = "Enter the officer password.";
      authError.classList.remove("hidden");
    }
    return false;
  }
  authLoginBtn.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, officer_id: selectedOfficer }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Authentication failed.");
    }

    state.accessToken = token;
    sessionStorage.setItem("cers_access_token", token);
    state.officerId = data.officer_id;
    officerSelect.value = state.officerId;
    authOfficerSelect.value = state.officerId;
    authTokenInput.value = "";
    hideAuthOverlay();
    state.callsMap.clear();
    renderQueue();
    connectWebSocket();
    return true;
  } catch (error) {
    if (!silent) {
      authError.textContent = error.message || "Authentication failed.";
      authError.classList.remove("hidden");
    }
    return false;
  } finally {
    authLoginBtn.disabled = false;
  }
}

export async function checkAuth() {
  try {
    const response = await fetch("/api/auth/me", {
      credentials: "same-origin",
    });
    if (!response.ok) {
      showAuthOverlay();
      return;
    }
    const data = await response.json();
    state.officerId = data.officer_id;
    officerSelect.value = state.officerId;
    authOfficerSelect.value = state.officerId;
    hideAuthOverlay();
    connectWebSocket();
  } catch (error) {
    showAuthOverlay("Could not reach the dispatch gateway.");
  }
}

export async function logout() {
  disconnectWebSocket();
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
  } catch (e) {}
  sessionStorage.removeItem("cers_access_token");
  state.accessToken = "";
  state.officerId = null;
  state.callsMap.clear();
  renderQueue();
  if (startMenuPopup) startMenuPopup.classList.add("hidden");
  showAuthOverlay("Signed out.");
}

export function initAuth() {
  authLoginBtn.addEventListener("click", () => {
    authenticate(authTokenInput.value.trim(), authOfficerSelect.value.trim());
  });

  authTokenInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      authenticate(authTokenInput.value.trim(), authOfficerSelect.value.trim());
    }
  });

  const togglePasswordBtn = document.getElementById("toggle-password-btn");
  if (togglePasswordBtn) {
    togglePasswordBtn.addEventListener("click", () => {
      const isPass = authTokenInput.type === "password";
      authTokenInput.type = isPass ? "text" : "password";
      togglePasswordBtn.textContent = isPass ? "🙈" : "👁️";
    });
  }

  officerSelect.addEventListener("change", async () => {
    const newOfficer = officerSelect.value.trim();
    if (!state.officerId) {
      // Not signed in yet: this only pre-fills the login name.
      authOfficerSelect.value = newOfficer;
      return;
    }
    if (newOfficer === state.officerId) return;
    if (state.currentActiveCallId) {
      officerSelect.value = state.officerId || "";
      showToast("Finish the active call before switching stations.", "warn");
      return;
    }
    const previous = state.officerId;
    officerSelect.value = newOfficer;
    const ok = await authenticate(state.accessToken, newOfficer, {
      silent: true,
    });
    if (!ok) {
      officerSelect.value = previous || "";
      authOfficerSelect.value = newOfficer;
      showAuthOverlay("Re-enter the password to switch stations.");
    }
  });

  on("auth:required", ({ message }) => handleUnauthorized(message));
  on("auth:show", ({ message }) => showAuthOverlay(message));
}
