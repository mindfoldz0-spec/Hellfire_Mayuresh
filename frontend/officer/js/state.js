/** Shared mutable state for the officer console. */

export const state = {
  officerId: null,
  accessToken: sessionStorage.getItem("cers_access_token") || "",
  ws: null,
  socketGeneration: 0,
  shouldReconnect: false,
  reconnectTimer: null,

  callsMap: new Map(),
  currentActiveCallId: null,
  activeCallDuration: 0,
  activeTimerInterval: null,
  durationUiInterval: null,
  messageChain: Promise.resolve(),
  isFinishing: false,

  peerConnection: null,
  localMicStream: null,
  remoteStream: null,
  audioContext: null,
  pingContext: null,
  mixedDestination: null,
  mediaRecorder: null,
  recordedChunks: [],
  isOfficerMuted: false,
  pendingCandidates: [],
  micSource: null,
  remoteSource: null,
  remoteConnected: false,
  rtcConfigPromise: null,

  callerScopeAnimationId: null,
  officerScopeAnimationId: null,

  cachedRecordings: [],
  selectedRecording: null,
};
