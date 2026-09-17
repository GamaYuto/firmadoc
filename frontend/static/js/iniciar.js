import { apiFetch, escapeText, setSessionUser, showAlert, setBusy } from "./api.js";
import { PdfViewer } from "./pdf-viewer.js";

const params = new URLSearchParams(window.location.search);
const nodeId = params.get("nodeId");
const returnUrl = params.get("returnUrl");

const alertBox = document.querySelector("#alerts");
const userInput = document.querySelector("[data-user-input]");
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

let viewer = null;
let currentPosition = null;
let searchTimeout = null;

init().catch((error) => showAlert(alertBox, "danger", error.message));

async function init() {
  if (!nodeId) throw new Error("Se requiere nodeId en la URL");
  const user = params.get("user") || "admin";
  
  if (userInput) {
    userInput.value = user;
    userInput.addEventListener("change", async () => {
      await setSessionUser(userInput.value.trim());
    });
  }
  await setSessionUser(user);

  viewer = new PdfViewer({
    container: document.querySelector("#pdfContainer"),
    thumbs: document.querySelector("#thumbs"),
    status: pageStatus,
    editable: true,
    onSelectionChange: (pos) => {
      currentPosition = pos;
      confirmPositionBtn.disabled = !pos;
    }
  });
  
  viewer.signer = "firmante";

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
      }
    });
  });

  signerUserSearch.addEventListener("input", (e) => {
    clearTimeout(searchTimeout);
    const query = e.target.value.trim();
    if (query.length < 2) {
      userSearchResults.innerHTML = "";
      return;
    }
    searchTimeout = setTimeout(() => performUserSearch(query), 300);
  });

  document.querySelector("#deletePosition")?.addEventListener("click", () => {
    viewer.clearPositions();
  });

  confirmPositionBtn.addEventListener("click", confirmPreparation);
}

async function performUserSearch(query) {
  try {
    const results = await apiFetch(`/api/alfresco/usuarios/buscar?q=${encodeURIComponent(query)}`);
    userSearchResults.innerHTML = "";
    if (results.length === 0) {
      userSearchResults.innerHTML = '<div class="list-group-item text-muted">No se encontraron usuarios</div>';
      return;
    }
    
    results.forEach(user => {
      const a = document.createElement("a");
      a.href = "#";
      a.className = "list-group-item list-group-item-action";
      a.textContent = `${user.displayName} (${user.userName})`;
      a.addEventListener("click", (e) => {
        e.preventDefault();
        signerUser.value = user.userName;
        signerUserSearch.value = "";
        userSearchResults.innerHTML = "";
        selectedUserDisplay.textContent = `Usuario seleccionado: ${user.displayName} (${user.userName})`;
        selectedUserDisplay.hidden = false;
      });
      userSearchResults.appendChild(a);
    });
  } catch (error) {
    console.error("Error buscando usuarios:", error);
  }
}

async function loadDocumentInfo() {
  try {
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
    showAlert(alertBox, "danger", error.message);
  }
}

async function confirmPreparation() {
  if (!currentPosition) return;
  
  const isOther = document.querySelector('input[name="signerType"]:checked').value === "otro";
  let targetUser = "yo";
  if (isOther) {
    targetUser = signerUser.value.trim();
    if (!targetUser) {
      showAlert(alertBox, "danger", "Debe especificar el usuario firmante");
      return;
    }
  }

  try {
    setBusy(confirmPositionBtn, true, "Iniciando proceso...");
    const payload = {
      node_id: nodeId,
      signer_user_id: targetUser,
      page: currentPosition.pagina,
      posx: currentPosition.posx,
      posy: currentPosition.posy,
      width: currentPosition.ancho,
      height: currentPosition.alto
    };

    const response = await apiFetch("/api/firma/iniciar", {
      method: "POST",
      body: JSON.stringify(payload)
    });

    let redirectUrl = `/firmas/${response.firid}?user=${encodeURIComponent(userInput.value.trim())}&autoQr=true`;
    if (returnUrl) {
      try {
        const urlValidation = await apiFetch(`/api/firma/validate-return-url?url=${encodeURIComponent(returnUrl)}`);
        if (urlValidation.url) {
          redirectUrl += `&returnUrl=${encodeURIComponent(urlValidation.url)}`;
        } else {
          console.warn("returnUrl rechazado por el servidor:", returnUrl);
        }
      } catch (e) {
        console.warn("Error validando returnUrl:", e);
      }
    }
    window.location.href = redirectUrl;

  } catch (error) {
    showAlert(alertBox, "danger", error.message);
  } finally {
    setBusy(confirmPositionBtn, false);
  }
}
