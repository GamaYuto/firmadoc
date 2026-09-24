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
let viewer = null;
let pdfObjectUrl = null;

init().catch((error) => showUiError(alertBox, error, { onRetry: init }));

async function init() {
  if (!rawToken || rawToken.length < 32) {
    throw new Error("El enlace de autorizaci?n no es v?lido.");
  }

  const requestedUser = initialParams.get("user");
  if (requestedUser) {
    await setSessionUser(requestedUser);
  }
  const session = await getCurrentSession();
  if (!session) {
    throw new Error("Debe autenticarse para revisar esta solicitud.");
  }
  userInput.value = session.user_id;
  userInput.addEventListener("change", async () => {
    await setSessionUser(userInput.value.trim());
    window.location.reload();
  });

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
  });
  authorizeButton.disabled = false;
  rejectButton.disabled = false;
}

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
  if (!window.confirm("?Confirma que no autoriza este documento?")) return;
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
