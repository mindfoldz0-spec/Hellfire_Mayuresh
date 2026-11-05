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
  // server_time - client_time, measured from QUEUE_SNAPSHOT; keeps the queue
  // duration ticker immune to workstation clock skew.
  serverClockOffset: 0,

  peerConnection: null,
  localMicStream: null,
  remoteStream: null,
  audioContext: null,
  pingContext: null,
  mixedDestination: null,
  // One recorder per channel: mixed (reference), officer, caller.
  trackRecorders: {},
  // Client-generated id so a beacon + normal upload cannot create duplicates.
  callClientUuid: "",
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
  recordingsOffset: 0,
  recordingsTotal: 0,
  recordingsQuery: "",
};
