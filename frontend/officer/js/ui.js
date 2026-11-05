/** Console chrome: clocks, keyboard control, night theme, metrics. */

import { state } from "./state.js";
import {
  menuAboutBtn,
  menuLogoutBtn,
  menuRefreshBtn,
  metricsBody,
  metricsBtn,
  metricsCloseBtn,
  metricsPanel,
  refreshQueueBtn,
  standbyPhoneWrapper,
  startMenuPopup,
  systemClock,
  taskbarClock,
  taskbarStartBtn,
  themeToggleBtn,
} from "./dom.js";
import { logout } from "./auth.js";
import { claimCall, syncQueue } from "./queue.js";
import { setOfficerMicEnabled } from "./call.js";
import { fetchMetrics } from "./api.js";
import { showToast } from "./utils.js";

const THEME_KEY = "cers_night_theme";

function updateClocks() {
  const now = new Date();
  if (systemClock) {
    systemClock.textContent = `${now.toISOString().substring(11, 19)} UTC`;
  }
  if (taskbarClock) {
    taskbarClock.textContent = `${now.toTimeString().substring(0, 8)} LOC`;
  }
}

// ---------------------------------------------------------------------------
// Night theme (front-line desks work overnight; the default theme is bright)
// ---------------------------------------------------------------------------
export function applyTheme(night) {
  document.body.classList.toggle("theme-night", night);
  if (themeToggleBtn) {
    themeToggleBtn.textContent = night ? "[ DAY ]" : "[ NIGHT ]";
  }
  try {
    localStorage.setItem(THEME_KEY, night ? "1" : "0");
  } catch (e) {}
}

function toggleTheme() {
  applyTheme(!document.body.classList.contains("theme-night"));
}

// ---------------------------------------------------------------------------
// Metrics panel
// ---------------------------------------------------------------------------
function metricRow(label, value, tone = "") {
  const row = document.createElement("div");
  row.className = "metric-row";
  const key = document.createElement("span");
  key.className = "metric-key";
  key.textContent = label;
  const val = document.createElement("span");
  val.className = `metric-val ${tone}`;
  val.textContent = String(value);
  row.appendChild(key);
  row.appendChild(val);
  return row;
}

function metricSection(title) {
  const heading = document.createElement("div");
  heading.className = "metric-section";
  heading.textContent = title;
  return heading;
}

export async function refreshMetrics() {
  if (!metricsBody) return;
  const data = await fetchMetrics();
  metricsBody.innerHTML = "";
  if (!data) {
    metricsBody.appendChild(metricRow("Status", "metrics unavailable", "bad"));
    return;
  }

  metricsBody.appendChild(metricSection("LIVE QUEUE"));
  metricsBody.appendChild(metricRow("Active calls", data.active_calls ?? 0));
  metricsBody.appendChild(
    metricRow("Escalated", data.escalated ?? 0, data.escalated ? "bad" : "")
  );
  metricsBody.appendChild(
    metricRow(
      "AI mode",
      data.ai_offline ? "OFFLINE (deterministic)" : "online",
      data.ai_offline ? "warn" : "good"
    )
  );

  metricsBody.appendChild(metricSection("VOLUME"));
  const counters = data.counters || {};
  metricsBody.appendChild(metricRow("Calls created", counters.calls_created ?? 0));
  metricsBody.appendChild(metricRow("Calls claimed", counters.calls_claimed ?? 0));
  metricsBody.appendChild(metricRow("Calls ended", counters.calls_ended ?? 0));
  metricsBody.appendChild(metricRow("Escalations", counters.escalations ?? 0));
  metricsBody.appendChild(metricRow("Triage runs", counters.triage_ok ?? 0));
  metricsBody.appendChild(metricRow("Live windows", counters.triage_windows ?? 0));
  metricsBody.appendChild(
    metricRow(
      "Heuristic fallback",
      `${Math.round((data.heuristic_fallback_rate || 0) * 100)}%`,
      (data.heuristic_fallback_rate || 0) > 0.2 ? "warn" : ""
    )
  );

  metricsBody.appendChild(metricSection("TIME TO CLAIM"));
  Object.entries(data.time_to_claim || {}).forEach(([band, stats]) => {
    const median = stats.median_s === null ? "-" : `${stats.median_s}s`;
    metricsBody.appendChild(
      metricRow(
        `${band} (n=${stats.samples})`,
        `median ${median} · p90 ${stats.p90_s ?? "-"}s`
      )
    );
  });

  const categories = Object.entries(data.categories || {});
  if (categories.length) {
    metricsBody.appendChild(metricSection("INCIDENT MIX"));
    categories.forEach(([name, count]) => {
      metricsBody.appendChild(metricRow(name, count));
    });
  }

  metricsBody.appendChild(metricSection("PROVIDERS"));
  const providers = Object.entries(data.providers || {});
  if (!providers.length) {
    metricsBody.appendChild(metricRow("No outbound calls yet", "-"));
  }
  providers.forEach(([name, stats]) => {
    const tone = stats.breaker_open ? "bad" : stats.failures ? "warn" : "good";
    metricsBody.appendChild(
      metricRow(
        name,
        `${stats.successes}/${stats.calls} ok · ${stats.avg_latency_ms}ms${
          stats.breaker_open ? " · BREAKER OPEN" : ""
        }`,
        tone
      )
    );
  });

  metricsBody.appendChild(metricSection("SESSION"));
  metricsBody.appendChild(metricRow("Uptime", `${data.uptime_seconds ?? 0}s`));
}

function toggleMetrics(force) {
  if (!metricsPanel) return;
  const show =
    force !== undefined ? force : metricsPanel.classList.contains("hidden");
  metricsPanel.classList.toggle("hidden", !show);
  if (show) refreshMetrics();
}

// ---------------------------------------------------------------------------
// Keyboard control (dispatch desks are keyboard-driven)
// ---------------------------------------------------------------------------
function isTypingTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable
  );
}

function topWaitingCallId() {
  const waiting = Array.from(state.callsMap.values())
    .filter((call) => ["WAITING", "RINGING", "IVR"].includes(call.status))
    .sort((a, b) => {
      const prio =
        Number(b.triage_priority_score || 10) - Number(a.triage_priority_score || 10);
      return prio !== 0 ? prio : (a.created_at || 0) - (b.created_at || 0);
    });
  return waiting.length ? waiting[0].call_id : null;
}

function handleShortcut(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (isTypingTarget(event.target)) return;

  const key = event.key.toLowerCase();
  if (key === "a") {
    const callId = topWaitingCallId();
    if (callId) {
      event.preventDefault();
      claimCall(callId);
    } else {
      showToast("No waiting calls to accept.", "info");
    }
  } else if (key === "v") {
    event.preventDefault();
    document.getElementById("header-vault-btn")?.click();
  } else if (key === "t") {
    event.preventDefault();
    toggleTheme();
  } else if (key === "i") {
    event.preventDefault();
    toggleMetrics();
  } else if (key === "m") {
    event.preventDefault();
    if (state.currentActiveCallId) setOfficerMicEnabled(state.isOfficerMuted);
  } else if (event.key === "Escape") {
    toggleMetrics(false);
    if (state.currentActiveCallId) {
      document.getElementById("end-dispatch-btn")?.click();
    }
  }
}

export function initUi() {
  setInterval(updateClocks, 1000);
  updateClocks();

  let night = false;
  try {
    night = localStorage.getItem(THEME_KEY) === "1";
  } catch (e) {}
  if (night) applyTheme(true);

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
        showToast(
          "CERS-OS v2.5 · A accept · V vault · I metrics · T theme · M mute · Esc end",
          "info",
          9000
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

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", toggleTheme);
  }
  if (metricsBtn) {
    metricsBtn.addEventListener("click", () => toggleMetrics());
  }
  if (metricsCloseBtn) {
    metricsCloseBtn.addEventListener("click", () => toggleMetrics(false));
  }

  document.addEventListener("keydown", handleShortcut);
}
