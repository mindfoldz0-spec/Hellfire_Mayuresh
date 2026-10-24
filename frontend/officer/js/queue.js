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

export function renderQueue() {
  const activeCalls = Array.from(state.callsMap.values()).filter(
    (call) => call.status !== "ENDED"
  );

  // Dynamic Triage Priority Sorting: highest priority score first, then created_at
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
        idleSub.textContent = `[${topCall.incident_category || "EMERGENCY"}] Score: ${topCall.triage_priority_score || 10}/100. Click [ ACCEPT CALL ] to dispatch.`;
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

  queueList.innerHTML = "";
  if (activeCalls.length === 0) {
    queueEmptyState.style.display = "flex";
    return;
  }
  queueEmptyState.style.display = "none";

  activeCalls.forEach((call) => {
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
    const busy = Boolean(state.currentActiveCallId);

    const prioScore = Number(call.triage_priority_score || 10);
    const urgency = call.urgency_level || "ROUTINE";

    const card = document.createElement("div");
    let cardClasses = `queue-card ${isReady ? "waiting" : "engaged"}`;
    if (prioScore >= 85 || urgency === "VERY_VERY_URGENT") {
      cardClasses += " urgent-critical";
    } else if (prioScore >= 65 || urgency === "URGENT") {
      cardClasses += " urgent-high";
    }
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

    // 1. Top Bar
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

    // 2. Fused Triage Header & Priority Badge
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

    // 3. Incident Summary Box
    const summaryBox = document.createElement("div");
    summaryBox.className = "triage-summary-box";
    summaryBox.textContent = `📋 ${call.incident_summary || "Caller statement triage in progress..."}`;

    // 4. Acoustic Speech Emotion Recognition Gauges
    const emotions = call.emotions || {};
    const fearVal = Math.round((Number(emotions.fear) || 0) * 100);
    const distressVal = Math.round((Number(emotions.distress) || 0) * 100);
    const angerVal = Math.round((Number(emotions.anger) || 0) * 100);
    const nervVal = Math.round((Number(emotions.nervousness) || 0) * 100);

    const emotionContainer = document.createElement("div");
    emotionContainer.className = "emotion-meters-container";
    emotionContainer.innerHTML = `
      <div class="emotion-meters-title">
        <span>Acoustic Vocal Emotion Spectrum</span>
        <span>Dominant: ${(emotions.dominant || "Neutral").toUpperCase()}</span>
      </div>
      <div class="emotion-meter-row">
        <span class="emotion-label">🔴 Fear:</span>
        <div class="emotion-bar-track"><div class="emotion-bar-fill bar-fear" style="width: ${fearVal}%"></div></div>
        <span class="emotion-val">${fearVal}%</span>
      </div>
      <div class="emotion-meter-row">
        <span class="emotion-label">🟣 Distress:</span>
        <div class="emotion-bar-track"><div class="emotion-bar-fill bar-distress" style="width: ${distressVal}%"></div></div>
        <span class="emotion-val">${distressVal}%</span>
      </div>
      <div class="emotion-meter-row">
        <span class="emotion-label">🟠 Anger:</span>
        <div class="emotion-bar-track"><div class="emotion-bar-fill bar-anger" style="width: ${angerVal}%"></div></div>
        <span class="emotion-val">${angerVal}%</span>
      </div>
      <div class="emotion-meter-row">
        <span class="emotion-label">🟡 Jitter:</span>
        <div class="emotion-bar-track"><div class="emotion-bar-fill bar-nervousness" style="width: ${nervVal}%"></div></div>
        <span class="emotion-val">${nervVal}%</span>
      </div>
    `;

    // 5. Problem Statement Compliance: Responsible AI Guardrail Advisory
    const guardrail = document.createElement("div");
    guardrail.className = "responsible-ai-guardrail";
    guardrail.textContent = "⚖️ Responsible AI Advisory: Emotion predictions are indicative triage signals, not evidence of guilt or intent.";

    // 6. Meta Bar
    const meta = document.createElement("div");
    meta.className = "card-meta";
    const gatewaySpan = document.createElement("span");
    gatewaySpan.textContent = `GATEWAY: ${call.dialed_number || "2007"}`;
    const durationSpan = document.createElement("span");
    durationSpan.dataset.durationCallId = call.call_id;
    durationSpan.textContent = `DURATION: ${Math.max(
      0,
      Math.floor(call.duration || 0)
    )}s`;
    meta.appendChild(gatewaySpan);
    meta.appendChild(durationSpan);

    // 7. Claim Button
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
    card.appendChild(emotionContainer);
    card.appendChild(guardrail);
    card.appendChild(meta);
    card.appendChild(claimBtn);
    queueList.appendChild(card);
  });

  startDurationTicker();
}

function startDurationTicker() {
  if (state.durationUiInterval) return;
  state.durationUiInterval = setInterval(() => {
    const now = Date.now() / 1000;
    document.querySelectorAll("[data-duration-call-id]").forEach((element) => {
      const call = state.callsMap.get(element.dataset.durationCallId);
      if (!call) return;
      const started = call.engaged_at || call.created_at || now;
      const seconds = Math.max(0, Math.floor(now - started));
      element.textContent = `DURATION: ${seconds}s`;
    });
  }, 1000);
}

export function claimCall(callId) {
  if (state.currentActiveCallId) {
    alert("Finish the current emergency call before accepting another.");
    return;
  }
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    emit("ws:send", { action: "CLAIM_CALL", call_id: callId });
  } else {
    alert("Dispatch gateway is not connected. Reconnecting...");
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
