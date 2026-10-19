import {
  ApiError,
  apiFetch,
  showAlert,
  setBusy,
} from "./api.js?v=12.5";
import { clearFeedback, showUiError } from "./ui-feedback.js?v=12.5";

// ---------------------------------------------------------------------------
// Constantes de estado de sesion movil
// ---------------------------------------------------------------------------
const SESSION_STATES = Object.freeze({
  MATCH: "MATCH",
  MISMATCH: "MISMATCH",
});

// ---------------------------------------------------------------------------
// Referencias DOM
// ---------------------------------------------------------------------------
const alerts               = document.getElementById("alerts");
const authNoSessionNotice  = document.getElementById("authNoSessionNotice");
const authMismatchNotice   = document.getElementById("authMismatchNotice");
const sessionContent       = document.getElementById("sessionContent");
const successContent       = document.getElementById("successContent");

// Panel MISMATCH
const mismatchAssignedLabel = document.getElementById("mismatchAssignedLabel");
const mismatchCurrentLabel  = document.getElementById("mismatchCurrentLabel");
const loginAsAssignedBtn    = document.getElementById("loginAsAssignedBtn");

// Panel NO_SESSION
const noSessionAssignedLabel = document.getElementById("noSessionAssignedLabel");
const noSessionUserLabel     = document.getElementById("noSessionUserLabel");
const loginBtn               = document.getElementById("loginBtn");

// Panel MATCH
const docTitle    = document.getElementById("docTitle");
const signerLabel = document.getElementById("signerLabel");
const canvas      = document.getElementById("mobileSignatureCanvas");
const clearBtn    = document.getElementById("clearBtn");
const confirmBtn  = document.getElementById("confirmBtn");

// ---------------------------------------------------------------------------
// Estado de modulo
// ---------------------------------------------------------------------------
let pad           = null;
let currentToken  = null;
let sessionLoaded = false;   // bandera anti-doble-carga

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getTokenFromUrl() {
  const match = window.location.pathname.match(/\/firma-movil\/([^/]+)/);
  return match ? match[1] : null;
}

function buildLoginUrl() {
  const returnUrl = encodeURIComponent(window.location.href);
  return `/auth/login?next=${returnUrl}`;
}

function hideAllPanels() {
  if (authNoSessionNotice) authNoSessionNotice.hidden = true;
  if (authMismatchNotice)  authMismatchNotice.hidden  = true;
  if (sessionContent)      sessionContent.hidden      = true;
}

function showNoSession(assignedSigner = "") {
  hideAllPanels();
  if (noSessionAssignedLabel) {
    // Muestra el nombre completo si llega del body del error, si no el usuario
    noSessionAssignedLabel.textContent = assignedSigner || "—";
  }
  if (authNoSessionNotice) authNoSessionNotice.hidden = false;
}

function showMismatch(assignedSigner = "", currentUser = "") {
  hideAllPanels();
  if (mismatchAssignedLabel) mismatchAssignedLabel.textContent = assignedSigner || "—";
  if (mismatchCurrentLabel  && currentUser) mismatchCurrentLabel.textContent = currentUser;
  if (authMismatchNotice) authMismatchNotice.hidden = false;
}

// ---------------------------------------------------------------------------
// Canvas / pad de firma
// ---------------------------------------------------------------------------
function resizeCanvas() {
  if (!canvas) return;
  const previous = pad && !pad.isEmpty() ? pad.toDataURL("image/png") : null;
  const ratio    = Math.max(window.devicePixelRatio || 1, 1);
  canvas.width   = canvas.offsetWidth  * ratio;
  canvas.height  = canvas.offsetHeight * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  if (pad && previous) {
    pad.fromDataURL(previous);
  }
}

function initPad() {
  if (pad) { resizeCanvas(); return; }
  pad = new window.SignaturePad(canvas, {
    backgroundColor: "rgba(0, 0, 0, 0)",
    penColor: "rgb(0, 0, 0)",
  });
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

// ---------------------------------------------------------------------------
// loadSession  —  UNA SOLA LLAMADA POR CARGA DE PAGINA
// ---------------------------------------------------------------------------
async function loadSession() {
  // Guardia anti-doble-ejecucion (modulo cargado dos veces, listener duplicado, etc.)
  if (sessionLoaded) return;
  sessionLoaded = true;

  currentToken = getTokenFromUrl();
  if (!currentToken) {
    showAlert(alerts, "danger", "Enlace de firma movil invalido o sin token.");
    hideAllPanels();
    return;
  }

  clearFeedback(alerts);

  let data;
  try {
    data = await apiFetch(
      `/api/firma/movil/sesion/${encodeURIComponent(currentToken)}`
    );
  } catch (err) {
    // ---- Caso A: sin sesion institucional (HTTP 401) ----
    // apiFetch lanza ApiError con err.status === 401.
    // NO se muestra error generico; se muestra el panel NO_SESSION.
    if (err instanceof ApiError && err.status === 401) {
      // El backend 401 no incluye assigned_signer en el cuerpo porque get_current_principal
      // falla antes de leer la sesion. Mostramos el panel sin nombre (sera llenado con "—").
      const assignedFromErr =
        (typeof err.detail === "object" && err.detail?.assigned_signer) ||
        (typeof err.detail === "string" ? "" : "");
      showNoSession(assignedFromErr);
      return;   // <-- retorno limpio; sin relanzar; sin reintento automatico
    }
    // ---- Cualquier otro error (red, 500, etc.) ----
    hideAllPanels();
    showUiError(alerts, err, { onRetry: reloadSession });
    return;
  }

  // ---- Caso B / C: hay sesion ----
  const assigned  = (data.assigned_signer || data.usrid || "").toLowerCase();
  const state     = data.session_state || SESSION_STATES.MATCH;
  const preparedBy = data.prepared_by || "";

  if (state === SESSION_STATES.MATCH) {
    // Caso B: sesion correcta -> puede firmar
    hideAllPanels();
    if (docTitle)    docTitle.textContent    = data.docnom   || "Documento";
    if (signerLabel) signerLabel.textContent = assigned;
    if (sessionContent) sessionContent.hidden = false;
    initPad();
  } else {
    // Caso C: sesion de otro usuario (MISMATCH)
    // mismatchCurrentLabel lo llenara el servidor si expone el campo; si no, queda "-"
    const currentUser = data._current_user || "";
    showMismatch(assigned, currentUser);
  }
}

// Funcion de reintento manual (solo para errores no-401)
function reloadSession() {
  sessionLoaded = false;   // permite una nueva carga tras reintento manual
  loadSession();
}

// ---------------------------------------------------------------------------
// Listener: Limpiar lienzo
// ---------------------------------------------------------------------------
if (clearBtn) {
  clearBtn.addEventListener("click", () => {
    if (pad) pad.clear();
  });
}

// ---------------------------------------------------------------------------
// Listener: Confirmar y enviar firma
// ---------------------------------------------------------------------------
if (confirmBtn) {
  confirmBtn.addEventListener("click", async () => {
    if (!pad || pad.isEmpty()) {
      showAlert(alerts, "warning", "Por favor dibuje su firma antes de confirmar.");
      return;
    }

    const pngDataUrl = pad.toDataURL("image/png");
    setBusy(confirmBtn, true, "Enviando firma...");

    try {
      await apiFetch("/api/firma/movil/confirmar", {
        method: "POST",
        body: JSON.stringify({
          token:        currentToken,
          png_data_url: pngDataUrl,
        }),
      });

      hideAllPanels();
      if (successContent) successContent.hidden = false;
      window.setTimeout(() => window.close(), 1500);
    } catch (err) {
      showUiError(alerts, err, { onRetry: reloadSession });
      setBusy(confirmBtn, false, "Confirmar y Enviar Firma");
    }
  });
}

// ---------------------------------------------------------------------------
// Listener: Boton de login en panel MISMATCH
// ---------------------------------------------------------------------------
if (loginAsAssignedBtn) {
  loginAsAssignedBtn.addEventListener("click", () => {
    window.location.href = buildLoginUrl();
  });
}

// ---------------------------------------------------------------------------
// Listener: Boton de login en panel NO_SESSION
// ---------------------------------------------------------------------------
if (loginBtn) {
  loginBtn.addEventListener("click", () => {
    window.location.href = buildLoginUrl();
  });
}

// ---------------------------------------------------------------------------
// Arranque — una sola llamada inicial; errores inesperados de carga capturados
// ---------------------------------------------------------------------------
loadSession().catch((unexpectedErr) => {
  // Solo llega aqui si loadSession() lanza sin capturar (no deberia ocurrir).
  // El 401 ya es capturado y manejado dentro de loadSession().
  if (unexpectedErr instanceof ApiError && unexpectedErr.status === 401) {
    // Doble guardia: si por alguna razon el 401 llego aqui, no mostrar error generico.
    showNoSession();
    return;
  }
  hideAllPanels();
  showUiError(alerts, unexpectedErr, { onRetry: reloadSession });
});