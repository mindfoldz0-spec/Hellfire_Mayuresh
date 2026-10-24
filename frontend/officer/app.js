// Cyber Emergency Response System - Officer Dispatch Console
document.addEventListener("DOMContentLoaded", () => {
  // DOM References
  const officerSelect = document.getElementById("officer-select");
  const gatewayStatus = document.getElementById("gateway-status");
  const systemClock = document.getElementById("system-clock");
  const queueCountBadge = document.getElementById("queue-count-badge");
  const refreshQueueBtn = document.getElementById("refresh-queue-btn");
  const queueEmptyState = document.getElementById("queue-empty-state");
  const queueList = document.getElementById("queue-list");

  const stationIdle = document.getElementById("station-idle");
  const standbyPhoneWrapper = document.getElementById("standby-phone-wrapper");
  const idleTitle = document.getElementById("idle-title");
  const idleSub = document.getElementById("idle-sub");
  const stationActive = document.getElementById("station-active");
  const activeCallIdEl = document.getElementById("active-call-id");
  const activeCallTimerEl = document.getElementById("active-call-timer");
  const incidentNotes = document.getElementById("incident-notes");
  const officerMuteBtn = document.getElementById("officer-mute-btn");
  const officerMuteText = document.getElementById("officer-mute-text");
  const endDispatchBtn = document.getElementById("end-dispatch-btn");
  const tagBtns = document.querySelectorAll(".tag-btn");

  const callerScope = document.getElementById("caller-scope");
  const officerScope = document.getElementById("officer-scope");
  const remoteCallerAudio = document.getElementById("remote-caller-audio");

  const taskbarClock = document.getElementById("taskbar-clock");
  const taskbarStartBtn = document.getElementById("taskbar-start-btn");
  const startMenuPopup = document.getElementById("start-menu-popup");
  const menuIntelBtn = document.getElementById("menu-intel-btn");
  const menuRefreshBtn = document.getElementById("menu-refresh-btn");
  const menuAboutBtn = document.getElementById("menu-about-btn");

  const openIntelBtn = document.getElementById("open-intel-btn");
  const headerVaultBtn = document.getElementById("header-vault-btn");
  const closeIntelModalBtn = document.getElementById("close-intel-modal-btn");
  const minIntelModalBtn = document.getElementById("min-intel-modal-btn");
  const maxIntelModalBtn = document.getElementById("max-intel-modal-btn");
  const retroWindowModal = document.getElementById("retro-window-modal");
  const intelModalBackdrop = document.getElementById("intel-modal-backdrop");
  const vaultBadgeCount = document.getElementById("vault-badge-count");
  const sidebarCount = document.getElementById("sidebar-count");
  const modalRecordingsList = document.getElementById("modal-recordings-list");
  const intelSearchInput = document.getElementById("intel-search-input");

  const intelEmptyPrompt = document.getElementById("intel-empty-prompt");
  const intelDetailView = document.getElementById("intel-detail-view");
  const intelCallId = document.getElementById("intel-call-id");
  const intelThreatBadge = document.getElementById("intel-threat-badge");
  const intelTimestamp = document.getElementById("intel-timestamp");
  const intelDuration = document.getElementById("intel-duration");
  const intelOfficer = document.getElementById("intel-officer");
  const intelAudioPlayer = document.getElementById("intel-audio-player");
  const intelAudioFilename = document.getElementById("intel-audio-filename");
  const intelLocationVal = document.getElementById("intel-location-val");
  const intelCategoryVal = document.getElementById("intel-category-val");
  const intelRansomVal = document.getElementById("intel-ransom-val");
  const intelDeadlineVal = document.getElementById("intel-deadline-val");
  const intelTranscriptBox = document.getElementById("intel-transcript-box");
  const intelNotesTextarea = document.getElementById("intel-notes-textarea");
  const saveIntelNotesBtn = document.getElementById("save-intel-notes-btn");
  const refreshVaultBtn = document.getElementById("refresh-vault-btn");

  // State
  const urlParams = new URLSearchParams(window.location.search);
  let officerId = urlParams.get("id") || officerSelect.value || "OFFICER_01";
  officerSelect.value = officerId;

  let ws = null;
  let callsMap = new Map();
  let currentActiveCallId = null;
  let activeCallDuration = 0;
  let activeTimerInterval = null;

  // WebRTC & Audio
  let peerConnection = null;
  let localMicStream = null;
  let remoteStream = null;
  let audioContext = null;
  let mediaRecorder = null;
  let recordedChunks = [];
  let isOfficerMuted = false;

  let callerScopeAnimationId = null;
  let officerScopeAnimationId = null;

  const rtcConfig = {
    iceServers: [
      { urls: "stun:stun.l.google.com:19302" },
      { urls: "stun:stun1.l.google.com:19302" }
    ]
  };

  // Clock Update
  function updateClock() {
    const now = new Date();
    const timeStr = now.toTimeString().split(" ")[0] + " LOC";
    systemClock.textContent = timeStr;
    if (taskbarClock) taskbarClock.textContent = timeStr;
  }
  setInterval(updateClock, 1000);
  updateClock();

  // Officer Dropdown Change
  officerSelect.addEventListener("change", () => {
    officerId = officerSelect.value;
    const newUrl = `${window.location.pathname}?id=${officerId}`;
    window.history.replaceState(null, "", newUrl);
    reconnectWebSocket();
  });

  // WebSocket Connection
  function connectWebSocket() {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/officer/${officerId}`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      gatewayStatus.textContent = `ONLINE (${officerId})`;
      gatewayStatus.parentElement.querySelector(".live-dot").className = "live-dot pulse-green";
      console.log(`[Officer WS] Connected as ${officerId}`);
    };

    ws.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleServerEvent(msg);
      } catch (e) {
        console.error("[Officer WS] Error parsing message:", e);
      }
    };

    ws.onclose = () => {
      gatewayStatus.textContent = "DISCONNECTED - RETRYING...";
      gatewayStatus.parentElement.querySelector(".live-dot").className = "live-dot pulse-red";
      setTimeout(connectWebSocket, 2500);
    };

    ws.onerror = (err) => {
      console.error("[Officer WS] Error:", err);
    };
  }

  function reconnectWebSocket() {
    if (ws) {
      ws.close();
    }
  }

  connectWebSocket();

  // Server Event Handler
  async function handleServerEvent(msg) {
    console.log("[Officer Received Event]", msg);

    if (msg.event === "QUEUE_SNAPSHOT") {
      callsMap.clear();
      msg.calls.forEach(c => callsMap.set(c.call_id, c));
      renderQueue();
    }

    else if (msg.event === "NEW_CALL") {
      callsMap.set(msg.call.call_id, msg.call);
      renderQueue();
      playIncomingPing();
    }

    else if (msg.event === "STATUS_CHANGED") {
      if (callsMap.has(msg.call_id)) {
        callsMap.get(msg.call_id).status = msg.status;
        renderQueue();
      }
    }

    else if (msg.event === "CALL_CLAIMED") {
      // ATOMIC LOCKOUT UPDATE:
      // Updates call status for all officer dashboards
      if (callsMap.has(msg.call_id)) {
        const call = callsMap.get(msg.call_id);
        call.status = "ENGAGED";
        call.officer_id = msg.officer_id;
        renderQueue();
      }
    }

    else if (msg.event === "CLAIM_RESULT") {
      if (msg.success) {
        currentActiveCallId = msg.call_id;
        startActiveCallSession(msg.call_id);
      } else {
        alert(`Cannot claim call: ${msg.reason}`);
      }
    }

    else if (msg.event === "WEBRTC_SIGNAL") {
      const payload = msg.payload;
      if (payload.answer && peerConnection) {
        console.log("[Officer WebRTC] Received Answer from Caller");
        await peerConnection.setRemoteDescription(new RTCSessionDescription(payload.answer));
      } else if (payload.candidate && peerConnection) {
        console.log("[Officer WebRTC] Received ICE Candidate");
        try {
          await peerConnection.addIceCandidate(new RTCIceCandidate(payload.candidate));
        } catch (e) {
          console.error("Error adding candidate:", e);
        }
      }
    }

    else if (msg.event === "CALL_ENDED") {
      if (currentActiveCallId === msg.call_id) {
        finishActiveCallSession(false);
      }
      callsMap.delete(msg.call_id);
      renderQueue();
    }
  }

  // Audio Ping for Incoming Call
  function playIncomingPing() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(1760, ctx.currentTime + 0.18);
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    } catch (e) {}
  }

  // Queue Renderer
  function renderQueue() {
    const activeCalls = Array.from(callsMap.values()).filter(c => c.status !== "ENDED");
    const waitingCalls = activeCalls.filter(c => c.status === "WAITING" || c.status === "RINGING" || c.status === "IVR");
    const hasIncoming = waitingCalls.length > 0;

    queueCountBadge.textContent = activeCalls.length;

    // Animate standby phone icon if calls are in queue
    if (standbyPhoneWrapper) {
      if (hasIncoming) {
        standbyPhoneWrapper.classList.add("ringing");
        if (idleTitle) {
          idleTitle.textContent = `🚨 INCOMING CALL RINGING (${waitingCalls.length})`;
          idleTitle.style.color = "var(--red-alert)";
        }
        if (idleSub) {
          idleSub.textContent = `Emergency Call #${waitingCalls[0].call_id} is in queue! Click [ ACCEPT CALL ] to dispatch.`;
        }
      } else {
        standbyPhoneWrapper.classList.remove("ringing");
        if (idleTitle) {
          idleTitle.textContent = "STATION STANDBY";
          idleTitle.style.color = "";
        }
        if (idleSub) {
          idleSub.textContent = 'Select and "Accept" an incoming emergency call from the queue to start two-way voice dispatch.';
        }
      }
    }

    if (activeCalls.length === 0) {
      queueEmptyState.style.display = "flex";
      queueList.innerHTML = "";
      return;
    }

    queueEmptyState.style.display = "none";
    queueList.innerHTML = "";

    activeCalls.forEach(call => {
      const isClaimedByMe = call.officer_id === officerId && call.status === "ENGAGED";
      const isClaimedByOther = call.officer_id && call.officer_id !== officerId && call.status === "ENGAGED";
      const isReady = call.status === "WAITING" || call.status === "RINGING" || call.status === "IVR";

      const card = document.createElement("div");
      card.className = `queue-card ${isReady ? "waiting" : "engaged"}`;

      let statusBadgeClass = "badge-waiting";
      let statusText = "READY FOR DISPATCH";

      if (call.status === "IVR") {
        statusBadgeClass = "badge-ivr";
        statusText = "IVR ADVISORY";
      } else if (isClaimedByMe) {
        statusBadgeClass = "badge-waiting";
        statusText = "ENGAGED (YOU)";
      } else if (isClaimedByOther) {
        statusBadgeClass = "badge-engaged";
        statusText = `ENGAGED BY ${call.officer_id}`;
      }

      card.innerHTML = `
        <div class="card-top">
          <div class="call-id-tag">
            <div class="card-phone-badge ${isReady ? 'ringing' : ''}">
              <svg viewBox="0 0 100 100" width="28" height="28">
                <circle cx="50" cy="50" r="48" fill="${isReady ? '#FF0000' : 'var(--primary)'}" stroke="#000000" stroke-width="3" />
                <g class="handset-group">
                  <path fill="${isReady ? '#FFFFFF' : '#000000'}" d="M 31.3 15.8 C 34.6 14.5 38.3 16.1 39.8 19.5 L 46.2 33.7 C 47.5 36.8 46.6 40.5 43.8 42.6 L 37.5 47.5 C 41.2 55.8 47.9 62.4 56.1 66.2 L 61.0 59.8 C 63.2 57.1 66.8 56.1 69.9 57.5 L 84.1 63.8 C 87.6 65.3 89.2 69.1 87.9 72.4 L 84.5 81.4 C 83.0 85.3 78.8 87.9 74.1 87.2 C 42.6 83.0 20.7 61.0 16.4 29.6 C 15.8 24.9 18.3 20.7 22.3 19.2 Z" />
                </g>
              </svg>
            </div>
            <span>#${call.call_id}</span>
          </div>
          <span class="status-badge ${statusBadgeClass}">${statusText}</span>
        </div>
        <div class="card-meta">
          <span>CALL GATEWAY: <strong>SIP-2007</strong></span>
          <span>DURATION: <strong>${call.duration || 0}s</strong></span>
        </div>
        <button class="claim-btn" id="claim-btn-${call.call_id}" ${isClaimedByOther || isClaimedByMe ? "disabled" : ""}>
          ${isClaimedByOther ? `[ 🔒 Locked by ${call.officer_id} ]` : isClaimedByMe ? "[ ✓ Engaged on Station ]" : "[ ACCEPT CALL ]"}
        </button>
      `;

      const claimBtn = card.querySelector(`#claim-btn-${call.call_id}`);
      if (claimBtn && !isClaimedByOther && !isClaimedByMe) {
        claimBtn.addEventListener("click", () => {
          claimCall(call.call_id);
        });
      }

      queueList.appendChild(card);
    });
  }

  // Claim Call Action
  function claimCall(callId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        action: "CLAIM_CALL",
        call_id: callId
      }));
    }
  }

  // Start Active Call Session & WebRTC
  async function startActiveCallSession(callId) {
    try {
      // 1. Get officer microphone stream
      localMicStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });

      // 2. Switch station panels
      stationIdle.classList.add("hidden");
      stationActive.classList.remove("hidden");
      activeCallIdEl.textContent = `EMERGENCY CALL #${callId}`;
      incidentNotes.value = "";
      setOfficerMicEnabled(true);

      // 3. Start active timer
      startActiveCallTimer();

      // 4. Setup Audio Context & Visualizers
      setupAudioEnvironment();

      // 5. Initialize WebRTC Peer Connection as Offerer
      initWebRTCOfferer(callId);

    } catch (err) {
      console.error("Error accessing officer microphone:", err);
      alert("Microphone access is required to accept emergency calls. Please grant microphone permissions.");
    }
  }

  // WebRTC Setup (Officer initiates Offer to Caller)
  async function initWebRTCOfferer(callId) {
    peerConnection = new RTCPeerConnection(rtcConfig);

    // Add officer mic track
    if (localMicStream) {
      localMicStream.getTracks().forEach(track => {
        peerConnection.addTrack(track, localMicStream);
      });
    }

    // Exchange ICE Candidates
    peerConnection.onicecandidate = (event) => {
      if (event.candidate && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          action: "WEBRTC_SIGNAL",
          call_id: callId,
          payload: { candidate: event.candidate }
        }));
      }
    };

    // Receive Caller's Audio Stream
    peerConnection.ontrack = (event) => {
      console.log("[Officer WebRTC] Received caller remote audio stream!");
      remoteStream = event.streams[0];
      remoteCallerAudio.srcObject = remoteStream;
      remoteCallerAudio.play().catch(e => console.warn("Remote audio play error:", e));

      // Connect remote audio to caller oscilloscope & recorder
      connectStreamsToAudioNodes();
    };

    // Create & Send SDP Offer
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);

    ws.send(JSON.stringify({
      action: "WEBRTC_SIGNAL",
      call_id: callId,
      payload: { offer: peerConnection.localDescription }
    }));
  }

  // Audio Oscilloscopes & Call Recorder Setup
  function setupAudioEnvironment() {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === "suspended") {
      audioContext.resume();
    }
  }

  function connectStreamsToAudioNodes() {
    if (!audioContext) return;

    // Mixed Destination for Call Recording (Officer + Caller mixed into one stream)
    const mixedDestination = audioContext.createMediaStreamDestination();

    // 1. Officer Mic Visualizer & Mix
    if (localMicStream) {
      const officerSource = audioContext.createMediaStreamSource(localMicStream);
      const officerAnalyser = audioContext.createAnalyser();
      officerAnalyser.fftSize = 256;
      officerSource.connect(officerAnalyser);
      officerSource.connect(mixedDestination);
      startOscilloscope(officerScope, officerAnalyser, "#BEF000", (id) => officerScopeAnimationId = id);
    }

    // 2. Caller Incoming Audio Visualizer & Mix
    if (remoteStream) {
      const callerSource = audioContext.createMediaStreamSource(remoteStream);
      const callerAnalyser = audioContext.createAnalyser();
      callerAnalyser.fftSize = 256;
      callerSource.connect(callerAnalyser);
      callerSource.connect(mixedDestination);
      startOscilloscope(callerScope, callerAnalyser, "#3ddc84", (id) => callerScopeAnimationId = id);
    }

    // 3. Start MediaRecorder on Mixed Stream
    try {
      recordedChunks = [];
      const options = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") 
        ? { mimeType: "audio/webm;codecs=opus" } 
        : {};

      mediaRecorder = new MediaRecorder(mixedDestination.stream, options);

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          recordedChunks.push(e.data);
        }
      };

      mediaRecorder.start(1000); // 1-second chunks
      console.log("[Recorder] In-browser dual-audio session recorder started!");
    } catch (e) {
      console.warn("MediaRecorder init failed:", e);
    }
  }

  // Oscilloscope Canvas Rendering Function
  function startOscilloscope(canvas, analyser, strokeColor, saveAnimId) {
    const ctx = canvas.getContext("2d");
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function draw() {
      const animId = requestAnimationFrame(draw);
      saveAnimId(animId);

      analyser.getByteTimeDomainData(dataArray);

      const width = canvas.width;
      const height = canvas.height;

      ctx.fillStyle = "rgba(4, 8, 14, 0.4)";
      ctx.fillRect(0, 0, width, height);

      ctx.lineWidth = 2;
      ctx.strokeStyle = strokeColor;
      ctx.shadowColor = strokeColor;
      ctx.shadowBlur = 6;

      ctx.beginPath();
      const sliceWidth = (width * 1.0) / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * height) / 2;

        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
        x += sliceWidth;
      }

      ctx.lineTo(width, height / 2);
      ctx.stroke();
    }

    draw();
  }

  // Timer Controls
  function startActiveCallTimer() {
    if (activeTimerInterval) clearInterval(activeTimerInterval);
    activeCallDuration = 0;
    activeTimerInterval = setInterval(() => {
      activeCallDuration++;
      const mins = String(Math.floor(activeCallDuration / 60)).padStart(2, "0");
      const secs = String(activeCallDuration % 60).padStart(2, "0");
      activeCallTimerEl.textContent = `${mins}:${secs}`;
    }, 1000);
  }

  // Quick Tags
  tagBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const tag = btn.dataset.tag;
      if (!incidentNotes.value.includes(tag)) {
        incidentNotes.value = (tag + " " + incidentNotes.value).trim();
      }
    });
  });

  // Mute Officer Mic
  function setOfficerMicEnabled(enabled) {
    if (localMicStream) {
      localMicStream.getAudioTracks().forEach(t => t.enabled = enabled);
    }
    isOfficerMuted = !enabled;

    if (isOfficerMuted) {
      officerMuteBtn.classList.add("active");
      officerMuteText.textContent = "[ UNMUTE MIC ]";
    } else {
      officerMuteBtn.classList.remove("active");
      officerMuteText.textContent = "[ MUTE MIC ]";
    }
  }

  officerMuteBtn.addEventListener("click", () => {
    setOfficerMicEnabled(isOfficerMuted);
  });

  // Disconnect & Save Recording
  endDispatchBtn.addEventListener("click", () => {
    finishActiveCallSession(true);
  });

  async function finishActiveCallSession(notifyServer = true) {
    const finishedCallId = currentActiveCallId;
    const finalDuration = activeCallDuration;
    const finalNotes = incidentNotes.value.trim();

    if (notifyServer && ws && ws.readyState === WebSocket.OPEN && finishedCallId) {
      ws.send(JSON.stringify({
        action: "END_CALL",
        call_id: finishedCallId
      }));
    }

    // Stop MediaRecorder and Upload Recording
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(recordedChunks, { type: "audio/webm" });
        await uploadRecording(finishedCallId, audioBlob, finalDuration, finalNotes);
      };
      mediaRecorder.stop();
    }

    // Clean up streams & connections
    if (peerConnection) {
      peerConnection.close();
      peerConnection = null;
    }

    if (localMicStream) {
      localMicStream.getTracks().forEach(t => t.stop());
      localMicStream = null;
    }

    if (activeTimerInterval) {
      clearInterval(activeTimerInterval);
      activeTimerInterval = null;
    }

    if (callerScopeAnimationId) cancelAnimationFrame(callerScopeAnimationId);
    if (officerScopeAnimationId) cancelAnimationFrame(officerScopeAnimationId);

    // Reset UI to standby
    currentActiveCallId = null;
    stationActive.classList.add("hidden");
    stationIdle.classList.remove("hidden");
    activeCallTimerEl.textContent = "00:00";
    setOfficerMicEnabled(true);
  }

  // Upload Recording to Backend
  async function uploadRecording(callId, audioBlob, duration, notes) {
    if (!callId || audioBlob.size === 0) return;

    try {
      const formData = new FormData();
      formData.append("audio_file", audioBlob, `call_${callId}.webm`);
      formData.append("duration", duration);
      formData.append("notes", notes);

      const res = await fetch(`/api/recordings/${callId}`, {
        method: "POST",
        body: formData
      });

      if (res.ok) {
        console.log(`[Recording] Successfully uploaded recording for Call #${callId}`);
        fetchRecordingsVault();
      }
    } catch (e) {
      console.error("Error uploading recording:", e);
    }
  }

  // Live UTC & Local Taskbar Clocks
  function updateClocks() {
    const now = new Date();
    if (systemClock) {
      systemClock.textContent = now.toISOString().substring(11, 19) + " UTC";
    }
    if (taskbarClock) {
      taskbarClock.textContent = now.toTimeString().substring(0, 8) + " LOC";
    }
  }
  setInterval(updateClocks, 1000);
  updateClocks();

  // Taskbar Start Menu Popup Controls
  if (taskbarStartBtn && startMenuPopup) {
    taskbarStartBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      startMenuPopup.classList.toggle("hidden");
    });

    document.addEventListener("click", (e) => {
      if (!startMenuPopup.contains(e.target) && e.target !== taskbarStartBtn) {
        startMenuPopup.classList.add("hidden");
      }
    });

    if (menuIntelBtn) {
      menuIntelBtn.addEventListener("click", () => {
        if (startMenuPopup) startMenuPopup.classList.add("hidden");
        openVaultModal();
      });
    }

    if (menuRefreshBtn) {
      menuRefreshBtn.addEventListener("click", () => {
        if (startMenuPopup) startMenuPopup.classList.add("hidden");
        if (ws && ws.readyState === WebSocket.OPEN) {
          reconnectWebSocket();
        }
      });
    }

    if (menuAboutBtn) {
      menuAboutBtn.addEventListener("click", () => {
        if (startMenuPopup) startMenuPopup.classList.add("hidden");
        alert("CERS-OS v2.4 (Cyber Emergency Response System)\nProtocol: Atomic Lockout Active\nAudio Codec: Opus WebRTC (48kHz)\nSpeech SER Model: Wav2vec2 (Phase 2 Preview)");
      });
    }
  }

  // Taskbar & Modal Window Controls
  let cachedRecordings = [];
  let selectedRecording = null;

  function openVaultModal() {
    if (intelModalBackdrop) {
      intelModalBackdrop.classList.remove("hidden");
      intelModalBackdrop.style.setProperty("display", "flex", "important");
    }
    fetchRecordingsVault();
  }

  function closeVaultModal() {
    if (intelModalBackdrop) {
      intelModalBackdrop.classList.add("hidden");
      intelModalBackdrop.style.removeProperty("display");
    }
    if (intelAudioPlayer) intelAudioPlayer.pause();
  }

  window.openVaultModal = openVaultModal;
  window.closeVaultModal = closeVaultModal;

  if (openIntelBtn) {
    openIntelBtn.addEventListener("click", openVaultModal);
  }

  if (headerVaultBtn) {
    headerVaultBtn.addEventListener("click", openVaultModal);
  }

  if (closeIntelModalBtn) {
    closeIntelModalBtn.addEventListener("click", closeVaultModal);
  }

  if (minIntelModalBtn) {
    minIntelModalBtn.addEventListener("click", closeVaultModal);
  }

  if (standbyPhoneWrapper) {
    standbyPhoneWrapper.addEventListener("click", () => {
      standbyPhoneWrapper.classList.toggle("ringing");
    });
  }

  if (maxIntelModalBtn && retroWindowModal) {
    maxIntelModalBtn.addEventListener("click", () => {
      const isMax = retroWindowModal.classList.toggle("maximized");
      maxIntelModalBtn.innerHTML = isMax
        ? `<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="1" width="6" height="6"></rect><rect x="1" y="3" width="6" height="6" fill="var(--surface-alt)"></rect></svg>`
        : `<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="1" width="8" height="8"></rect></svg>`;
    });
  }

  if (intelModalBackdrop) {
    intelModalBackdrop.addEventListener("click", (e) => {
      if (e.target === intelModalBackdrop) {
        closeVaultModal();
      }
    });
  }

  // Search Filter
  intelSearchInput.addEventListener("input", () => {
    const query = intelSearchInput.value.toLowerCase().trim();
    renderArchiveList(cachedRecordings.filter(r => 
      String(r.call_id).toLowerCase().includes(query) || 
      (r.notes && r.notes.toLowerCase().includes(query))
    ));
  });

  // Fetch Recordings Archive
  async function fetchRecordingsVault() {
    try {
      const res = await fetch("/api/recordings");
      if (res.ok) {
        cachedRecordings = await res.json();
        const count = cachedRecordings.length;
        if (vaultBadgeCount) vaultBadgeCount.textContent = count;
        if (sidebarCount) sidebarCount.textContent = count;
        renderArchiveList(cachedRecordings);
        
        // Auto-select first recording if none selected
        if (cachedRecordings.length > 0 && !selectedRecording) {
          selectRecordingForAnalysis(cachedRecordings[0]);
        }
      }
    } catch (e) {
      console.error("Error fetching recordings:", e);
    }
  }

  // Render Sidebar Tree List
  function renderArchiveList(recordings) {
    if (!modalRecordingsList) return;

    if (!recordings || recordings.length === 0) {
      modalRecordingsList.innerHTML = `<div class="empty-list-notice">No recorded calls found in archive.</div>`;
      return;
    }

    modalRecordingsList.innerHTML = "";
    recordings.forEach(rec => {
      const item = document.createElement("div");
      const isSelected = selectedRecording && selectedRecording.filename === rec.filename;
      item.className = `recording-tree-item ${isSelected ? "selected" : ""}`;
      
      const timeStr = new Date(rec.timestamp * 1000).toLocaleTimeString();
      const dur = rec.duration ? `${rec.duration}s` : "Audio";

      item.innerHTML = `
        <div class="tree-item-top">
          <span class="tree-item-id">🚨 #${rec.call_id}</span>
          <span class="tree-item-tag">AI ANALYZED</span>
        </div>
        <div class="tree-item-sub">
          <span>${timeStr}</span>
          <span>⏱️ ${dur}</span>
        </div>
      `;

      item.addEventListener("click", () => {
        selectRecordingForAnalysis(rec);
      });

      modalRecordingsList.appendChild(item);
    });
  }

  // Select Call & Populate Deep Forensic Intel (Phase 2 Preview)
  function selectRecordingForAnalysis(rec) {
    selectedRecording = rec;
    
    // Update active highlight in sidebar
    renderArchiveList(cachedRecordings);

    intelEmptyPrompt.classList.add("hidden");
    intelDetailView.classList.remove("hidden");

    // Populate Headers
    intelCallId.textContent = `EMERGENCY CALL #${rec.call_id}`;
    intelAudioFilename.textContent = rec.filename || `call_${rec.call_id}.webm`;
    intelTimestamp.textContent = `📅 ${new Date(rec.timestamp * 1000).toLocaleString()}`;
    intelDuration.textContent = `⏱️ Duration: ${rec.duration || 15.0}s`;
    intelOfficer.textContent = `👮 Handled by: ${rec.officer_id || officerId}`;

    // Populate Audio Player
    intelAudioPlayer.src = rec.url;

    // Extracted Intelligence Key Data (Mocked for Phase 2)
    const mockLocations = [
      "Sector 4, Cyber Tech Park, Building B-4 (Floor 3)",
      "Financial District, Tower 9, Suite 1402",
      "Industrial Area Phase 2, Data Center Alpha",
      "Green Glen Layout, Block C, Residence #104"
    ];
    const locIndex = parseInt(rec.call_id) % mockLocations.length || 0;
    intelLocationVal.textContent = mockLocations[locIndex];

    const threatCategories = [
      "RANSOMWARE EXTORTION (Strain: Vecna-X)",
      "UNAUTHORIZED WIRE TRANSFER / CEO FRAUD",
      "INFRASTRUCTURE DDOS & PRIVILEGE HIJACK",
      "DATA THEFT & EXTRADITION EXTORTION"
    ];
    intelCategoryVal.textContent = threatCategories[locIndex];

    intelRansomVal.textContent = (rec.call_id === "2007") 
      ? "5.0 BITCOIN (~$340,000 USD Demanded)" 
      : "2.5 ETHEREUM (~$8,500 USD Unauthorized)";

    // Populate Diarized Transcript (Mocked for Phase 2 with interactive seek on click)
    intelTranscriptBox.innerHTML = `
      <div class="transcript-line caller" data-seek="1" style="cursor: pointer;" title="Click to seek audio">
        <span class="time">[00:01] ▶</span>
        <span class="speaker">📱 CALLER:</span>
        <span class="text">"Emergency! Someone breached our mainframe and encrypted all databases! They are demanding a transfer immediately!"</span>
      </div>
      <div class="transcript-line officer" data-seek="6" style="cursor: pointer;" title="Click to seek audio">
        <span class="time">[00:06] ▶</span>
        <span class="speaker">👮 OFFICER:</span>
        <span class="text">"This is Officer Alpha. Remain calm and do not authorize any transactions. Confirm your physical location."</span>
      </div>
      <div class="transcript-line caller" data-seek="11" style="cursor: pointer;" title="Click to seek audio">
        <span class="time">[00:11] ▶</span>
        <span class="speaker">📱 CALLER:</span>
        <span class="text">"We are at ${mockLocations[locIndex]}! The countdown on our screens says 2 hours remaining!"</span>
      </div>
      <div class="transcript-line officer" data-seek="15" style="cursor: pointer;" title="Click to seek audio">
        <span class="time">[00:15] ▶</span>
        <span class="speaker">👮 OFFICER:</span>
        <span class="text">"Coordinates verified. Ground cyber incident team is being dispatched. Sever all external ethernet connections immediately."</span>
      </div>
    `;

    // Add click-to-seek audio behavior
    intelTranscriptBox.querySelectorAll(".transcript-line").forEach(line => {
      line.addEventListener("click", () => {
        const seekTime = parseFloat(line.getAttribute("data-seek"));
        if (!isNaN(seekTime) && intelAudioPlayer) {
          intelAudioPlayer.currentTime = seekTime;
          intelAudioPlayer.play().catch(() => {});
        }
      });
    });

    // Notes textarea
    intelNotesTextarea.value = rec.notes || `[INCIDENT LOGGED - PRIORITY DISPATCH APPROVED] Verified distress from Call #${rec.call_id}. Location flagged for rapid team routing.`;
  }

  saveIntelNotesBtn.addEventListener("click", () => {
    alert("Forensic notes saved to case dossier!");
  });

  refreshVaultBtn.addEventListener("click", fetchRecordingsVault);
  refreshQueueBtn.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      reconnectWebSocket();
    }
  });

  // Initial Vault Load & Count
  fetchRecordingsVault();
});
