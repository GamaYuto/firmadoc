import { apiFetch, apiFetchBinary, getCurrentSession, setBusy, setSessionUser } from "./api.js?v=16.0";
import { PdfViewer } from "./pdf-viewer.js";
import { showUiError } from "./ui-feedback.js?v=12.1";
import { consumeApprovalToken } from "./manager-approval-token.js";
import { PdfReadAloud, ReadAloudState } from "./pdf-read-aloud.js?v=17.0";

const initialParams = new URLSearchParams(window.location.search);
const rawToken = consumeApprovalToken(window.location, window.history);

const alertBox = document.querySelector("#alerts");
const userInput = document.querySelector("[data-user-input]");
const authorizeButton = document.querySelector("#authorizeButton");
const rejectButton = document.querySelector("#rejectButton");
const authenticationModal = document.querySelector("#authenticationModal");
const authenticationForm = document.querySelector("#authenticationForm");
const authenticationUser = document.querySelector("#authenticationUser");
const authenticationPassword = document.querySelector("#authenticationPassword");
const authenticationError = document.querySelector("#authenticationError");
const authenticationSubmit = document.querySelector("#authenticationSubmit");
const zoomSelect = document.querySelector("#zoomSelect");
const expandDocButton = document.querySelector("#expandDocButton");
const viewerColumn = document.querySelector(".viewer-column");

const readAloudBar = document.querySelector("#readAloudBar");
const readAloudPlayBtn = document.querySelector("#readAloudPlayBtn");
const readAloudPlayText = document.querySelector("#readAloudPlayText");
const readAloudPauseBtn = document.querySelector("#readAloudPauseBtn");
const readAloudStopBtn = document.querySelector("#readAloudStopBtn");
const readAloudStatus = document.querySelector("#readAloudStatus");
const readAloudPageIndicator = document.querySelector("#readAloudPageIndicator");
const readAloudRateSelect = document.querySelector("#readAloudRateSelect");

let viewer = null;
let pdfReader = null;
let readerListenersAttached = false;
let pdfObjectUrl = null;
let authRetryAction = null;
let currentRequestDetail = null;
let isViewerExpanded = false;

function isMobileLayout() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function setUserHeader(userId) {
  const cleanUser = String(userId || "").trim();
  if (userInput) userInput.value = cleanUser;
  if (authenticationUser) authenticationUser.value = cleanUser;
}

function disableApprovalActions() {
  authorizeButton.disabled = true;
  rejectButton.disabled = true;
}

function stopAndCleanReader() {
  if (pdfReader) {
    try {
      pdfReader.stop();
    } catch (_e) {}
  }
  if (typeof window !== "undefined" && window.speechSynthesis) {
    try {
      window.speechSynthesis.cancel();
    } catch (_e) {}
  }
}

function updateReaderUi(status) {
  if (!readAloudStatus) return;

  const { state, page, totalPages, message } = status;

  if (readAloudPageIndicator) {
    if (totalPages > 0) {
      readAloudPageIndicator.textContent = `Página ${page || 1} de ${totalPages}`;
    } else {
      readAloudPageIndicator.textContent = "Página - de -";
    }
  }

  switch (state) {
    case ReadAloudState.IDLE:
      readAloudStatus.textContent = "Listo para leer";
      readAloudStatus.className = "read-aloud-status-badge badge-ready";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = true;
      if (readAloudPlayText) readAloudPlayText.textContent = "Leer PDF";
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = true;
      break;

    case ReadAloudState.READY:
      readAloudStatus.textContent = "Listo para leer";
      readAloudStatus.className = "read-aloud-status-badge badge-ready";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = false;
      if (readAloudPlayText) readAloudPlayText.textContent = "Leer PDF";
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = true;
      break;

    case ReadAloudState.PREPARING:
      readAloudStatus.textContent = "Preparando texto";
      readAloudStatus.className = "read-aloud-status-badge badge-active";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = true;
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = false;
      if (readAloudStopBtn) readAloudStopBtn.disabled = false;
      break;

    case ReadAloudState.READING:
      readAloudStatus.textContent = `Leyendo página ${page} de ${totalPages}`;
      readAloudStatus.className = "read-aloud-status-badge badge-active";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = true;
      if (readAloudPlayText) readAloudPlayText.textContent = "Leer PDF";
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = false;
      if (readAloudStopBtn) readAloudStopBtn.disabled = false;
      break;

    case ReadAloudState.PAUSED:
      readAloudStatus.textContent = "Pausado";
      readAloudStatus.className = "read-aloud-status-badge badge-paused";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = false;
      if (readAloudPlayText) readAloudPlayText.textContent = "Reanudar";
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = false;
      break;

    case ReadAloudState.COMPLETED:
      readAloudStatus.textContent = "Lectura finalizada";
      readAloudStatus.className = "read-aloud-status-badge badge-completed";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = false;
      if (readAloudPlayText) readAloudPlayText.textContent = "Leer PDF";
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = true;
      break;

    case ReadAloudState.SCANNED_UNAVAILABLE:
      readAloudStatus.textContent =
        message || "Este documento no contiene texto seleccionable. La lectura en voz alta no está disponible para archivos escaneados.";
      readAloudStatus.className = "read-aloud-status-badge badge-warning";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = true;
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = true;
      break;

    case ReadAloudState.UNAVAILABLE:
      readAloudStatus.textContent = "Lectura no disponible";
      readAloudStatus.className = "read-aloud-status-badge badge-unavailable";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = true;
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = true;
      if (message) readAloudStatus.title = message;
      break;

    case ReadAloudState.ERROR:
      readAloudStatus.textContent = "Error en lectura";
      readAloudStatus.className = "read-aloud-status-badge badge-error";
      if (readAloudPlayBtn) readAloudPlayBtn.disabled = false;
      if (readAloudPlayText) readAloudPlayText.textContent = "Reintentar";
      if (readAloudPauseBtn) readAloudPauseBtn.disabled = true;
      if (readAloudStopBtn) readAloudStopBtn.disabled = true;
      break;

    default:
      break;
  }
}

function initReaderControls() {
  if (readerListenersAttached) return;
  readerListenersAttached = true;

  readAloudPlayBtn?.addEventListener("click", async () => {
    if (!pdfReader) return;
    if (pdfReader.state === ReadAloudState.PAUSED) {
      await pdfReader.resume();
    } else {
      await pdfReader.start();
    }
  });

  readAloudPauseBtn?.addEventListener("click", () => {
    if (!pdfReader) return;
    pdfReader.pause();
  });

  readAloudStopBtn?.addEventListener("click", () => {
    if (!pdfReader) return;
    pdfReader.stop();
  });

  readAloudRateSelect?.addEventListener("change", (e) => {
    if (!pdfReader) return;
    pdfReader.setRate(Number(e.target.value));
  });
}

async function toggleExpandedViewer() {
  if (!viewerColumn) return;
  isViewerExpanded = !isViewerExpanded;
  viewerColumn.classList.toggle("viewer-expanded", isViewerExpanded);
  document.body.classList.toggle("viewer-expanded-active", isViewerExpanded);

  const btnText = expandDocButton?.querySelector(".expand-btn-text");
  if (isViewerExpanded) {
    if (btnText) btnText.textContent = "Volver";
    expandDocButton?.setAttribute("aria-label", "Volver a la solicitud");
    if (viewer) {
      await viewer.fitWidth({ maxZoom: 1.5, horizontalPadding: 16 });
    }
  } else {
    if (btnText) btnText.textContent = "Ampliar documento";
    expandDocButton?.setAttribute("aria-label", "Ampliar documento a pantalla completa");
    if (viewer) {
      if (zoomSelect?.value === "fit" || isMobileLayout()) {
        await viewer.fitWidth({ maxZoom: 1, horizontalPadding: 24 });
      } else if (zoomSelect) {
        await viewer.setZoom(Number(zoomSelect.value));
      }
    }
  }
}

if (expandDocButton) {
  expandDocButton.addEventListener("click", () => {
    toggleExpandedViewer().catch(console.error);
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && isViewerExpanded) {
    toggleExpandedViewer().catch(console.error);
  }
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(async () => {
    if (!viewer) return;
    if (isViewerExpanded) {
      await viewer.fitWidth({ maxZoom: 1.5, horizontalPadding: 16 });
    } else if (zoomSelect?.value === "fit" || isMobileLayout()) {
      await viewer.fitWidth({ maxZoom: 1, horizontalPadding: 24 });
    }
  }, 150);
});

async function loadManagerRequest() {
  try {
    alertBox.innerHTML = "";
    disableApprovalActions();

    const detail = await apiFetch("/api/firma/gerencia/solicitud", {
      headers: { "X-FirmaDoc-Approval": rawToken }
    });
    currentRequestDetail = detail;

    document.querySelector("#documentName").textContent = detail.document_name;
    document.querySelector("#requesterName").textContent = detail.requester_name;
    document.querySelector("#requestedAt").textContent = new Date(detail.requested_at).toLocaleString();
    document.querySelector("#managerName").textContent = detail.manager_name;
    document.querySelector("#managerRole").textContent = detail.manager_role;
    document.querySelector("[data-mobile-document]").textContent = detail.document_name;
    document.querySelector("[data-mobile-requester]").textContent = detail.requester_name;

    const pdfBlob = await apiFetchBinary("/api/firma/gerencia/documento", {
      headers: { "X-FirmaDoc-Approval": rawToken }
    });

    if (pdfObjectUrl) {
      URL.revokeObjectURL(pdfObjectUrl);
    }
    pdfObjectUrl = URL.createObjectURL(pdfBlob);

    viewer = new PdfViewer({
      container: document.querySelector("#pdfContainer"),
      thumbs: null,
      status: document.querySelector("#pageStatus"),
      editable: false
    });
    await viewer.load(pdfObjectUrl, [], []);

    initReaderControls();
    if (!pdfReader) {
      pdfReader = new PdfReadAloud({
        pdfDoc: viewer.pdfDoc,
        preferredLang: "es-CO",
        rate: Number(readAloudRateSelect?.value) || 1.0,
        onStateChange: updateReaderUi,
      });
    } else {
      pdfReader.setDocument(viewer.pdfDoc);
    }

    if (isMobileLayout()) {
      await viewer.fitWidth({ maxZoom: 1, horizontalPadding: 24 });
    }
    zoomSelect.addEventListener("change", async (event) => {
      if (event.target.value === "fit") {
        await viewer.fitWidth({ maxZoom: 1.25, horizontalPadding: isMobileLayout() ? 16 : 24 });
      } else {
        await viewer.setZoom(Number(event.target.value));
      }
    });

    authorizeButton.disabled = false;
    rejectButton.disabled = false;
  } catch (error) {
    stopAndCleanReader();
    if (error?.status === 401) {
      await requestAuthentication({
        suggestedUser: (await getCurrentSession())?.user_id || initialParams.get("user") || "",
        retryAction: () => loadManagerRequest(),
      });
      return;
    }

    if (error?.status === 403) {
      alertBox.innerHTML = "";
      const warning = document.createElement("div");
      warning.className = "alert alert-warning";
      warning.setAttribute("role", "status");
      warning.innerHTML = `
        <strong>Usuario no autorizado</strong>
        <p class="mb-2">La solicitud está asignada a otro usuario de Gerencia.</p>
      `;

      const changeUserButton = document.createElement("button");
      changeUserButton.type = "button";
      changeUserButton.className = "btn btn-sm btn-outline-secondary";
      changeUserButton.textContent = "Cambiar usuario";
      changeUserButton.addEventListener("click", () => {
        requestAuthentication({
          suggestedUser: (document.querySelector("[data-user-input]")?.value || "").trim(),
          retryAction: () => loadManagerRequest(),
        });
      });
      warning.appendChild(changeUserButton);
      alertBox.appendChild(warning);
      return;
    }

    showUiError(alertBox, error, { onRetry: loadManagerRequest });
  }
}

function hideApprovalActions() {
  stopAndCleanReader();
  authorizeButton.hidden = true;
  rejectButton.hidden = true;
  document.querySelector(".manager-approval-actions")?.setAttribute("hidden", "");
}

async function loadSignedPreview(firid) {
  stopAndCleanReader();
  const pdfBlob = await apiFetchBinary(`/api/firma/firmas/${firid}/resultado/pdf`);
  if (pdfObjectUrl) {
    URL.revokeObjectURL(pdfObjectUrl);
  }
  pdfObjectUrl = URL.createObjectURL(pdfBlob);
  if (!viewer) {
    viewer = new PdfViewer({
      container: document.querySelector("#pdfContainer"),
      thumbs: null,
      status: document.querySelector("#pageStatus"),
      editable: false
    });
  }
  await viewer.load(pdfObjectUrl, [], []);
  if (isMobileLayout()) {
    await viewer.fitWidth({ maxZoom: 1, horizontalPadding: 24 });
  }
}

async function showAuthorizedResult(result) {
  hideApprovalActions();
  alertBox.innerHTML = "";
  const notice = document.createElement("div");
  notice.className = "alert alert-success";
  notice.setAttribute("role", "status");
  notice.textContent = result?.message || "Documento autorizado por Gerencia.";
  alertBox.appendChild(notice);
  const firid = result?.firid || currentRequestDetail?.firid;
  if (firid) {
    try {
      await loadSignedPreview(firid);
    } catch (error) {
      showUiError(alertBox, error, { onRetry: () => loadSignedPreview(firid) });
    }
  }
}

async function init() {
  if (!rawToken || rawToken.length < 32) {
    throw new Error("El enlace de autorizacion no es valido.");
  }

  const requestedUser = initialParams.get("user");
  const session = await getCurrentSession();
  if (session?.user_id) {
    setUserHeader(session.user_id);
  } else if (requestedUser) {
    setUserHeader(requestedUser);
  } else {
    setUserHeader("No autenticado");
    await requestAuthentication({
      suggestedUser: requestedUser || "",
      retryAction: () => loadManagerRequest(),
    });
    return;
  }

  await loadManagerRequest();
}

function requestAuthentication({ suggestedUser = "", retryAction = null } = {}) {
  authRetryAction = typeof retryAction === "function" ? retryAction : null;
  authenticationUser.value = suggestedUser || "";
  if (authenticationPassword) authenticationPassword.value = "";
  authenticationError.hidden = true;
  authenticationError.textContent = "";
  authenticationModal.hidden = false;
  window.setTimeout(() => {
    if (authenticationUser.value.trim() && authenticationPassword) {
      authenticationPassword.focus();
    } else {
      authenticationUser.focus();
    }
  }, 0);

  return new Promise((resolve) => {
    const handler = async (event) => {
      event.preventDefault();
      const userId = authenticationUser.value.trim();
      const password = authenticationPassword ? authenticationPassword.value : "";
      if (!userId) {
        authenticationError.textContent = "Ingrese su usuario Alfresco.";
        authenticationError.hidden = false;
        authenticationUser.focus();
        return;
      }
      if (!password) {
        authenticationError.textContent = "Ingrese su contraseña.";
        authenticationError.hidden = false;
        if (authenticationPassword) authenticationPassword.focus();
        return;
      }

      try {
        authenticationError.hidden = true;
        setBusy(authenticationSubmit, true, "Autenticando...");
        const session = await setSessionUser(userId, password);
        setUserHeader(session?.user_id || userId);
        if (authenticationPassword) authenticationPassword.value = "";
        authenticationModal.hidden = true;
        const nextAction = authRetryAction;
        authRetryAction = null;
        resolve(session);
        if (typeof nextAction === "function") {
          await nextAction();
        }
      } catch (error) {
        if (error?.status === 401) {
          authenticationError.textContent = "Credenciales inválidas.";
        } else {
          authenticationError.textContent = error.message || "No fue posible autenticar el usuario.";
        }
        authenticationError.hidden = false;
        if (authenticationPassword) {
          authenticationPassword.value = "";
          authenticationPassword.focus();
        }
      } finally {
        setBusy(authenticationSubmit, false);
      }
    };

    authenticationForm.onsubmit = handler;
  });
}

init().catch((error) => showUiError(alertBox, error, { onRetry: init }));

authorizeButton.addEventListener("click", async () => {
  try {
    setBusy(authorizeButton, true, "Autorizando...");
    rejectButton.disabled = true;
    const result = await apiFetch("/api/firma/gerencia/autorizar", {
      method: "POST",
      body: JSON.stringify({ token: rawToken })
    });
    await showAuthorizedResult(result);
  } catch (error) {
    rejectButton.disabled = false;
    if (error?.status === 409 && currentRequestDetail?.firid) {
      await showAuthorizedResult({ firid: currentRequestDetail.firid, message: "La solicitud de Gerencia ya fue resuelta." });
      return;
    }
    showUiError(alertBox, error);
  } finally {
    setBusy(authorizeButton, false);
  }
});

rejectButton.addEventListener("click", async () => {
  if (!window.confirm("Confirma que no autoriza este documento?")) return;
  try {
    setBusy(rejectButton, true, "Registrando...");
    authorizeButton.disabled = true;
    const result = await apiFetch("/api/firma/gerencia/rechazar", {
      method: "POST",
      body: JSON.stringify({
        token: rawToken,
        reason: "No autorizado por Gerencia"
      })
    });
    showCompleted(result.message);
  } catch (error) {
    authorizeButton.disabled = false;
    showUiError(alertBox, error);
  } finally {
    setBusy(rejectButton, false);
  }
});

function showCompleted(message) {
  hideApprovalActions();
  alertBox.innerHTML = "";
  const notice = document.createElement("div");
  notice.className = "alert alert-success";
  notice.setAttribute("role", "status");
  notice.textContent = message;
  alertBox.appendChild(notice);
}

window.addEventListener("beforeunload", () => {
  stopAndCleanReader();
  if (pdfObjectUrl) URL.revokeObjectURL(pdfObjectUrl);
});

window.addEventListener("pagehide", () => {
  stopAndCleanReader();
});
