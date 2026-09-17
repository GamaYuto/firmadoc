import { apiFetch, apiFetchBinary, escapeText, parseFirmaIdFromPath, setBusy, showAlert, setSessionUser, getCurrentUser } from "./api.js";
import { PdfViewer } from "./pdf-viewer.js";
import { isPngDataUrlWithinLimit, pngDataUrlBinarySize } from "./signature-utils.js";
import { getPublicationControlState, getPublicationErrorAlertType, isLocalResultReady, isSignatureAttemptActive } from "./workflow-state.js";

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
let preparation = null;

let qrPollingInterval = null;
let qrCountdownInterval = null;
let currentQrSessionId = null;


init().catch((error) => showAlert(alertBox, "danger", error.message));

async function init() {
  if (!firid) throw new Error("Identificador de firma invalido");
  const user = params.get("user") || "firmante";
  if (userInput) {
    userInput.value = user;
    userInput.addEventListener("change", async () => {
      const newUser = userInput.value.trim();
      if (newUser) {
        await setSessionUser(newUser);
        await loadSignature();
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
  userInput.addEventListener("change", loadSignature);
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
  detail = await apiFetch(`/api/firma/firmas/${firid}`, { user: getCurrentUser("firmante") });
  docName.textContent = detail.document_name;
  docStatus.textContent = detail.document_status || detail.estado;
  signerName.textContent = detail.signer_name;
  typeText.textContent = detail.tipfir === "INTERNA" ? "Firma interna" : "Firma manuscrita";
  resultPdfUrl = `/api/firma/firmas/${firid}/resultado/pdf`;

  const pdfUrl = `/api/alfresco/nodes/${detail.node_id}/content`;
  preparation = await apiFetch(`/api/firma/preparacion/doc/${detail.docid}`, { user: getCurrentUser("firmante") });
  await viewer.load(pdfUrl, preparation.pages, detail.positions.map((position, index) => ({ ...position, id: `sign-${index}`, saved: true })));

  if (isSignatureAttemptActive(detail.estado)) {
    setActiveSignatureMode();
    return;
  }

  setClosedSignatureMode();
  if (isLocalResultReady(detail.document_status)) {
    const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
    renderResult(result);
  } else {
    resultPanel.hidden = true;
    showAlert(alertBox, "info", "El intento ya fue procesado. El resultado local solo esta disponible cuando la firma final queda pendiente de publicacion.");
  }
}

function setActiveSignatureMode() {
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
  handwrittenPanel.hidden = true;
  internalPanel.hidden = true;
  clearButton.hidden = true;
  cancelButton.hidden = true;
  confirmButton.hidden = true;
  preview.closest(".properties")?.setAttribute("hidden", "");
  pageStatus.textContent = detail?.document_status || "Resultado local";
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
    renderResult(result);
  } catch (error) {
    showAlert(alertBox, error.status === 409 ? "info" : "danger", error.message);
  } finally {
    setBusy(confirmButton, false);
  }
}

async function openGeneratedResult() {
  if (!resultPdfUrl) return;
  try {
    setBusy(document.querySelector("#viewGeneratedResult"), true, "Abriendo");
    const blob = await apiFetchBinary(resultPdfUrl, { user: getCurrentUser("firmante") });
    const blobUrl = URL.createObjectURL(blob);
    const child = window.open(blobUrl, "_blank", "noopener");
    if (!child) {
      showAlert(alertBox, "info", "El navegador bloqueo la nueva pestaña del resultado.");
    }
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000);
  } catch (error) {
    showAlert(alertBox, error.status === 409 ? "info" : "danger", error.message);
  } finally {
    setBusy(document.querySelector("#viewGeneratedResult"), false);
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
    const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
    renderResult(result);
  } catch (error) {
    showAlert(alertBox, getPublicationErrorAlertType(error), error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderResult(result) {
  setClosedSignatureMode();
  if (detail) {
    detail.document_status = result.document_status || result.status;
    detail.estado = result.status;
  }
  docStatus.textContent = result.document_status || result.status;
  showAlert(alertBox, "success", result.message);
  resultPanel.hidden = false;
  if (isLocalResultReady(result.document_status || result.status) && preparation?.pages) {
    viewer.load(`/api/firma/firmas/${firid}/resultado/pdf`, preparation.pages, []).catch((err) => {
      console.warn("No fue posible cargar el PDF firmado en el visor:", err);
    });
  }
  const publicationLabel = result.alfresco_publication === "PENDING" ? "Pendiente de publicacion en Alfresco" : result.alfresco_publication === "PUBLISHED" ? "Publicada" : "No iniciada";
  const localStateLabel = result.document_status === "PENDIENTE_PUBLICACION" ? "Pendiente de publicacion en Alfresco" : "Pendiente del siguiente firmante";
  const publicationControl = getPublicationControlState(result);
  const disabledNotice = publicationControl.disabledMessage
    ? `<p class="result-message publication-disabled">${escapeText(publicationControl.disabledMessage)}</p>`
    : "";
  const publishButton = publicationControl.visible
    ? `<button id="publishAlfresco" class="btn btn-primary" type="button" ${publicationControl.disabled ? "disabled" : ""}>Publicar en Alfresco</button>`
    : "";
  resultPanel.innerHTML = `
    <h2>Resultado local</h2>
    <p class="result-message">${escapeText(result.message || "Firma registrada. Pendiente del siguiente firmante.")}</p>
    ${disabledNotice}
    <dl>
      <dt>Documento</dt><dd>${escapeText(result.document_name || detail.document_name)}</dd>
      <dt>Proceso</dt><dd>${escapeText(result.document_status || result.status)}</dd>
      <dt>Firmas completadas</dt><dd>${escapeText(result.completed_signatures)} de ${escapeText(result.total_signatures)}</dd>
      <dt>Version fuente Alfresco</dt><dd>${escapeText(result.original_version)}</dd>
      <dt>Version final</dt><dd>${escapeText(result.final_version || "Pendiente")}</dd>
      <dt>Estado de publicacion</dt><dd>${escapeText(publicationLabel)}</dd>
      <dt>Estado local</dt><dd>${escapeText(localStateLabel)}</dd>
      <dt>Hash final</dt><dd>${escapeText(result.final_hash_short || "No disponible")}</dd>
      <dt>Fecha</dt><dd>${escapeText(new Date(result.date).toLocaleString())}</dd>
    </dl>
    <div class="button-row">
      <button id="viewGeneratedResult" class="btn btn-info" type="button">Ver resultado generado</button>
      ${publishButton}
      <a class="btn btn-primary" href="/pendientes?user=${encodeURIComponent(getCurrentUser("firmante"))}">Volver a pendientes</a>
      ${params.get("returnUrl") ? `<a class="btn btn-success" href="${escapeText(params.get("returnUrl"))}">Volver a Alfresco</a>` : ""}
    </div>
  `;
  document.querySelector("#viewGeneratedResult").addEventListener("click", openGeneratedResult);
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
  } catch (error) {
    showAlert(alertBox, "danger", error.message);
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
    if (statusData.estado === "USADO") {
      closeQrModalView();
      showAlert(alertBox, "success", "Firma capturada y confirmada exitosamente desde el móvil.");
      cleanupSignature();
      const result = await apiFetch(`/api/firma/firmas/${firid}/resultado`, { user: getCurrentUser("firmante") });
      renderResult(result);
    } else if (statusData.estado === "EXPIRADO" || statusData.estado === "CANCELADO") {
      closeQrModalView();
      showAlert(alertBox, "warning", "La sesión QR ha expirado o fue cancelada.");
    }
  } catch (err) {
    console.warn("Error consultando estado QR:", err);
  }
}
