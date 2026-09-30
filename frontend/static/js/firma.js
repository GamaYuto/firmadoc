import { apiFetch, apiFetchBinary, escapeText, parseFirmaIdFromPath, setBusy, showAlert, setSessionUser, getCurrentUser } from "./api.js?v=12.1";
import { PdfViewer } from "./pdf-viewer.js";
import { isPngDataUrlWithinLimit, pngDataUrlBinarySize } from "./signature-utils.js";
import {
  getPublicationControlState,
  getResultPdfSource,
  getStatusLabel,
  isLocalResultReady,
  isManagerApprovalFlow,
  isSignatureAttemptActive,
  requiresLiveSignaturePreparation,
} from "./workflow-state.js?v=12.2";
import { clearFeedback, showUiError } from "./ui-feedback.js?v=12.1";

const firid = parseFirmaIdFromPath();
const params = new URLSearchParams(window.location.search);
const alertBox = document.querySelector("#alerts");
const userInput = document.querySelector("[data-user-input]");
const docName = document.querySelector("#docName");
const docStatus = document.querySelector("#docStatus");
const signerName = document.querySelector("#signerName");
const typeText = document.querySelector("#signatureTypeText");
const pageStatus = document.querySelector("#pageStatus");
const handwrittenPanel = document.querySelector("#handwrittenPanel");
const internalPanel = document.querySelector("#internalPanel");
const preview = document.querySelector("#signaturePreview");
const clearButton = document.querySelector("#clearButton");
const cancelButton = document.querySelector("#cancelButton");
const confirmButton = document.querySelector("#confirmButton");
const zoomInput = document.querySelector("#zoomSelect");
const resultPanel = document.querySelector("#resultPanel");
const taskKicker = document.querySelector("#taskKicker");
const taskTitle = document.querySelector("#taskTitle");

// QR modal elements
const openQrButton = document.querySelector("#openQrButton");
const qrModal = document.querySelector("#qrModal");
const closeQrModal = document.querySelector("#closeQrModal");
const cancelQrButton = document.querySelector("#cancelQrButton");
const qrContainer = document.querySelector("#qrContainer");
const qrCountdown = document.querySelector("#qrCountdown");
const qrStatusText = document.querySelector("#qrStatusText");
const qrDirectLink = document.querySelector("#qrDirectLink");

let detail = null;
let viewer = null;
let signaturePad = null;
let lastPreviewDataUrl = null;
let resizeHandler = null;
let resultPdfUrl = null;
let localResultPdfUrl = null;
let sourcePdfUrl = null;
let resultObjectUrl = null;
let preparation = null;

let qrPollingInterval = null;
let qrCountdownInterval = null;
let currentQrSessionId = null;
let qrPollingFailures = 0;


init().catch((error) => showUiError(alertBox, error, { onRetry: () => window.location.reload() }));

async function init() {
  if (!firid) throw new Error("Identificador de firma invalido");
  const user = params.get("user") || "firmante";
  if (userInput) {
    userInput.value = user;
    userInput.addEventListener("change", async () => {
      const newUser = userInput.value.trim();
      if (newUser) {
        try {
          await setSessionUser(newUser);
          await loadSignature();
        } catch (error) {
          showUiError(alertBox, error, { onRetry: loadSignature });
        }
      }
    });
  }
  await setSessionUser(user);
  viewer = new PdfViewer({
    container: document.querySelector("#pdfContainer"),
    thumbs: document.querySelector("#thumbs"),
    status: pageStatus,
    editable: false,
  });
  bindControls();
  await loadSignature();
  
  if (params.get("autoQr") === "true" && detail?.tipfir === "MANUSCRITA" && isSignatureAttemptActive(detail.estado)) {
    openQrModal();
  }
}

function bindControls() {
  zoomInput.addEventListener("change", async () => viewer.setZoom(Number(zoomInput.value)));
  cancelButton.addEventListener("click", () => {
    if (window.confirm("Desea cancelar y volver a pendientes?")) {
      cleanupSignature();
      window.location.href = `/pendientes?user=${encodeURIComponent(getCurrentUser("firmante"))}`;
    }
  });
  clearButton.addEventListener("click", () => {
    signaturePad?.clear();
    lastPreviewDataUrl = null;
    renderPreview();
  });
  confirmButton.addEventListener("click", confirmSignature);
  openQrButton?.addEventListener("click", openQrModal);
  closeQrModal?.addEventListener("click", closeQrModalView);
  cancelQrButton?.addEventListener("click", closeQrModalView);
}


async function loadSignature() {
  cleanupSignature();
  clearFeedback(alertBox);
  pageStatus.textContent = "Cargando documento";
  detail = await apiFetch(`/api/firma/firmas/${firid}`, { user: getCurrentUser("firmante") });
  docName.textContent = detail.document_name;
  docStatus.textContent = getStatusLabel(detail.document_status || detail.estado);
  signerName.textContent = detail.signer_name;
  typeText.textContent = isManagerApprovalFlow(detail)
    ? "Autorización de Gerencia"
    : detail.tipfir === "INTERNA"
      ? "Firma interna"
      : "Firma manuscrita";
  localResultPdfUrl = `/api/firma/firmas/${firid}/resultado/pdf`;

  sourcePdfUrl = `/api/alfresco/nodes/${detail.node_id}/content`;
  if (isManagerApprovalFlow(detail)) {
    setClosedSignatureMode();

    if (isLocalResultReady(detail.document_status)) {
      const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
      await renderResult(result);
    } else {
      resultPanel.hidden = true;
      showAlert(alertBox, "info", "El documento está pendiente de autorización por Gerencia.");
    }
    return;
  }

  const needsLivePreparation = requiresLiveSignaturePreparation(detail);
  if (!needsLivePreparation) {
    setClosedSignatureMode();
    if (isLocalResultReady(detail.document_status)) {
      const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
      await renderResult(result);
    } else {
      resultPanel.hidden = true;
      showAlert(alertBox, "info", "El intento ya fue procesado. El resultado local solo esta disponible cuando la firma final queda pendiente de publicacion.");
    }
    return;
  }

  preparation = await apiFetch(`/api/firma/preparacion/doc/${detail.docid}`, { user: getCurrentUser("firmante") });
  const sourcePositions = detail.positions.map((position, index) => ({ ...position, id: `sign-${index}`, saved: true }));
  await viewer.load(sourcePdfUrl, preparation.pages, sourcePositions);
  setActiveSignatureMode();
}

function setActiveSignatureMode() {
  taskKicker.textContent = "Accion pendiente";
  taskTitle.textContent = "Revisar y firmar";
  handwrittenPanel.hidden = detail.tipfir !== "MANUSCRITA";
  internalPanel.hidden = detail.tipfir === "MANUSCRITA";
  clearButton.hidden = detail.tipfir !== "MANUSCRITA";
  cancelButton.hidden = false;
  confirmButton.hidden = false;
  preview.closest(".properties")?.removeAttribute("hidden");
  resultPanel.hidden = true;
  pageStatus.textContent = "Firma activa";
  if (detail.tipfir === "MANUSCRITA") {
    setupSignaturePad();
  } else {
    preview.innerHTML = internalPreviewHtml();
  }
}

function setClosedSignatureMode() {
  if (isManagerApprovalFlow(detail)) {
    taskKicker.textContent = "Autorización completada";
    taskTitle.textContent = "Autorizado por Gerencia";
  } else {
    taskKicker.textContent = "Resultado";
    taskTitle.textContent = detail?.document_status === "COMPLETADO" ? "Firma publicada" : "Firma completada";
  }
  handwrittenPanel.hidden = true;
  internalPanel.hidden = true;
  clearButton.hidden = true;
  cancelButton.hidden = true;
  confirmButton.hidden = true;
  preview.closest(".properties")?.setAttribute("hidden", "");
  pageStatus.textContent = getStatusLabel(detail?.document_status || "Resultado local");
  preview.textContent = "Sin previsualizacion";
}

function setupSignaturePad() {
  cleanupSignature();
  const canvas = document.querySelector("#signatureCanvas");
  resizeCanvas(canvas);
  signaturePad = new window.SignaturePad(canvas, {
    backgroundColor: "rgba(255,255,255,0)",
    penColor: "#08337B",
  });
  signaturePad.addEventListener("endStroke", () => {
    lastPreviewDataUrl = signaturePad.isEmpty() ? null : signaturePad.toDataURL("image/png");
    renderPreview();
  });
  resizeHandler = () => resizeSignatureCanvasPreservingStroke();
  window.addEventListener("resize", resizeHandler);
}

function resizeCanvas(canvas) {
  const ratio = Math.max(window.devicePixelRatio || 1, 1);
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function resizeSignatureCanvasPreservingStroke() {
  if (!signaturePad) return;
  const canvas = document.querySelector("#signatureCanvas");
  const previous = signaturePad.isEmpty() ? null : signaturePad.toDataURL("image/png");
  signaturePad.off();
  resizeCanvas(canvas);
  signaturePad = new window.SignaturePad(canvas, {
    backgroundColor: "rgba(255,255,255,0)",
    penColor: "#08337B",
  });
  signaturePad.addEventListener("endStroke", () => {
    lastPreviewDataUrl = signaturePad.isEmpty() ? null : signaturePad.toDataURL("image/png");
    renderPreview();
  });
  if (previous) {
    signaturePad.fromDataURL(previous);
    lastPreviewDataUrl = previous;
    showAlert(alertBox, "info", "Se ajusto el area de firma y se conservo el trazo actual.");
  }
}

function renderPreview() {
  if (!lastPreviewDataUrl) {
    preview.textContent = "Sin previsualizacion";
    return;
  }
  preview.innerHTML = `<img alt="Previsualizacion de firma manuscrita" src="${lastPreviewDataUrl}">`;
}

function internalPreviewHtml() {
  return `
    <div>
      <strong>FIRMADO ELECTRONICAMENTE</strong><br>
      ${escapeText(detail.signer_name)}<br>
      ${escapeText(detail.signer_role || "")}<br>
      Usuario: ${escapeText(detail.signer_user)}
    </div>
  `;
}

async function confirmSignature() {
  if (!isSignatureAttemptActive(detail.estado)) {
    showAlert(alertBox, "info", "La firma ya fue procesada.");
    return;
  }
  try {
    setBusy(confirmButton, true, "Confirmando");
    let result;
    if (detail.tipfir === "MANUSCRITA") {
      if (!signaturePad || signaturePad.isEmpty()) {
        showAlert(alertBox, "danger", "La firma manuscrita no puede estar vacia.");
        return;
      }
      const dataUrl = signaturePad.toDataURL("image/png");
      if (!isPngDataUrlWithinLimit(dataUrl, detail.max_png_size)) {
        showAlert(alertBox, "danger", `La firma manuscrita supera el tamano permitido (${pngDataUrlBinarySize(dataUrl)} bytes).`);
        return;
      }
      result = await apiFetch(`/api/firma/firmas/${firid}/confirmar-manuscrita`, {
        method: "POST",
        user: getCurrentUser("firmante"),
        body: JSON.stringify({ png_data_url: dataUrl }),
      });
    } else {
      result = await apiFetch(`/api/firma/firmas/${firid}/confirmar-interna`, {
        method: "POST",
        user: getCurrentUser("firmante"),
        body: JSON.stringify({ confirm: true }),
      });
    }
    cleanupSignature();
    await renderResult(result);
  } catch (error) {
    showUiError(alertBox, error, { onRetry: loadSignature });
  } finally {
    setBusy(confirmButton, false);
  }
}

async function publishToAlfresco(event) {
  const button = event.currentTarget;
  try {
    setBusy(button, true, "Publicando");
    const publication = await apiFetch(`/api/firma/documentos/${detail.docid}/publicar`, {
      method: "POST",
      user: getCurrentUser("firmante"),
    });
    showAlert(alertBox, "success", publication.message);
    if (await redirectToReturnUrlAfterPublication()) return;
    const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
    await renderResult(result);
  } catch (error) {
    showUiError(alertBox, error, { context: "publication", onRetry: loadSignature });
  } finally {
    setBusy(button, false);
  }
}

async function redirectToReturnUrlAfterPublication() {
  const rawReturnUrl = params.get("returnUrl");
  if (!rawReturnUrl) return false;
  try {
    const validation = await apiFetch("/api/firma/validate-return-url?url=" + encodeURIComponent(rawReturnUrl));
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
function renderPdfLoadError(error, retryAction) {
  const message = error?.message || "No fue posible cargar el PDF firmado.";
  const container = document.querySelector("#pdfContainer");
  container.innerHTML = "";
  const notice = document.createElement("div");
  notice.className = "pdf-inline-error";
  notice.setAttribute("role", "alert");
  notice.innerHTML = `
    <strong>No se pudo verificar la previsualizacion del resultado</strong>
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

async function loadResultPdfInline(url, pdfSource, retryAction) {
  if (!url) return false;
  try {
    if (resultObjectUrl) {
      URL.revokeObjectURL(resultObjectUrl);
      resultObjectUrl = null;
    }
    const loadUrl = pdfSource === "LOCAL"
      ? URL.createObjectURL(await apiFetchBinary(url, { user: getCurrentUser("firmante") }))
      : url;
    if (pdfSource === "LOCAL") {
      resultObjectUrl = loadUrl;
    }
    await viewer.load(loadUrl, [], []);
    pageStatus.textContent = "PDF firmado cargado";
    return true;
  } catch (error) {
    renderPdfLoadError(error, retryAction);
    showUiError(alertBox, error, { onRetry: retryAction });
    return false;
  }
}

async function renderResult(result) {
  setClosedSignatureMode();
  if (detail) {
    detail.document_status = result.document_status || result.status;
    detail.estado = result.status;
  }
  docStatus.textContent = getStatusLabel(result.document_status || result.status);
  resultPanel.hidden = false;
  const resultStatus = result.document_status || result.status;
  const pdfSource = getResultPdfSource(resultStatus);
  resultPdfUrl = pdfSource === "LOCAL" ? localResultPdfUrl : pdfSource === "ALFRESCO" ? sourcePdfUrl : null;
  const retryResultLoad = async () => {
    const refreshed = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
    await renderResult(refreshed);
  };
  const pdfLoaded = resultPdfUrl
    ? await loadResultPdfInline(resultPdfUrl, pdfSource, retryResultLoad)
    : false;
  if (resultPdfUrl && !pdfLoaded) {
    showAlert(alertBox, "warning", "El documento fue autorizado, pero el PDF firmado no pudo verificarse en el visor. Reintente antes de publicar.");
  } else {
    showAlert(alertBox, "success", result.message);
  }
  const publicationLabel = result.alfresco_publication === "PENDING" ? "Pendiente de publicacion en Alfresco" : result.alfresco_publication === "PUBLISHED" ? "Publicada" : "No iniciada";
  const localStateLabel = resultPdfUrl && !pdfLoaded
    ? "Previsualizacion del PDF pendiente de verificar"
    : result.document_status === "PENDIENTE_PUBLICACION"
      ? "Documento firmado y listo para publicar"
      : result.document_status === "COMPLETADO"
        ? "Documento publicado y verificado"
        : "Pendiente del siguiente firmante";
  const publicationControl = getPublicationControlState(result);
  if (resultPdfUrl && !pdfLoaded) {
    publicationControl.disabled = true;
    publicationControl.disabledMessage = "Verifique la previsualizacion del PDF firmado antes de publicar.";
  }
  const disabledNotice = publicationControl.disabledMessage
    ? `<p class="result-message publication-disabled">${escapeText(publicationControl.disabledMessage)}</p>`
    : "";
  const publishButton = publicationControl.visible
    ? `<button id="publishAlfresco" class="btn btn-primary" type="button" ${publicationControl.disabled ? "disabled" : ""}>Publicar en Alfresco</button>`
    : "";
  const resultPdfButton = resultPdfUrl && !pdfLoaded
    ? `<button id="retryResultPreview" class="btn btn-info" type="button">Reintentar previsualizacion</button>`
    : "";
  resultPanel.innerHTML = `
    <h2>Resultado local</h2>
    <p class="result-message">${escapeText(result.message || "Firma registrada. Pendiente del siguiente firmante.")}</p>
    ${disabledNotice}
    <dl>
      <dt>Documento</dt><dd>${escapeText(result.document_name || detail.document_name)}</dd>
      <dt>Proceso</dt><dd>${escapeText(getStatusLabel(result.document_status || result.status))}</dd>
      <dt>Firmas completadas</dt><dd>${escapeText(result.completed_signatures)} de ${escapeText(result.total_signatures)}</dd>
      <dt>Version fuente Alfresco</dt><dd>${escapeText(result.original_version)}</dd>
      <dt>Version final</dt><dd>${escapeText(result.final_version || "Pendiente")}</dd>
      <dt>Estado de publicacion</dt><dd>${escapeText(publicationLabel)}</dd>
      <dt>Estado local</dt><dd>${escapeText(localStateLabel)}</dd>
      <dt>Hash final</dt><dd>${escapeText(result.final_hash_short || "No disponible")}</dd>
      <dt>Fecha</dt><dd>${escapeText(new Date(result.date).toLocaleString())}</dd>
    </dl>
    <div class="button-row">
      ${publishButton}
      ${resultPdfButton}
      <a class="btn btn-primary" href="/pendientes?user=${encodeURIComponent(getCurrentUser("firmante"))}">Volver a pendientes</a>
      ${params.get("returnUrl") ? `<a class="btn btn-success" href="${escapeText(params.get("returnUrl"))}">Volver a Alfresco</a>` : ""}
    </div>
  `;
  document.querySelector("#retryResultPreview")?.addEventListener("click", retryResultLoad);
  document.querySelector("#publishAlfresco")?.addEventListener("click", publishToAlfresco);
}

function cleanupSignature() {
  closeQrModalView();
  if (signaturePad) {
    signaturePad.off();
    signaturePad.clear();
    signaturePad = null;
  }
  if (resizeHandler) {
    window.removeEventListener("resize", resizeHandler);
    resizeHandler = null;
  }
  lastPreviewDataUrl = null;
}

async function openQrModal() {
  if (!firid) return;
  try {
    clearFeedback(alertBox);
    qrPollingFailures = 0;
    setBusy(openQrButton, true, "Generando QR");
    const qrData = await apiFetch(`/api/firma/firmas/${firid}/qr`, {
      method: "POST",
      user: getCurrentUser("firmante"),
    });
    currentQrSessionId = qrData.sesid;
    qrContainer.innerHTML = "";
    const absoluteQrUrl = new URL(qrData.qr_url, window.location.origin).href;
    if (window.QRCode) {
      new window.QRCode(qrContainer, {
        text: absoluteQrUrl,
        width: 192,
        height: 192,
        correctLevel: window.QRCode.CorrectLevel.M,
      });
    } else {
      qrContainer.textContent = "Librería QR no cargada";
    }
    qrDirectLink.href = absoluteQrUrl;
    qrStatusText.textContent = "Esperando firma desde el móvil...";


    // Countdown timer
    const expTime = new Date(qrData.expires_at).getTime();
    if (qrCountdownInterval) clearInterval(qrCountdownInterval);
    const updateCountdown = () => {
      const remaining = Math.max(0, Math.floor((expTime - Date.now()) / 1000));
      const mins = String(Math.floor(remaining / 60)).padStart(2, "0");
      const secs = String(remaining % 60).padStart(2, "0");
      qrCountdown.textContent = `${mins}:${secs}`;
      if (remaining <= 0) {
        clearInterval(qrCountdownInterval);
        qrCountdownInterval = null;
        qrStatusText.textContent = "La sesión QR ha expirado.";
      }
    };
    updateCountdown();
    qrCountdownInterval = window.setInterval(updateCountdown, 1000);

    // Polling every 2500ms
    if (qrPollingInterval) clearInterval(qrPollingInterval);
    qrPollingInterval = window.setInterval(pollQrStatus, 2500);

    qrModal.hidden = false;
    closeQrModal?.focus();
  } catch (error) {
    showUiError(alertBox, error, { onRetry: openQrModal });
  } finally {
    setBusy(openQrButton, false);
  }
}

function closeQrModalView() {
  if (qrPollingInterval) {
    clearInterval(qrPollingInterval);
    qrPollingInterval = null;
  }
  if (qrCountdownInterval) {
    clearInterval(qrCountdownInterval);
    qrCountdownInterval = null;
  }
  if (qrModal) qrModal.hidden = true;
  if (qrContainer) qrContainer.innerHTML = "";
  currentQrSessionId = null;
}

async function pollQrStatus() {
  if (!currentQrSessionId) return;
  try {
    const statusData = await apiFetch(`/api/firma/qr/${currentQrSessionId}/estado`, {
      user: getCurrentUser("firmante"),
    });
    qrPollingFailures = 0;
    if (statusData.estado === "USADO") {
      closeQrModalView();
      showAlert(alertBox, "success", "Firma capturada y confirmada exitosamente desde el móvil.");
      cleanupSignature();
      const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
      await renderResult(result);
    } else if (statusData.estado === "EXPIRADO" || statusData.estado === "CANCELADO") {
      closeQrModalView();
      showAlert(alertBox, "warning", "La sesión QR ha expirado o fue cancelada.");
    }
  } catch (err) {
    qrPollingFailures += 1;
    if (qrPollingFailures >= 2 && qrStatusText) {
      qrStatusText.textContent = "Conexion interrumpida. Intentando recuperar el estado...";
    }
    if (qrPollingFailures >= 4) {
      showUiError(alertBox, err, { onRetry: pollQrStatus });
    }
  }
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && qrModal && !qrModal.hidden) closeQrModalView();
});

window.addEventListener("beforeunload", () => {
  if (resultObjectUrl) URL.revokeObjectURL(resultObjectUrl);
});
