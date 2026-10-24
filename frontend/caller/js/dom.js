/** Cached DOM references for the caller dialer. */

export const dialpadScreen = document.getElementById("dialpad-screen");
export const incallScreen = document.getElementById("incall-screen");
export const phoneInput = document.getElementById("phone-number-input");
export const callBtn = document.getElementById("call-btn");
export const clearBtn = document.getElementById("clear-btn");
export const keyBtns = document.querySelectorAll(".key-btn");

export const incallNumberTitle = document.getElementById("incall-number-title");
export const callStatusPill = document.getElementById("call-status-pill");
export const callStatusText = document.getElementById("call-status-text");
export const callTimer = document.getElementById("call-timer");
export const visualizerCaption = document.getElementById("visualizer-label");
export const visualizerCanvas = document.getElementById("caller-visualizer");
export const canvasCtx = visualizerCanvas.getContext("2d");
export const skipIvrBtn = document.getElementById("skip-ivr-btn");

export const officerCard = document.getElementById("officer-card");
export const officerNameDisplay = document.getElementById("officer-name-display");
export const toggleMuteBtn = document.getElementById("toggle-mute-btn");
export const muteLabel = document.getElementById("mute-label");
export const micIconOn = document.getElementById("mic-icon-on");
export const micIconOff = document.getElementById("mic-icon-off");
export const hangupBtn = document.getElementById("hangup-btn");

export const ivrAudio = document.getElementById("ivr-audio");
export const remoteAudio = document.getElementById("remote-audio");
