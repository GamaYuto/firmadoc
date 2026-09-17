import {
  apiFetch,
  getCurrentSession,
  setSessionUser,
  showAlert,
  setBusy,
} from "./api.js";

const alerts = document.getElementById("alerts");
const authNotice = document.getElementById("authNotice");
const quickLoginBtn = document.getElementById("quickLoginBtn");
const sessionContent = document.getElementById("sessionContent");
const successContent = document.getElementById("successContent");
const docTitle = document.getElementById("docTitle");
const signerLabel = document.getElementById("signerLabel");
const canvas = document.getElementById("mobileSignatureCanvas");
const clearBtn = document.getElementById("clearBtn");
const confirmBtn = document.getElementById("confirmBtn");

let pad = null;
let currentToken = null;
let assignedSigner = null;

function getTokenFromUrl() {
  const match = window.location.pathname.match(/\/firma-movil\/([^/]+)/);
  return match ? match[1] : null;
}

function resizeCanvas() {
  if (!canvas) return;
  const ratio = Math.max(window.devicePixelRatio || 1, 1);
  canvas.width = canvas.offsetWidth * ratio;
  canvas.height = canvas.offsetHeight * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  if (pad) {
    pad.clear();
  }
}

function initPad() {
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
    const data = await apiFetch(`/api/firma/movil/sesion/${encodeURIComponent(currentToken)}`);
    assignedSigner = data.usrid;
    docTitle.textContent = data.docnom || "Documento";
    signerLabel.textContent = `${data.usrid} (${data.tipfir})`;
    authNotice.hidden = true;
    sessionContent.hidden = false;
    initPad();
  } catch (err) {
    if (err.status === 401 || err.status === 403) {
      authNotice.hidden = false;
      sessionContent.hidden = true;
      showAlert(alerts, "warning", err.message || "Identificación requerida para esta firma.");
      // Si el error contiene el firmante asignado, pre-cargar botón
      quickLoginBtn.onclick = async () => {
        try {
          const userPrompt = prompt("Ingrese su usuario institucional:", "firmante");
          if (!userPrompt) return;
          await setSessionUser(userPrompt.trim());
          alerts.innerHTML = "";
          await loadSession();
        } catch (authErr) {
          showAlert(alerts, "danger", `Error de autenticación: ${authErr.message}`);
        }
      };
    } else {
      sessionContent.hidden = true;
      showAlert(alerts, "danger", err.message || "No fue posible cargar la sesión de firma.");
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
  } catch (err) {
    showAlert(alerts, "danger", `Error al registrar firma: ${err.message}`);
    setBusy(confirmBtn, false, "Confirmar y Enviar Firma");
  }
});

loadSession();
