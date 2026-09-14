import { apiFetch, escapeText, isUuid, setBusy, showAlert, setSessionUser } from "./api.js";
import { PdfViewer } from "./pdf-viewer.js";
import { buildDraftPayload, normalizeUser } from "./preparation-state.js";
import { isPreparationEditableStatus } from "./workflow-state.js";

const params = new URLSearchParams(window.location.search);
const nodeId = params.get("nodeId") || "";
const alertBox = document.querySelector("#alerts");
const docName = document.querySelector("#docName");
const docVersion = document.querySelector("#docVersion");
const docStatus = document.querySelector("#docStatus");
const pageStatus = document.querySelector("#pageStatus");
const userInput = document.querySelector("[data-user-input]");
const signerInput = document.querySelector("#signerUser");
const orderInput = document.querySelector("#signerOrder");
const typeInput = document.querySelector("#signatureType");
const zoomInput = document.querySelector("#zoomSelect");
const positionInfo = document.querySelector("#positionInfo");
const saveButton = document.querySelector("#saveDraft");
const sendButton = document.querySelector("#sendSignature");
const saveSendButton = document.querySelector("#saveAndSendSignature");
const deleteButton = document.querySelector("#deletePosition");
const backButton = document.querySelector("#backButton");
const dirtyStatus = document.querySelector("#dirtyStatus");

let preparation = null;
let viewer = null;
let preparationEditable = true;

init().catch((error) => showAlert(alertBox, "danger", error.message));

async function init() {
  if (!isUuid(nodeId)) {
    throw new Error("nodeId invalido. Abra la URL con /documentos/preparar?nodeId=<uuid>");
  }
  const user = params.get("user") || "preparador";
  if (userInput) {
    userInput.value = user;
    userInput.addEventListener("change", async () => {
      const newUser = userInput.value.trim();
      if (newUser) {
        await setSessionUser(newUser);
      }
    });
  }
  await setSessionUser(user);
  preparation = await apiFetch(`/api/firma/preparacion/${nodeId}`);
  preparationEditable = isPreparationEditableStatus(preparation.status);
  viewer = new PdfViewer({
    container: document.querySelector("#pdfContainer"),
    thumbs: document.querySelector("#thumbs"),
    status: pageStatus,
    userProvider: getCurrentUser,
    editable: preparationEditable,
    onSelectionChange: renderPositionInfo,
    onDirtyChange: renderDirtyState,
  });

  bindControls();
  await loadPreparation(preparation);
}

function bindControls() {
  typeInput.addEventListener("change", () => {
    if (!preparationEditable) return;
    viewer.setMode(typeInput.value);
    viewer.updateSelectedProperties({ tipfir: typeInput.value });
  });
  signerInput.addEventListener("input", () => {
    if (!preparationEditable) return;
    const signer = normalizeUser(signerInput.value);
    viewer.setSigner(signer);
    viewer.updateSelectedProperties({ usrid: signer });
  });
  orderInput.addEventListener("input", () => {
    if (!preparationEditable) return;
    viewer.setOrder(orderInput.value);
    viewer.updateSelectedProperties({ orden: Number(orderInput.value || 1) });
  });
  zoomInput.addEventListener("change", async () => {
    pageStatus.textContent = "Actualizando zoom";
    await viewer.setZoom(Number(zoomInput.value));
    pageStatus.textContent = "";
  });
  deleteButton.addEventListener("click", () => {
    if (!preparationEditable) return;
    viewer.deleteSelected();
  });
  saveButton.addEventListener("click", saveDraft);
  sendButton.addEventListener("click", sendToSignature);
  saveSendButton.addEventListener("click", saveAndSendToSignature);
  backButton.addEventListener("click", () => {
    if (preparationEditable && viewer.isDirty()) {
      const confirmed = window.confirm("Hay cambios sin guardar. Desea salir?");
      if (!confirmed) return;
    }
    window.location.href = "/pendientes";
  });
}

async function loadPreparation(initialPreparation = null) {
  pageStatus.textContent = "Consultando proceso";
  preparation = initialPreparation || (await apiFetch(`/api/firma/preparacion/${nodeId}`));
  preparationEditable = isPreparationEditableStatus(preparation.status);
  docName.textContent = preparation.document_name;
  docVersion.textContent = `Version ${preparation.version}`;
  docStatus.textContent = preparation.status;

  const participant = preparation.participants[0];
  signerInput.value = participant?.usrid || "firmante";
  orderInput.value = participant?.orden || 1;
  viewer.setSigner(normalizeUser(signerInput.value));
  viewer.setOrder(orderInput.value);
  viewer.setMode(typeInput.value);
  applyPreparationMode();

  const positions = preparation.positions.map((position, index) => ({
    ...position,
    id: `saved-${index}`,
    saved: true,
    dirty: false,
  }));
  const pdfUrl = `/api/alfresco/nodes/${preparation.node_id}/content`;
  await viewer.load(pdfUrl, preparation.pages, positions);
  if (preparation.active_firid) {
    showAlert(alertBox, "info", `Preparacion enviada. Firma activa: ${preparation.active_firid}`);
  }
  renderDirtyState(false);
}

function applyPreparationMode() {
  const readonly = !preparationEditable;
  typeInput.disabled = readonly;
  signerInput.disabled = readonly;
  orderInput.disabled = readonly;
  deleteButton.hidden = readonly;
  saveButton.hidden = readonly;
  sendButton.hidden = readonly;
  saveSendButton.hidden = readonly;
  dirtyStatus.textContent = readonly ? "Solo lectura" : "Sin cambios pendientes";
  dirtyStatus.classList.remove("dirty");
  if (readonly) {
    renderPositionInfo(null);
  }
}

function renderPositionInfo(position) {
  if (!position) {
    positionInfo.innerHTML = preparationEditable
      ? "<p>Seleccione o dibuje un rectangulo.</p>"
      : "<p>Seleccione una posicion para consultar su informacion.</p>";
    return;
  }
  typeInput.value = position.tipfir;
  signerInput.value = position.usrid;
  orderInput.value = position.orden;
  if (preparationEditable) {
    viewer.setMode(position.tipfir);
    viewer.setSigner(position.usrid);
    viewer.setOrder(position.orden);
  }
  positionInfo.innerHTML = `
    <dl>
      <dt>Pagina</dt><dd>${escapeText(position.pagina)}</dd>
      <dt>Tipo</dt><dd>${escapeText(position.tipfir)}</dd>
      <dt>Firmante</dt><dd>${escapeText(position.usrid)}</dd>
      <dt>Orden</dt><dd>${escapeText(position.orden)}</dd>
      <dt>X / Y</dt><dd>${escapeText(position.posx)} / ${escapeText(position.posy)}</dd>
      <dt>Ancho</dt><dd>${escapeText(position.ancho)}</dd>
      <dt>Alto</dt><dd>${escapeText(position.alto)}</dd>
    </dl>
  `;
}

function renderDirtyState(isDirty) {
  if (!preparationEditable) {
    dirtyStatus.textContent = "Solo lectura";
    dirtyStatus.classList.remove("dirty");
    saveButton.disabled = true;
    sendButton.disabled = true;
    saveSendButton.disabled = true;
    return;
  }
  dirtyStatus.textContent = isDirty ? "Cambios sin guardar" : "Sin cambios pendientes";
  dirtyStatus.classList.toggle("dirty", Boolean(isDirty));
  saveButton.disabled = false;
  sendButton.disabled = Boolean(isDirty);
  saveSendButton.disabled = Boolean(isDirty);
}

function buildPayloadFromViewer() {
  return buildDraftPayload(viewer.getPositions());
}

async function saveDraft() {
  if (!preparationEditable) {
    showAlert(alertBox, "info", "El proceso ya no es editable.");
    return;
  }
  let payload;
  try {
    payload = buildPayloadFromViewer();
    setBusy(saveButton, true, "Guardando");
    preparation = await apiFetch(`/api/firma/preparacion/${preparation.docid}/borrador`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showAlert(alertBox, "success", "Borrador guardado en PostgreSQL.");
    await viewer.load(`/api/alfresco/nodes/${preparation.node_id}/content`, preparation.pages, preparation.positions);
    viewer.markClean();
  } catch (error) {
    showAlert(alertBox, error.status === 409 ? "info" : "danger", error.message);
  } finally {
    setBusy(saveButton, false);
  }
}

async function sendToSignature() {
  if (!preparationEditable) {
    showAlert(alertBox, "info", "El proceso ya no es editable.");
    return;
  }
  if (viewer.isDirty()) {
    showAlert(alertBox, "danger", "Guarde los cambios antes de enviar a firma.");
    return;
  }
  try {
    setBusy(sendButton, true, "Enviando");
    const result = await apiFetch(`/api/firma/preparacion/${preparation.docid}/enviar`, { method: "POST" });
    showAlert(alertBox, "success", `Enviado a firma. FirID ${result.firid}.`);
    window.location.href = `/firmas/${result.firid}?user=${encodeURIComponent(signerInput.value.trim().toLowerCase())}`;
  } catch (error) {
    showAlert(alertBox, error.status === 409 ? "info" : "danger", error.message);
  } finally {
    setBusy(sendButton, false);
  }
}

async function saveAndSendToSignature() {
  if (!preparationEditable) {
    showAlert(alertBox, "info", "El proceso ya no es editable.");
    return;
  }
  let payload;
  try {
    payload = buildPayloadFromViewer();
    setBusy(saveSendButton, true, "Guardando");
    setBusy(saveButton, true, "Guardando");
    setBusy(sendButton, true, "Enviando");
    const result = await apiFetch(`/api/firma/preparacion/${preparation.docid}/guardar-enviar`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    viewer.markClean();
    showAlert(alertBox, "success", `Guardado y enviado a firma. FirID ${result.firid}.`);
    window.location.href = `/firmas/${result.firid}?user=${encodeURIComponent(result.positions[0]?.usrid || signerInput.value.trim().toLowerCase())}`;
  } catch (error) {
    showAlert(alertBox, error.status === 409 ? "info" : "danger", error.message);
  } finally {
    setBusy(saveSendButton, false);
    setBusy(saveButton, false);
    setBusy(sendButton, false);
    renderDirtyState(viewer.isDirty());
  }
}
