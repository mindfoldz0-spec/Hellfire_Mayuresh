/** Officer console bootstrap. */

import "./websocket.js";
import { initAuth, checkAuth } from "./auth.js";
import { initCallControls } from "./call.js";
import { initVault } from "./vault.js";
import { initUi } from "./ui.js";

initAuth();
initCallControls();
initVault();
initUi();
checkAuth();
