/** Emergency call queue rendering, duration ticker, and claim actions. */

import { state } from "./state.js";
import {
  idleSub,
  idleTitle,
  queueCountBadge,
  queueEmptyState,
  queueList,
  standbyPhoneWrapper,
} from "./dom.js";
import { emit } from "./events.js";
import { showToast } from "./utils.js";

/** Last rendered queue fingerprint; guards against needless full re-renders. */
let lastQueueSignature = null;
let hasRenderedQueue = false;

function createPhoneBadge(isReady) {
  const badge = document.createElement("div");
  badge.className = `card-phone-badge ${isReady ? "ringing" : ""}`;
  // Constant markup: no server-controlled values.
  badge.innerHTML = `
    <svg viewBox="0 0 100 100" width="28" height="28">
      <circle cx="50" cy="50" r="48" fill="${isReady ? "#FF0000" : "var(--primary)"}" stroke="#000000" stroke-width="3" />
      <g class="handset-group">
        <path fill="${isReady ? "#FFFFFF" : "#000000"}" d="M 31.3 15.8 C 34.6 14.5 38.3 16.1 39.8 19.5 L 46.2 33.7 C 47.5 36.8 46.6 40.5 43.8 42.6 L 37.5 47.5 C 41.2 55.8 47.9 62.4 56.1 66.2 L 61.0 59.8 C 63.2 57.1 66.8 56.1 69.9 57.5 L 84.1 63.8 C 87.6 65.3 89.2 69.1 87.9 72.4 L 84.5 81.4 C 83.0 85.3 78.8 87.9 74.1 87.2 C 42.6 83.0 20.7 61.0 16.4 29.6 C 15.8 24.9 18.3 20.7 22.3 19.2 Z" />
      </g>
    </svg>`;
  return badge;
}

export function callLabel(call) {
  return call.dialed_number || String(call.call_id || "").slice(0, 8);
}

export function parseEmotionPct(value) {
  const n = Number(value) || 0;
  return n <= 1 ? Math.min(100, Math.round(n * 100)) : Math.min(100, Math.round(n));
}

/**
 * Everything that affects the rendered DOM. ``duration`` is deliberately
 * excluded — it changes every second and is patched in place by the ticker.
 */
function queueSignature(calls) {
  return calls
    .map((call) =>
      [
        call.call_id,
        call.status,
        call.officer_id || "",
        call.dialed_number || "",
        Number(call.triage_priority_score) || 0,
        call.urgency_level || "",
        call.incident_category || "",
        call.incident_summary || "",
        JSON.stringify(call.scam_indicators || null),
        JSON.stringify(call.officer_sop || null),
        JSON.stringify(call.emotions || null),
        call.escalated_at ? "esc" : "",
      ].join("\u0001")
    )
    .join("\u0002");
}

/** Build the emotion gauges with DOM calls — never interpolate LLM text. */
function buildEmotionMeters(emotions) {
  const container = document.createElement("div");
  container.className = "emotion-meters-container";

  const header = document.createElement("div");
  header.className = "emotion-meters-title";
  const left = document.createElement("span");
  left.textContent = "Acoustic Vocal Emotion Spectrum";
  const right = document.createElement("span");
  const dominant = String(emotions.dominant || "Neutral").toUpperCase();
  const estimated = emotions.source === "acoustic";
  right.textContent = estimated
    ? `Dominant: ${dominant} (ESTIMATED)`
    : `Dominant: ${dominant}`;
  if (estimated) {
    right.title =
      "No SER model was reachable; these values are a prosody-based estimate, not a measured emotion.";
  }
  header.appendChild(left);
  header.appendChild(right);
  container.appendChild(header);

  const rows = [
    ["🔴 Fear:", "bar-fear", parseEmotionPct(emotions.fear)],
    ["🟣 Distress:", "bar-distress", parseEmotionPct(emotions.distress)],
    ["🟠 Anger:", "bar-anger", parseEmotionPct(emotions.anger)],
    ["🟡 Jitter:", "bar-nervousness", parseEmotionPct(emotions.nervousness)],
  ];
  rows.forEach(([label, barClass, pct]) => {
    const row = document.createElement("div");
    row.className = "emotion-meter-row";

    const labelEl = document.createElement("span");
    labelEl.className = "emotion-label";
    labelEl.textContent = label;

    const track = document.createElement("div");
    track.className = "emotion-bar-track";
    const fill = document.createElement("div");
    fill.className = `emotion-bar-fill ${barClass}`;
    fill.style.width = `${pct}%`;
    track.appendChild(fill);

    const value = document.createElement("span");
    value.className = "emotion-val";
    value.textContent = `${pct}%`;

    row.appendChild(labelEl);
    row.appendChild(track);
    row.appendChild(value);
    container.appendChild(row);
  });

  return container;
}

function buildSopBox(sop) {
  const lines = Array.isArray(sop) ? sop.filter(Boolean) : sop ? [String(sop)] : [];
  if (lines.length === 0) return null;

  const box = document.createElement("div");
  box.className = "officer-sop-box";
  const title = document.createElement("div");
  title.className = "officer-sop-title";
  title.textContent = "🎯 TACTICAL OFFICER SOP (FIRST 60s)";
  const body = document.createElement("div");
  body.className = "officer-sop-body";
  // textContent + pre-line CSS: caller-influenced LLM text never becomes HTML.
  body.textContent = lines.map((line) => `• ${line}`).join("\n");
  box.appendChild(title);
  box.appendChild(body);
  return box;
}

function buildCard(call, busy) {
  const isClaimedByMe =
    call.officer_id === state.officerId && call.status === "ENGAGED";
  const isClaimedByOther =
    Boolean(call.officer_id) &&
    call.officer_id !== state.officerId &&
    call.status === "ENGAGED";
  const isReady =
    call.status === "WAITING" ||
    call.status === "RINGING" ||
    call.status === "IVR";

  const prioScore = Number(call.triage_priority_score || 10);
  const urgency = String(call.urgency_level || "ROUTINE");

  const card = document.createElement("div");
  let cardClasses = `queue-card ${isReady ? "waiting" : "engaged"}`;
  if (prioScore >= 85 || urgency === "VERY_VERY_URGENT") {
    cardClasses += " urgent-critical";
  } else if (prioScore >= 65 || urgency === "URGENT") {
    cardClasses += " urgent-high";
  }
  if (call.escalated_at) cardClasses += " escalated";
  card.className = cardClasses;
  card.dataset.callId = call.call_id;

  let statusBadgeClass = "badge-waiting";
  let statusText = "READY FOR DISPATCH";
  if (call.status === "IVR") {
    statusBadgeClass = "badge-ivr";
    statusText = "BOT TRIAGING STATEMENT";
  } else if (isClaimedByMe) {
    statusBadgeClass = "badge-waiting";
    statusText = "ENGAGED (YOU)";
  } else if (isClaimedByOther) {
    statusBadgeClass = "badge-engaged";
    statusText = `ENGAGED BY ${call.officer_id}`;
  }
  if (call.escalated_at && !isClaimedByMe && !isClaimedByOther) {
    statusBadgeClass = "badge-escalated";
    statusText = "ESCALATED - UNCLAIMED";
  }

  // 1. Top bar
  const top = document.createElement("div");
  top.className = "card-top";
  const idTag = document.createElement("div");
  idTag.className = "call-id-tag";
  idTag.appendChild(createPhoneBadge(isReady));
  const idSpan = document.createElement("span");
  idSpan.textContent = `#${callLabel(call)}`;
  idTag.appendChild(idSpan);
  const badge = document.createElement("span");
  badge.className = `status-badge ${statusBadgeClass}`;
  badge.textContent = statusText;
  top.appendChild(idTag);
  top.appendChild(badge);

  // 2. Fused triage header & priority badge
  const triageHeader = document.createElement("div");
  triageHeader.className = "card-triage-header";

  let scoreClass = "score-routine";
  if (prioScore >= 85) scoreClass = "score-critical";
  else if (prioScore >= 65) scoreClass = "score-high";
  else if (prioScore >= 40) scoreClass = "score-elevated";

  const scoreBadge = document.createElement("span");
  scoreBadge.className = `triage-score-badge ${scoreClass}`;
  scoreBadge.textContent = `⚡ PRIORITY: ${prioScore}/100 [${urgency.replace(/_/g, " ")}]`;
  const categoryTag = document.createElement("span");
  categoryTag.className = "triage-category-tag";
  categoryTag.textContent = call.incident_category || "GENERAL REPORT";
  triageHeader.appendChild(scoreBadge);
  triageHeader.appendChild(categoryTag);

  // 3. Incident summary
  const summaryBox = document.createElement("div");
  summaryBox.className = "triage-summary-box";
  let summaryText = "Caller statement triage in progress...";
  if (typeof call.incident_summary === "string" && call.incident_summary.trim()) {
    summaryText = call.incident_summary.trim();
  } else if (Array.isArray(call.incident_summary) && call.incident_summary.length > 0) {
    summaryText = call.incident_summary.join(" ");
  }
  summaryBox.textContent = `📋 ${summaryText}`;

  // 3b. Scam / threat red flags
  const indicators = Array.isArray(call.scam_indicators)
    ? call.scam_indicators
    : typeof call.scam_indicators === "string" && call.scam_indicators.trim()
    ? [call.scam_indicators.trim()]
    : [];
  let scamFlagsContainer = null;
  if (indicators.length > 0) {
    scamFlagsContainer = document.createElement("div");
    scamFlagsContainer.className = "triage-flags-container";
    indicators.forEach((indicator) => {
      if (!indicator) return;
      const flag = document.createElement("span");
      flag.className = "scam-pill";
      flag.textContent = `🚩 ${indicator}`;
      scamFlagsContainer.appendChild(flag);
    });
  }

  // 4. Emotion meters and tactical SOP
  const emotionContainer = buildEmotionMeters(call.emotions || {});
  const sopBox = buildSopBox(call.officer_sop);

  // 5. Responsible AI guardrail
  const guardrail = document.createElement("div");
  guardrail.className = "responsible-ai-guardrail";
  guardrail.textContent =
    "⚖️ Responsible AI Advisory: Emotion predictions are indicative triage signals, not evidence of guilt or intent.";

  // 6. Meta bar
  const meta = document.createElement("div");
  meta.className = "card-meta";
  const gatewaySpan = document.createElement("span");
  gatewaySpan.textContent = `GATEWAY: ${call.dialed_number || "2007"}`;
  const durationSpan = document.createElement("span");
  durationSpan.dataset.durationCallId = call.call_id;
  const engaged = call.status === "ENGAGED";
  durationSpan.textContent = `${engaged ? "DURATION" : "WAITING"}: ${Math.max(
    0,
    Math.floor(call.duration || 0)
  )}s`;
  meta.appendChild(gatewaySpan);
  meta.appendChild(durationSpan);

  // 7. Claim button
  const claimBtn = document.createElement("button");
  claimBtn.className = "claim-btn";
  const isBotTriaging = call.status === "IVR" || call.status === "RINGING";
  claimBtn.disabled = isClaimedByOther || isClaimedByMe || busy || isBotTriaging;
  if (isBotTriaging) {
    claimBtn.textContent = "[ ⏳ BOT RECORDING STATEMENT... ]";
  } else if (isClaimedByOther) {
    claimBtn.textContent = `[ 🔒 Locked by ${call.officer_id} ]`;
  } else if (isClaimedByMe) {
    claimBtn.textContent = "[ ✓ Engaged on Station ]";
  } else if (busy) {
    claimBtn.textContent = "[ FINISH CURRENT CALL FIRST ]";
  } else if (prioScore >= 85) {
    claimBtn.textContent = "[ 🚨 DISPATCH URGENT SQUAD ]";
  } else {
    claimBtn.textContent = "[ ACCEPT CALL ]";
  }
  if (!claimBtn.disabled) {
    claimBtn.addEventListener("click", () => claimCall(call.call_id));
  }

  card.appendChild(top);
  card.appendChild(triageHeader);
  card.appendChild(summaryBox);
  if (scamFlagsContainer) card.appendChild(scamFlagsContainer);
  if (sopBox) card.appendChild(sopBox);
  card.appendChild(emotionContainer);
  card.appendChild(guardrail);
  card.appendChild(meta);
  card.appendChild(claimBtn);
  return card;
}

export function renderQueue() {
  const activeCalls = Array.from(state.callsMap.values()).filter(
    (call) => call.status !== "ENDED"
  );

  // Dynamic triage priority sorting: highest priority score first, then created_at
  activeCalls.sort((a, b) => {
    const prioA = Number(a.triage_priority_score || 10);
    const prioB = Number(b.triage_priority_score || 10);
    if (prioB !== prioA) return prioB - prioA;
    return (a.created_at || 0) - (b.created_at || 0);
  });

  const waitingCalls = activeCalls.filter(
    (call) =>
      call.status === "WAITING" ||
      call.status === "RINGING" ||
      call.status === "IVR"
  );
  const hasIncoming = waitingCalls.length > 0;

  queueCountBadge.textContent = String(activeCalls.length);

  if (standbyPhoneWrapper) {
    if (hasIncoming) {
      standbyPhoneWrapper.classList.add("ringing");
      const topCall = waitingCalls[0];
      const isCritical = (topCall.triage_priority_score || 0) >= 85;

      if (idleTitle) {
        if (isCritical) {
          idleTitle.textContent = `🚨 CRITICAL EMERGENCY CALL (#${callLabel(topCall)})`;
          idleTitle.style.color = "#FF0033";
        } else {
          idleTitle.textContent = `🚨 INCOMING CALL RINGING (${waitingCalls.length})`;
          idleTitle.style.color = "var(--red-alert)";
        }
      }
      if (idleSub) {
        idleSub.textContent = `[${topCall.incident_category || "EMERGENCY"}] Score: ${
          topCall.triage_priority_score || 10
        }/100. Click [ ACCEPT CALL ] to dispatch.`;
      }
    } else {
      standbyPhoneWrapper.classList.remove("ringing");
      if (idleTitle) {
        idleTitle.textContent = "STATION STANDBY";
        idleTitle.style.color = "";
      }
      if (idleSub) {
        idleSub.textContent =
          'Select and "Accept" an incoming emergency call from the queue to start two-way voice dispatch.';
      }
    }
  }

  // Rebuilding this list on every STATUS_CHANGED destroyed scroll position,
  // selection and focus. Only touch the DOM when something visible changed.
  const signature = queueSignature(activeCalls);
  if (hasRenderedQueue && signature === lastQueueSignature) {
    startDurationTicker();
    return;
  }
  lastQueueSignature = signature;
  hasRenderedQueue = true;

  const previousScroll = queueList.scrollTop;
  queueList.innerHTML = "";
  if (activeCalls.length === 0) {
    queueEmptyState.style.display = "flex";
    queueList.scrollTop = previousScroll;
    return;
  }
  queueEmptyState.style.display = "none";

  const busy = Boolean(state.currentActiveCallId);
  activeCalls.forEach((call) => {
    try {
      queueList.appendChild(buildCard(call, busy));
    } catch (cardErr) {
      console.error(
        `[Queue] Failed rendering call card ${call && call.call_id}:`,
        cardErr
      );
    }
  });
  queueList.scrollTop = previousScroll;

  startDurationTicker();
}

/**
 * Duration is derived from *server* timestamps plus the measured server/client
 * clock offset, so a skewed workstation clock no longer shows wrong times.
 */
function startDurationTicker() {
  if (state.durationUiInterval) return;
  state.durationUiInterval = setInterval(() => {
    const now = Date.now() / 1000 + (state.serverClockOffset || 0);
    document.querySelectorAll("[data-duration-call-id]").forEach((element) => {
      const call = state.callsMap.get(element.dataset.durationCallId);
      if (!call) return;
      const started = call.engaged_at || call.created_at || now;
      const seconds = Math.max(0, Math.floor(now - started));
      const label = call.status === "ENGAGED" ? "DURATION" : "WAITING";
      element.textContent = `${label}: ${seconds}s`;
    });
  }, 1000);
}

export function claimCall(callId) {
  if (state.currentActiveCallId) {
    showToast("Finish the current emergency call before accepting another.", "warn");
    return;
  }
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    emit("ws:send", { action: "CLAIM_CALL", call_id: callId });
  } else {
    showToast("Dispatch gateway is not connected. Reconnecting...", "error");
    emit("ws:reconnect");
  }
}

export function syncQueue() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    emit("ws:send", { action: "SYNC_QUEUE" });
  } else {
    emit("ws:reconnect");
  }
}

