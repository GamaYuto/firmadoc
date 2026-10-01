function isActiveProcessConflict(error) {
  if (error?.status !== 409) return false;
  if (error?.code === "ACTIVE_PROCESS" || error?.detail?.code === "ACTIVE_PROCESS") return true;
  const msg = String(error?.detail?.message || error?.detail || error?.message || "").toLowerCase();
  return msg.includes("proceso activo");
}

import { apiFetch, apiFetchBinary, escapeText, showAlert, setBusy } from "./api.js?v=12.1";
import { PdfViewer } from "./pdf-viewer.js";
import { clearFeedback, showUiError } from "./ui-feedback.js?v=12.1";
import { buildSignatureRedirectUrl } from "./signature-navigation.js";

const params = new URLSearchParams(window.location.search);
const nodeId = params.get("nodeId");
const returnUrl = params.get("returnUrl");

const alertBox = document.querySelector("#alerts");
const docName = document.querySelector("#docName");
const docVersion = document.querySelector("#docVersion");
const pageStatus = document.querySelector("#pageStatus");

const signerTypeRadios = document.querySelectorAll('input[name="signerType"]');
const otherUserField = document.querySelector("#otherUserField");
const signerUser = document.querySelector("#signerUser");
const signerUserSearch = document.querySelector("#signerUserSearch");
const userSearchResults = document.querySelector("#userSearchResults");
const selectedUserDisplay = document.querySelector("#selectedUserDisplay");
const confirmPositionBtn = document.querySelector("#confirmPosition");
const activeProcessModal = document.querySelector("#activeProcessModal");
const activeProcessStatus = document.querySelector("#activeProcessStatus");
const activeProcessDescription = document.querySelector("#activeProcessDescription");
const closeActiveProcessModalBtn = document.querySelector("#closeActiveProcessModal");
const continueActiveProcessBtn = document.querySelector("#continueActiveProcess");
const replaceActiveProcessBtn = document.querySelector("#replaceActiveProcess");
const dismissActiveProcessBtn = document.querySelector("#dismissActiveProcess");
const managerRequestModal = document.querySelector("#managerRequestModal");
const managerApprovalLink = document.querySelector("#managerApprovalLink");
const managerRequestStatus = document.querySelector("#managerRequestStatus");
const closeManagerRequestModalBtn = document.querySelector("#closeManagerRequestModal");
const closeManagerRequestActionBtn = document.querySelector("#closeManagerRequestAction");
const copyManagerApprovalLinkBtn = document.querySelector("#copyManagerApprovalLink");

let viewer = null;
let currentPosition = null;
let searchTimeout = null;
let searchController = null;
let activeProcess = null;
let pendingStartPayload = null;
let pendingStartFlow = null;
let cancellationConfirmPending = false;
let managerStatusTimer = null;
let managerRequestFirid = null;
let managerResultObjectUrl = null;

init().catch((error) => showUiError(alertBox, error, { onRetry: () => window.location.reload() }));

async function init() {
  if (!nodeId) throw new Error("Se requiere nodeId en la URL");

  viewer = new PdfViewer({
    container: document.querySelector("#pdfContainer"),
    thumbs: document.querySelector("#thumbs"),
    status: pageStatus,
    editable: true,
    onSelectionChange: (pos) => {
      currentPosition = pos;
      confirmPositionBtn.disabled = !pos;
      updateWorkflowSteps();
    }
  });
  
  viewer.setSigner("yo");

  bindControls();
  await loadDocumentInfo();
}

function bindControls() {
  const zoomInput = document.querySelector("#zoomSelect");
  if (zoomInput) {
    zoomInput.addEventListener("change", () => viewer.setZoom(Number(zoomInput.value)));
  }

  signerTypeRadios.forEach(radio => {
    radio.addEventListener("change", (e) => {
      if (e.target.value === "otro") {
        otherUserField.hidden = false;
      } else {
        otherUserField.hidden = true;

        signerUser.value = "";
        signerUserSearch.value = "";
        userSearchResults.innerHTML = "";
        selectedUserDisplay.textContent = "";
        selectedUserDisplay.hidden = true;
        signerUserSearch.setAttribute("aria-expanded", "false");
      }
      if (currentPosition) {
        viewer.clearPositions();
      }
      if (e.target.value === "gerencia") {
        viewer.setMode("INTERNA");
        viewer.setSigner("gerencia");
      } else {
        viewer.setMode("MANUSCRITA");
        viewer.setSigner(e.target.value === "otro" ? "" : "yo");
      }
      updateWorkflowSteps();
    });
  });

  signerUserSearch.addEventListener("input", (e) => {
    clearTimeout(searchTimeout);

    // Cualquier edición manual invalida la selección anterior.
    signerUser.value = "";
    viewer.setSigner("");
    selectedUserDisplay.textContent = "";
    selectedUserDisplay.hidden = true;
    updateWorkflowSteps();

    const query = e.target.value.trim();

    if (query.length < 2) {
      searchController?.abort();
      userSearchResults.innerHTML = "";
      signerUserSearch.setAttribute("aria-expanded", "false");
      return;
    }

    searchTimeout = setTimeout(() => performUserSearch(query), 300);
  });

  document.querySelector("#deletePosition")?.addEventListener("click", () => {
    viewer.clearPositions();
  });

  confirmPositionBtn.addEventListener("click", confirmPreparation);
  closeActiveProcessModalBtn.addEventListener("click", closeActiveProcessModal);
  continueActiveProcessBtn.addEventListener("click", continueActiveProcess);
  replaceActiveProcessBtn.addEventListener("click", replaceActiveProcess);
  dismissActiveProcessBtn?.addEventListener("click", closeActiveProcessModal);
  closeManagerRequestModalBtn.addEventListener("click", closeManagerRequestModal);
  closeManagerRequestActionBtn.addEventListener("click", closeManagerRequestModal);
  copyManagerApprovalLinkBtn.addEventListener("click", copyManagerApprovalLink);
}

async function performUserSearch(query) {
  searchController?.abort();
  searchController = new AbortController();
  userSearchResults.innerHTML = '<div class="list-group-item text-muted">Buscando usuarios...</div>';
  signerUserSearch.setAttribute("aria-expanded", "true");
  try {
    const results = await apiFetch(`/api/alfresco/usuarios/buscar?q=${encodeURIComponent(query)}`, {
      signal: searchController.signal,
    });
    userSearchResults.innerHTML = "";
    if (results.length === 0) {
      userSearchResults.innerHTML = '<div class="list-group-item text-muted">No se encontraron usuarios</div>';
      return;
    }
    
    results.forEach(user => {
      const a = document.createElement("a");
      a.href = "#";
      a.className = "list-group-item list-group-item-action";
      a.setAttribute("role", "option");
      a.textContent = `${user.displayName} (${user.userName})`;
      a.addEventListener("click", (e) => {
        e.preventDefault();
        signerUser.value = user.userName;
        viewer.setSigner(user.userName);
        signerUserSearch.value = "";
        userSearchResults.innerHTML = "";
        selectedUserDisplay.textContent = `Usuario seleccionado: ${user.displayName} (${user.userName})`;
        selectedUserDisplay.hidden = false;
        signerUserSearch.setAttribute("aria-expanded", "false");
        updateWorkflowSteps();
      });
      userSearchResults.appendChild(a);
    });
  } catch (error) {
    if (error?.name === "AbortError") return;
    userSearchResults.replaceChildren();
    const errorItem = document.createElement("div");
    errorItem.className = "list-group-item text-danger";
    errorItem.textContent = error.message || "No fue posible buscar usuarios.";
    userSearchResults.append(errorItem);
  }
}

function updateWorkflowSteps() {
  const steps = document.querySelectorAll(".workflow-steps li");
  const isOther = document.querySelector('input[name="signerType"]:checked')?.value === "otro";
  const signerReady = !isOther || Boolean(signerUser.value.trim());
  const activeIndex = currentPosition && signerReady ? 2 : signerReady ? 1 : 0;
  steps.forEach((step, index) => step.classList.toggle("active", index <= activeIndex));
}

async function loadDocumentInfo() {
  try {
    clearFeedback(alertBox);
    // Para simplificar y no requerir "pages" desde info, dado que el proceso todavía no existe,
    // llamamos directamente a preparacion/nodeId que sí devuelve pages.
    // OJO: El plan dice "sin crear el proceso", por lo tanto usaremos el endpoint /info.
    // Si /info no devuelve pages, el visor actual PdfViewer requiere pages para inicializar.
    // Por lo que necesitamos que /info devuelva pages.
    const info = await apiFetch(`/api/documentos/${nodeId}/info`);
    docName.textContent = info.name;
    docVersion.textContent = info.version;

    const pdfUrl = `/api/alfresco/nodes/${nodeId}/content`;
    
    // Asumimos que info.pages viene en la respuesta, de lo contrario PdfViewer fallará.
    await viewer.load(pdfUrl, info.pages || [{page: 1, width: 612, height: 792}], []);
    
  } catch (error) {
    showUiError(alertBox, error, { onRetry: loadDocumentInfo });
  }
}

async function confirmPreparation() {
  if (!currentPosition) return;

  const signerType = document.querySelector('input[name="signerType"]:checked').value;
  if (signerType === "gerencia") {
    await startManagerApproval({
      node_id: nodeId,
      page: currentPosition.pagina,
      posx: currentPosition.posx,
      posy: currentPosition.posy,
      width: currentPosition.ancho,
      height: currentPosition.alto
    });
    return;
  }

  const isOther = signerType === "otro";
  let targetUser = "yo";
  if (isOther) {
    targetUser = signerUser.value.trim();
    if (!targetUser) {
      signerUserSearch.focus();
      showAlert(alertBox, "warning", "Seleccione un usuario de los resultados de busqueda.");
      return;
    }
  }

  const payload = {
    node_id: nodeId,
    signer_user_id: targetUser,
    page: currentPosition.pagina,
    posx: currentPosition.posx,
    posy: currentPosition.posy,
    width: currentPosition.ancho,
    height: currentPosition.alto
  };

  await startSignature(payload);
}

async function startManagerApproval(payload) {
  try {
    clearFeedback(alertBox);
    setBusy(confirmPositionBtn, true, "Creando solicitud...");
    const response = await apiFetch("/api/firma/gerencia/solicitudes", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    managerRequestFirid = response.firid;
    managerApprovalLink.value = new URL(response.approval_url, window.location.origin).href;
    managerRequestStatus.textContent = "Pendiente de autorizacion por Gerencia.";
    managerRequestModal.hidden = false;
    copyManagerApprovalLinkBtn.focus();
    startManagerStatusPolling();
  } catch (error) {
    if (isActiveProcessConflict(error)) {
      activeProcess = (typeof error?.detail === "object" && error.detail !== null) ? error.detail : {};
      pendingStartPayload = payload;
      pendingStartFlow = "manager";
      activeProcessStatus.textContent = activeProcess.status || "ACTIVO";
      resetActiveProcessDialog();
      activeProcessModal.hidden = false;
      continueActiveProcessBtn.focus();
      return;
    }
    showUiError(alertBox, error, { onRetry: error.status === 409 ? null : () => startManagerApproval(payload) });
  } finally {
    setBusy(confirmPositionBtn, false);
  }
}

function startManagerStatusPolling() {
  clearInterval(managerStatusTimer);
  managerStatusTimer = window.setInterval(checkManagerStatus, 2500);
}

async function checkManagerStatus() {
  if (!managerRequestFirid) return;
  try {
    const status = await apiFetch(`/api/firma/gerencia/solicitudes/${managerRequestFirid}/estado`);
    managerRequestStatus.textContent = status.message;
    if (status.status === "AUTORIZADO") {
      clearInterval(managerStatusTimer);
      await loadAuthorizedManagerResult(managerRequestFirid);
    } else if (["RECHAZADO", "CONFLICTO", "EXPIRADO"].includes(status.status)) {
      clearInterval(managerStatusTimer);
    }
  } catch (error) {
    managerRequestStatus.textContent = error.message || "No fue posible actualizar el estado.";
  }
}

function showPdfInlineError(message, retryAction) {
  const container = document.querySelector("#pdfContainer");
  container.innerHTML = "";
  const notice = document.createElement("div");
  notice.className = "pdf-inline-error";
  notice.setAttribute("role", "alert");
  notice.innerHTML = `
    <strong>No se pudo cargar el PDF autorizado</strong>
    <p>${escapeText(message)}</p>
  `;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn btn-primary";
  retry.textContent = "Reintentar";
  retry.addEventListener("click", retryAction);
  notice.appendChild(retry);
  container.appendChild(notice);
  pageStatus.textContent = "Resultado pendiente de verificar";
}

async function publishAuthorizedResult(result) {
  const button = document.querySelector("#publishAuthorizedPdf");
  try {
    setBusy(button, true, "Publicando...");
    const publication = await apiFetch(`/api/firma/documentos/${result.docid}/publicar`, {
      method: "POST",
    });
    showAlert(alertBox, "success", publication.message || "Documento publicado en Alfresco.");
    await redirectToReturnUrlAfterPublication();
  } catch (error) {
    showUiError(alertBox, error, { onRetry: () => publishAuthorizedResult(result) });
  } finally {
    setBusy(button, false);
  }
}

async function redirectToReturnUrlAfterPublication() {
  if (!returnUrl) return false;
  try {
    const validation = await apiFetch("/api/firma/validate-return-url?url=" + encodeURIComponent(returnUrl));
    if (!validation.url) {
      showAlert(alertBox, "warning", "Documento publicado, pero la URL de regreso a Alfresco no esta autorizada.");
      return false;
    }
    pageStatus.textContent = "Publicado. Regresando a Alfresco";
    showAlert(alertBox, "success", "Documento publicado. Regresando a Alfresco...");
    window.location.href = validation.url;
    return true;
  } catch (error) {
    console.warn("No fue posible validar returnUrl despues de publicar:", error);
    showAlert(alertBox, "warning", "Documento publicado, pero no fue posible regresar automaticamente a Alfresco.");
    return false;
  }
}
function renderAuthorizedResultPanel(result) {
  document.querySelector(".task-heading .section-kicker").textContent = "Autorizado por Gerencia";
  document.querySelector(".task-heading h1").textContent = "Listo para publicar";
  document.querySelector(".workflow-steps")?.setAttribute("hidden", "");
  document.querySelector(".task-panel section")?.setAttribute("hidden", "");
  document.querySelector(".panel-actions")?.setAttribute("hidden", "");
  const publicationDisabled = result.can_publish_alfresco && !result.alfresco_write_enabled;
  const publishButton = result.can_publish_alfresco
    ? `<button id="publishAuthorizedPdf" class="btn btn-primary" type="button" ${publicationDisabled ? "disabled" : ""}>Publicar en Alfresco</button>`
    : "";
  const disabledNotice = publicationDisabled
    ? `<p class="result-message publication-disabled">Publicacion deshabilitada en este entorno.</p>`
    : "";
  document.querySelector("#positionInfo").innerHTML = `
    <p>${escapeText(result.message || "Documento autorizado por Gerencia.")}</p>
    ${disabledNotice}
    <dl>
      <dt>Estado</dt><dd>${escapeText(result.document_status || result.status)}</dd>
      <dt>Hash final</dt><dd>${escapeText(result.final_hash_short || "No disponible")}</dd>
    </dl>
    <div class="button-row result-actions">
      ${publishButton}
    </div>
  `;
  document.querySelector("#publishAuthorizedPdf")?.addEventListener("click", () => publishAuthorizedResult(result));
}

async function loadAuthorizedManagerResult(firid) {
  const retry = () => loadAuthorizedManagerResult(firid);
  try {
    clearFeedback(alertBox);
    managerRequestStatus.textContent = "Autorizado por Gerencia. Cargando PDF firmado...";
    pageStatus.textContent = "Cargando PDF firmado";
    const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`);
    const blob = await apiFetchBinary(`/api/firma/firmas/${firid}/resultado/pdf`);
    if (managerResultObjectUrl) URL.revokeObjectURL(managerResultObjectUrl);
    managerResultObjectUrl = URL.createObjectURL(blob);
    await viewer.load(managerResultObjectUrl, [], []);
    managerRequestModal.hidden = true;
    pageStatus.textContent = "PDF firmado cargado";
    renderAuthorizedResultPanel(result);
    confirmPositionBtn.disabled = true;
    showAlert(alertBox, "success", "PDF autorizado cargado. Listo para publicar.");
  } catch (error) {
    managerRequestStatus.textContent = "Autorizado por Gerencia, pero el PDF firmado no pudo cargarse.";
    showPdfInlineError(error.message || "No fue posible cargar el resultado firmado.", retry);
    showUiError(alertBox, error, { onRetry: retry });
  }
}

async function copyManagerApprovalLink() {
  try {
    await navigator.clipboard.writeText(managerApprovalLink.value);
    copyManagerApprovalLinkBtn.textContent = "Enlace copiado";
  } catch {
    managerApprovalLink.select();
    document.execCommand("copy");
  }
}

function closeManagerRequestModal() {
  managerRequestModal.hidden = true;
  copyManagerApprovalLinkBtn.textContent = "Copiar enlace";
}

async function startSignature(payload) {
  try {
    clearFeedback(alertBox);
    setBusy(confirmPositionBtn, true, "Iniciando proceso...");
    const response = await apiFetch("/api/firma/iniciar", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    await redirectToSignature(response.firid, true);
  } catch (error) {
    if (isActiveProcessConflict(error)) {
      activeProcess = (typeof error?.detail === "object" && error.detail !== null) ? error.detail : {};
      pendingStartPayload = payload;
      pendingStartFlow = "signature";
      activeProcessStatus.textContent = activeProcess.status || "ACTIVO";
      resetActiveProcessDialog();
      activeProcessModal.hidden = false;
      continueActiveProcessBtn.focus();
      return;
    }
    showUiError(alertBox, error, { onRetry: error.status === 409 ? null : () => startSignature(payload) });
  } finally {
    setBusy(confirmPositionBtn, false);
  }
}

async function redirectToSignature(firid, autoQr) {
  let validatedReturnUrl = "";
  if (returnUrl) {
    try {
      const validation = await apiFetch("/api/firma/validate-return-url?url=" + encodeURIComponent(returnUrl));
      if (validation.url) {
        validatedReturnUrl = validation.url;
      }
    } catch (error) {
      console.warn("No fue posible validar returnUrl:", error);
    }
  }
  window.location.href = buildSignatureRedirectUrl(firid, { autoQr, returnUrl: validatedReturnUrl });
}

function closeActiveProcessModal() {
  activeProcessModal.hidden = true;
  resetActiveProcessDialog();
  confirmPositionBtn.focus();
}

async function continueActiveProcess() {
  if (cancellationConfirmPending) {
    resetActiveProcessDialog();
    return;
  }
  if (!activeProcess) return;

  if (!activeProcess.docid && !activeProcess.firid && nodeId) {
    try {
      const list = await apiFetch(`/api/documentos?nodid=${encodeURIComponent(nodeId)}&activo=true`);
      if (list?.items?.length) {
        activeProcess.docid = list.items[0].docid;
        activeProcess.status = list.items[0].estado || activeProcess.status;
      }
    } catch (e) {
      console.warn("No se pudo obtener docid del proceso activo:", e);
    }
  }

  if (activeProcess.firid) {
    const autoQr = ["INICIADA", "GENERADA"].includes(activeProcess.signature_status);
    await redirectToSignature(activeProcess.firid, autoQr);
    return;
  }

  if (activeProcess.docid) {
    window.location.href = `/documentos/preparar?nodeId=${encodeURIComponent(nodeId)}`;
    return;
  }

  showAlert(alertBox, "warning", "No hay una firma activa que reanudar en este proceso.");
}

async function replaceActiveProcess() {
  if (!activeProcess?.docid && nodeId) {
    try {
      const list = await apiFetch(`/api/documentos?nodid=${encodeURIComponent(nodeId)}&activo=true`);
      if (list?.items?.length) {
        activeProcess.docid = list.items[0].docid;
        activeProcess.status = list.items[0].estado || activeProcess.status;
      }
    } catch (e) {
      console.warn("No se pudo obtener docid del proceso activo:", e);
    }
  }

  if (!activeProcess?.docid) {
    showAlert(alertBox, "danger", "No se pudo identificar el proceso a cancelar.");
    return;
  }

  if (!cancellationConfirmPending) {
    cancellationConfirmPending = true;
    const title = document.querySelector("#activeProcessTitle");
    if (title) title.textContent = "¿Cancelar el proceso de firma en curso?";
    activeProcessDescription.textContent = "Esta acción descartará la preparación pendiente, pero no modificará el PDF original en Alfresco.";
    continueActiveProcessBtn.hidden = false;
    continueActiveProcessBtn.textContent = "No, conservar proceso";
    replaceActiveProcessBtn.textContent = "Sí, cancelar proceso";
    if (dismissActiveProcessBtn) dismissActiveProcessBtn.hidden = true;
    return;
  }

  try {
    setBusy(replaceActiveProcessBtn, true, "Cancelando...");
    await apiFetch("/api/documentos/" + activeProcess.docid + "/cancelar", {
      method: "POST",
      body: JSON.stringify({
        motivo: "Cancelado para iniciar un nuevo proceso sobre el mismo documento"
      })
    });
    const payload = pendingStartPayload;
    const flow = pendingStartFlow;
    closeActiveProcessModal();
    activeProcess = null;
    pendingStartPayload = null;
    pendingStartFlow = null;
    showAlert(alertBox, "success", "Proceso anterior cancelado.");
    confirmPositionBtn.disabled = !currentPosition;
    if (flow === "manager") {
      await startManagerApproval(payload);
    } else if (payload) {
      await startSignature(payload);
    }
  } catch (error) {
    closeActiveProcessModal();
    showUiError(alertBox, error, { onRetry: loadDocumentInfo });
  } finally {
    setBusy(replaceActiveProcessBtn, false);
    resetActiveProcessDialog();
  }
}

function resetActiveProcessDialog() {
  cancellationConfirmPending = false;
  if (activeProcessModal.hidden && !activeProcess) {
    pendingStartPayload = null;
    pendingStartFlow = null;
  }
  activeProcessDescription.textContent = "Este documento ya tiene un proceso activo. Puede retomarlo sin perder el trabajo realizado.";
  continueActiveProcessBtn.textContent = "Continuar proceso anterior";
  replaceActiveProcessBtn.textContent = "Cancelar anterior y continuar";
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !activeProcessModal.hidden) closeActiveProcessModal();
});

window.addEventListener("beforeunload", () => {
  if (managerResultObjectUrl) URL.revokeObjectURL(managerResultObjectUrl);
});
