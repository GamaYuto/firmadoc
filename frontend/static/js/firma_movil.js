import {
  apiFetch,
  setSessionUser,
  showAlert,
  setBusy,
} from "./api.js?v=12.1";
import { clearFeedback, showUiError } from "./ui-feedback.js?v=12.1";

const alerts = document.getElementById("alerts");
const authNotice = document.getElementById("authNotice");
const quickLoginBtn = document.getElementById("quickLoginBtn");
const mobileUserInput = document.getElementById("mobileUserInput");
const sessionContent = document.getElementById("sessionContent");
const successContent = document.getElementById("successContent");
const docTitle = document.getElementById("docTitle");
const signerLabel = document.getElementById("signerLabel");
const canvas = document.getElementById("mobileSignatureCanvas");
const clearBtn = document.getElementById("clearBtn");
const confirmBtn = document.getElementById("confirmBtn");

let pad = null;
let currentToken = null;

function getTokenFromUrl() {
  const match = window.location.pathname.match(/\/firma-movil\/([^/]+)/);
  return match ? match[1] : null;
}

function resizeCanvas() {
  if (!canvas) return;
  const previous = pad && !pad.isEmpty() ? pad.toDataURL("image/png") : null;
  const ratio = Math.max(window.devicePixelRatio || 1, 1);
  canvas.width = canvas.offsetWidth * ratio;
  canvas.height = canvas.offsetHeight * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  if (pad && previous) {
    pad.fromDataURL(previous);
  }
}

function initPad() {
  if (pad) {
    resizeCanvas();
    return;
  }
  pad = new window.SignaturePad(canvas, {
    backgroundColor: "rgba(0, 0, 0, 0)",
    penColor: "rgb(0, 0, 0)",
  });
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

async function loadSession() {
  currentToken = getTokenFromUrl();
  if (!currentToken) {
    showAlert(alerts, "danger", "Enlace de firma móvil inválido o sin token.");
    sessionContent.hidden = true;
    return;
  }

  try {
    clearFeedback(alerts);
    const data = await apiFetch(`/api/firma/movil/sesion/${encodeURIComponent(currentToken)}`);
    docTitle.textContent = data.docnom || "Documento";
    signerLabel.textContent = `${data.usrid} (${data.tipfir})`;
    authNotice.hidden = true;
    sessionContent.hidden = false;
    initPad();
  } catch (err) {
    if (err.status === 401 || err.status === 403) {
      authNotice.hidden = false;
      sessionContent.hidden = true;
      showUiError(alerts, err);
    } else {
      sessionContent.hidden = true;
      showUiError(alerts, err, { onRetry: loadSession });
    }
  }
}

clearBtn.addEventListener("click", () => {
  if (pad) {
    pad.clear();
  }
});

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
        token: currentToken,
        png_data_url: pngDataUrl,
      }),
    });

    sessionContent.hidden = true;
    successContent.hidden = false;

    window.setTimeout(() => {
      window.close();
    }, 1500);
  } catch (err) {
    showUiError(alerts, err, { onRetry: loadSession });
    setBusy(confirmBtn, false, "Confirmar y Enviar Firma");
  }
});

quickLoginBtn.addEventListener("click", async () => {
  const user = mobileUserInput.value.trim();
  if (!user) {
    mobileUserInput.focus();
    showAlert(alerts, "warning", "Ingrese su usuario institucional.");
    return;
  }
  try {
    setBusy(quickLoginBtn, true, "Validando");
    await setSessionUser(user);
    await loadSession();
  } catch (error) {
    showUiError(alerts, error);
  } finally {
    setBusy(quickLoginBtn, false);
  }
});

mobileUserInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") quickLoginBtn.click();
});

loadSession().catch((error) => showUiError(alerts, error, { onRetry: loadSession }));
