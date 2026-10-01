import { apiFetch, apiFetchBinary, getCurrentSession, setBusy, setSessionUser } from "./api.js?v=12.1";
import { PdfViewer } from "./pdf-viewer.js";
import { showUiError } from "./ui-feedback.js?v=12.1";
import { consumeApprovalToken } from "./manager-approval-token.js";

const initialParams = new URLSearchParams(window.location.search);
const rawToken = consumeApprovalToken(window.location, window.history);

const alertBox = document.querySelector("#alerts");
const userInput = document.querySelector("[data-user-input]");
const authorizeButton = document.querySelector("#authorizeButton");
const rejectButton = document.querySelector("#rejectButton");
const authenticationModal = document.querySelector("#authenticationModal");
const authenticationForm = document.querySelector("#authenticationForm");
const authenticationUser = document.querySelector("#authenticationUser");
const authenticationError = document.querySelector("#authenticationError");
const authenticationSubmit = document.querySelector("#authenticationSubmit");
const zoomSelect = document.querySelector("#zoomSelect");
let viewer = null;
let pdfObjectUrl = null;
let authRetryAction = null;
let currentRequestDetail = null;

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
    if (isMobileLayout()) {
      await viewer.fitWidth({ maxZoom: 1, horizontalPadding: 24 });
    }
    zoomSelect.addEventListener("change", (event) => {
      viewer.setZoom(Number(event.target.value));
    });

    authorizeButton.disabled = false;
    rejectButton.disabled = false;
  } catch (error) {
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
  authorizeButton.hidden = true;
  rejectButton.hidden = true;
  document.querySelector(".manager-approval-actions")?.setAttribute("hidden", "");
}

async function loadSignedPreview(firid) {
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
  authenticationError.hidden = true;
  authenticationError.textContent = "";
  authenticationModal.hidden = false;
  window.setTimeout(() => authenticationUser.focus(), 0);

  return new Promise((resolve) => {
    const handler = async (event) => {
      event.preventDefault();
      const userId = authenticationUser.value.trim();
      if (!userId) {
        authenticationError.textContent = "Ingrese su usuario Alfresco.";
        authenticationError.hidden = false;
        authenticationUser.focus();
        return;
      }

      try {
        authenticationError.hidden = true;
        setBusy(authenticationSubmit, true, "Autenticando...");
        const session = await setSessionUser(userId);
        setUserHeader(session?.user_id || userId);
        authenticationModal.hidden = true;
        const nextAction = authRetryAction;
        authRetryAction = null;
        resolve(session);
        if (typeof nextAction === "function") {
          await nextAction();
        }
      } catch (error) {
        authenticationError.textContent = error.message || "No fue posible autenticar el usuario.";
        authenticationError.hidden = false;
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
  if (pdfObjectUrl) URL.revokeObjectURL(pdfObjectUrl);
});
