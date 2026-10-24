// Cyber Emergency Response System - Caller Client
document.addEventListener("DOMContentLoaded", () => {
  // DOM Elements
  const dialpadScreen = document.getElementById("dialpad-screen");
  const incallScreen = document.getElementById("incall-screen");
  const phoneInput = document.getElementById("phone-number-input");
  const callBtn = document.getElementById("call-btn");
  const clearBtn = document.getElementById("clear-btn");
  const keyBtns = document.querySelectorAll(".key-btn");
  
  const incallNumberTitle = document.getElementById("incall-number-title");
  const callStatusPill = document.getElementById("call-status-pill");
  const callStatusText = document.getElementById("call-status-text");
  const callTimer = document.getElementById("call-timer");
  const visualizerCaption = document.getElementById("visualizer-label");
  const visualizerCanvas = document.getElementById("caller-visualizer");
  const canvasCtx = visualizerCanvas.getContext("2d");
  
  const officerCard = document.getElementById("officer-card");
  const officerNameDisplay = document.getElementById("officer-name-display");
  const toggleMuteBtn = document.getElementById("toggle-mute-btn");
  const muteLabel = document.getElementById("mute-label");
  const micIconOn = document.getElementById("mic-icon-on");
  const micIconOff = document.getElementById("mic-icon-off");
  const hangupBtn = document.getElementById("hangup-btn");

  const ivrAudio = document.getElementById("ivr-audio");
  const remoteAudio = document.getElementById("remote-audio");

  // State
  let callId = "2007";
  let ws = null;
  let localStream = null;
  let peerConnection = null;
  let timerInterval = null;
  let callSeconds = 0;
  let isMuted = false;
  let audioCtx = null;
  let analyserNode = null;
  let animationFrameId = null;

  // WebRTC STUN Configuration
  const rtcConfig = {
    iceServers: [
      { urls: "stun:stun.l.google.com:19302" },
      { urls: "stun:stun1.l.google.com:19302" }
    ]
  };

  // DTMF Tone Frequencies
  const dtmfFrequencies = {
    "1": [697, 1209], "2": [697, 1336], "3": [697, 1477],
    "4": [770, 1209], "5": [770, 1336], "6": [770, 1477],
    "7": [852, 1209], "8": [852, 1336], "9": [852, 1477],
    "*": [941, 1209], "0": [941, 1336], "#": [941, 1477]
  };

  function playTone(freq1, freq2, duration = 0.12) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gainNode = ctx.createGain();

      osc1.frequency.value = freq1;
      osc2.frequency.value = freq2;

      gainNode.gain.setValueAtTime(0.12, ctx.currentTime);
      gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);

      osc1.connect(gainNode);
      osc2.connect(gainNode);
      gainNode.connect(ctx.destination);

      osc1.start();
      osc2.start();
      osc1.stop(ctx.currentTime + duration);
      osc2.stop(ctx.currentTime + duration);
    } catch (e) {
      // AudioContext muted/unsupported
    }
  }

  // Keypad Click Handlers
  keyBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      if (phoneInput.value.length < 8) {
        phoneInput.value += key;
      }
      const freqs = dtmfFrequencies[key] || [800, 1200];
      playTone(freqs[0], freqs[1]);
    });
  });

  clearBtn.addEventListener("click", () => {
    phoneInput.value = phoneInput.value.slice(0, -1);
  });

  // Call Button Click
  callBtn.addEventListener("click", async () => {
    callId = phoneInput.value.trim() || "2007";
    await startEmergencyCall(callId);
  });

  async function startEmergencyCall(id) {
    try {
      // 1. Request microphone access
      localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      
      // Initially MUTE microphone stream so IVR is strictly heard first
      setMicEnabled(false);

      // 2. Switch UI to active call
      dialpadScreen.classList.remove("active");
      incallScreen.classList.add("active");
      incallNumberTitle.textContent = id;
      setCallStatus("CONNECTING GATEWAY...", "status-connecting");
      visualizerCaption.textContent = "ESTABLISHING SECURE WEBSOCKET...";

      // 3. Setup Audio Visualizer
      setupVisualizer();

      // 4. Connect WebSocket
      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${wsProtocol}//${window.location.host}/ws/caller/${id}`;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.log("[Caller WS] Connected to backend gateway");
        playIVRSequence();
      };

      ws.onmessage = async (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleWebSocketMessage(msg);
        } catch (e) {
          console.error("[Caller WS] Parse error:", e);
        }
      };

      ws.onclose = () => {
        console.log("[Caller WS] Disconnected");
        terminateCall(false);
      };

      ws.onerror = (err) => {
        console.error("[Caller WS] Error:", err);
      };

    } catch (err) {
      console.error("Microphone access error:", err);
      alert("Microphone permission is required to place an emergency call. Please allow microphone access.");
    }
  }

  // Step 2: IVR Greeting & The Beep Tone
  function playIVRSequence() {
    setCallStatus("IVR ADVISORY IN PROGRESS", "status-connecting");
    visualizerCaption.textContent = "IVR ADVISORY PLAYING (MIC MUTED)";

    // Route IVR audio into visualizer
    try {
      if (audioCtx && ivrAudio) {
        const source = audioCtx.createMediaElementSource(ivrAudio);
        source.connect(analyserNode);
        analyserNode.connect(audioCtx.destination);
      }
    } catch (e) {
      // MediaElementAudioSource already connected
    }

    ivrAudio.currentTime = 0;
    ivrAudio.play().catch(e => {
      console.warn("Autoplay policy blocked IVR audio; playing directly:", e);
    });

    ivrAudio.onended = () => {
      console.log("[IVR] Audio and Beep concluded! Unmuting mic...");
      // Un-mute mic the instant the beep concludes
      setMicEnabled(true);
      
      // Connect microphone to visualizer
      connectMicToVisualizer();

      setCallStatus("MICROPHONE LIVE - WAITING DISPATCH", "status-connecting");
      visualizerCaption.textContent = "MICROPHONE ACTIVE - DESCRIBE INCIDENT";

      // Inform backend that caller has entered the distress listening window
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "IVR_FINISHED" }));
      }
    };
  }

  // WebSocket Message Router
  async function handleWebSocketMessage(msg) {
    console.log("[Caller WS Received]", msg);

    if (msg.event === "OFFICER_ASSIGNED") {
      officerCard.classList.remove("hidden");
      officerNameDisplay.textContent = msg.officer_id || "OFFICER ON DUTY";
      setCallStatus("OFFICER CONNECTING...", "status-connecting");
      visualizerCaption.textContent = "SYNCHRONIZING WEBRTC AUDIO...";
    }

    else if (msg.event === "WEBRTC_SIGNAL") {
      const payload = msg.payload;
      if (!peerConnection) {
        initPeerConnection();
      }

      if (payload.offer) {
        console.log("[Caller WebRTC] Received Offer from Officer");
        await peerConnection.setRemoteDescription(new RTCSessionDescription(payload.offer));
        const answer = await peerConnection.createAnswer();
        await peerConnection.setLocalDescription(answer);

        ws.send(JSON.stringify({
          action: "WEBRTC_SIGNAL",
          payload: { answer: peerConnection.localDescription }
        }));
      } else if (payload.candidate) {
        console.log("[Caller WebRTC] Received ICE candidate");
        try {
          await peerConnection.addIceCandidate(new RTCIceCandidate(payload.candidate));
        } catch (e) {
          console.error("Error adding ICE candidate:", e);
        }
      }
    }

    else if (msg.event === "CALL_ENDED") {
      alert("Call ended by the dispatch station.");
      terminateCall(false);
    }
  }

  // WebRTC Peer Connection Initialization
  function initPeerConnection() {
    peerConnection = new RTCPeerConnection(rtcConfig);

    // Add local microphone tracks
    if (localStream) {
      localStream.getTracks().forEach(track => {
        peerConnection.addTrack(track, localStream);
      });
    }

    // ICE Candidate generation
    peerConnection.onicecandidate = (event) => {
      if (event.candidate && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          action: "WEBRTC_SIGNAL",
          payload: { candidate: event.candidate }
        }));
      }
    };

    // Receive Officer Audio
    peerConnection.ontrack = (event) => {
      console.log("[Caller WebRTC] Remote Officer audio track received!");
      remoteAudio.srcObject = event.streams[0];
      remoteAudio.play().catch(e => console.warn("Remote audio play error:", e));

      // Visual updates
      setCallStatus("OFFICER CONNECTED (2-WAY)", "status-active");
      visualizerCaption.textContent = "TWO-WAY AUDIO STREAM ACTIVE";
      startCallTimer();
    };

    peerConnection.onconnectionstatechange = () => {
      console.log("[Caller WebRTC] State:", peerConnection.connectionState);
      if (peerConnection.connectionState === "disconnected" || peerConnection.connectionState === "failed") {
        setCallStatus("CONNECTION LOST", "status-connecting");
      }
    };
  }

  // Audio Visualizer Setup (HTML5 Canvas Oscilloscope)
  function setupVisualizer() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }

    analyserNode = audioCtx.createAnalyser();
    analyserNode.fftSize = 256;
    analyserNode.smoothingTimeConstant = 0.8;

    drawVisualizer();
  }

  function connectMicToVisualizer() {
    if (localStream && audioCtx && analyserNode) {
      try {
        const micSource = audioCtx.createMediaStreamSource(localStream);
        micSource.connect(analyserNode);
      } catch (e) {
        console.error("Error connecting mic to visualizer:", e);
      }
    }
  }

  function drawVisualizer() {
    const bufferLength = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function render() {
      animationFrameId = requestAnimationFrame(render);
      analyserNode.getByteTimeDomainData(dataArray);

      const width = visualizerCanvas.width;
      const height = visualizerCanvas.height;

      canvasCtx.fillStyle = "rgba(4, 8, 16, 0.3)";
      canvasCtx.fillRect(0, 0, width, height);

      canvasCtx.lineWidth = 2.5;
      canvasCtx.strokeStyle = isMuted ? "#ff1744" : "#00e5ff";
      canvasCtx.shadowColor = isMuted ? "#ff1744" : "#00e5ff";
      canvasCtx.shadowBlur = 8;

      canvasCtx.beginPath();
      const sliceWidth = (width * 1.0) / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * height) / 2;

        if (i === 0) {
          canvasCtx.moveTo(x, y);
        } else {
          canvasCtx.lineTo(x, y);
        }
        x += sliceWidth;
      }

      canvasCtx.lineTo(width, height / 2);
      canvasCtx.stroke();
    }

    render();
  }

  // Mic Mute Control
  function setMicEnabled(enabled) {
    if (localStream) {
      localStream.getAudioTracks().forEach(track => {
        track.enabled = enabled;
      });
    }
    isMuted = !enabled;

    if (isMuted) {
      toggleMuteBtn.classList.add("active");
      muteLabel.textContent = "Unmute";
      micIconOn.classList.add("hidden");
      micIconOff.classList.remove("hidden");
    } else {
      toggleMuteBtn.classList.remove("active");
      muteLabel.textContent = "Mute";
      micIconOn.classList.remove("hidden");
      micIconOff.classList.add("hidden");
    }
  }

  toggleMuteBtn.addEventListener("click", () => {
    setMicEnabled(isMuted); // toggles state
  });

  // Call Timer
  function startCallTimer() {
    if (timerInterval) clearInterval(timerInterval);
    callSeconds = 0;
    timerInterval = setInterval(() => {
      callSeconds++;
      const mins = String(Math.floor(callSeconds / 60)).padStart(2, "0");
      const secs = String(callSeconds % 60).padStart(2, "0");
      callTimer.textContent = `${mins}:${secs}`;
    }, 1000);
  }

  function setCallStatus(text, statusClass) {
    callStatusText.textContent = text;
    callStatusPill.className = `status-pill ${statusClass}`;
  }

  // End Call / Hangup
  hangupBtn.addEventListener("click", () => {
    terminateCall(true);
  });

  function terminateCall(notifyServer = true) {
    if (notifyServer && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "END_CALL" }));
    }

    if (ws) {
      ws.close();
      ws = null;
    }

    if (ivrAudio) {
      ivrAudio.pause();
      ivrAudio.currentTime = 0;
    }

    if (peerConnection) {
      peerConnection.close();
      peerConnection = null;
    }

    if (localStream) {
      localStream.getTracks().forEach(t => t.stop());
      localStream = null;
    }

    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }

    if (animationFrameId) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }

    // Reset UI
    officerCard.classList.add("hidden");
    callTimer.textContent = "00:00";
    setMicEnabled(true);
    incallScreen.classList.remove("active");
    dialpadScreen.classList.add("active");
  }
});
