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
let viewer = null;
let pdfObjectUrl = null;
let authRetryAction = null;

function setUserHeader(userId) {
  const cleanUser = (userId || "").trim();
  userInput.value = cleanUser || "No autenticado";
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

    document.querySelector("#documentName").textContent = detail.document_name;
    document.querySelector("#requesterName").textContent = detail.requester_name;
    document.querySelector("#requestedAt").textContent = new Date(detail.requested_at).toLocaleString();
    document.querySelector("#managerName").textContent = detail.manager_name;
    document.querySelector("#managerRole").textContent = detail.manager_role;

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
    document.querySelector("#zoomSelect").addEventListener("change", (event) => {
      viewer.setZoom(Number(event.target.value));
    }, { once: true });

    authorizeButton.disabled = false;
    rejectButton.disabled = false;
  } catch (error) {
    if (error?.status === 401) {
      await requestAuthentication({
        suggestedUser: (await getCurrentSession())?.user_id || "",
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

async function init() {
  if (!rawToken || rawToken.length < 32) {
    throw new Error("El enlace de autorizacion no es valido.");
  }

  const requestedUser = initialParams.get("user");
  const session = await getCurrentSession();
  if (requestedUser) {
    await setSessionUser(requestedUser);
    setUserHeader(requestedUser);
  } else if (session?.user_id) {
    setUserHeader(session.user_id);
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
    await apiFetch("/api/firma/gerencia/autorizar", {
      method: "POST",
      body: JSON.stringify({ token: rawToken })
    });
    showCompleted("Documento autorizado por Gerencia.");
  } catch (error) {
    rejectButton.disabled = false;
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
  authorizeButton.disabled = true;
  rejectButton.disabled = true;
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
