import {
  apiFetch,
  getCurrentSession,
  showAlert,
  escapeText,
} from "./api.js";

const alerts = document.getElementById("alerts");
const userInput = document.querySelector("[data-user-input]");
const refreshButton = document.getElementById("refreshButton");
const currentPathText = document.getElementById("currentPathText");
const searchForm = document.getElementById("searchForm");
const searchInput = document.getElementById("searchInput");
const clearSearchButton = document.getElementById("clearSearchButton");
const folderList = document.getElementById("folderList");
const documentList = document.getElementById("documentList");
const upFolderButton = document.getElementById("upFolderButton");

let folderHistory = [];
let currentFolderId = null;

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function formatDate(dateStr) {
  if (!dateStr) return "-";
  try {
    const d = new Date(dateStr);
    return d.toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return dateStr;
  }
}

async function initUser() {
  const session = await getCurrentSession();
  if (session && session.user_id) {
    
  } else {
    
    
  }

  userInput.addEventListener("change", async () => {
    const val = "";
    if (!val) return;
    try {
      
      showAlert(alerts, "success", `Sesión activa como: ${val}`);
      await loadFolder(currentFolderId);
    } catch (err) {
      showAlert(alerts, "danger", err.message);
    }
  });
}

async function loadFolder(folderId = null) {
  try {
    const url = folderId ? `/api/alfresco/explorar?folder_id=${encodeURIComponent(folderId)}` : `/api/alfresco/explorar`;
    const data = await apiFetch(url);

    currentFolderId = data.folder_id;
    currentPathText.textContent = `Carpeta: ${currentFolderId}`;
    upFolderButton.hidden = folderHistory.length === 0;

    renderFolders(data.folders || []);
    renderDocuments(data.documents || []);
  } catch (err) {
    showAlert(alerts, "danger", `Error al explorar Alfresco: ${err.message}`);
  }
}

function renderFolders(folders) {
  if (!folders.length) {
    folderList.innerHTML = `<p class="text-muted mb-0 small">No hay subcarpetas en este nivel.</p>`;
    return;
  }

  folderList.innerHTML = folders
    .map(
      (f) => `
      <div class="folder-card" data-folder-id="${escapeText(f.id)}">
        <span class="folder-icon">📁</span>
        <div class="overflow-hidden">
          <strong class="d-block text-truncate" title="${escapeText(f.name)}">${escapeText(f.name)}</strong>
          <small class="text-muted">${formatDate(f.modified_at)}</small>
        </div>
      </div>
    `
    )
    .join("");

  folderList.querySelectorAll(".folder-card").forEach((card) => {
    card.addEventListener("click", () => {
      const nextId = card.dataset.folderId;
      if (currentFolderId) {
        folderHistory.push(currentFolderId);
      }
      loadFolder(nextId);
    });
  });
}

function renderDocuments(docs) {
  if (!docs.length) {
    documentList.innerHTML = `
      <tr>
        <td colspan="5" class="text-center py-4 text-muted">No se encontraron documentos PDF en esta ubicación.</td>
      </tr>
    `;
    return;
  }

  documentList.innerHTML = docs
    .map(
      (doc) => `
      <tr>
        <td>
          <div class="d-flex align-items-center gap-2">
            <span class="badge bg-danger">PDF</span>
            <strong class="text-truncate" style="max-width: 380px;" title="${escapeText(doc.name)}">${escapeText(doc.name)}</strong>
          </div>
          ${doc.path ? `<small class="text-muted d-block">${escapeText(doc.path)}</small>` : ""}
        </td>
        <td><span class="badge bg-light text-dark border">v${escapeText(doc.version_label || "1.0")}</span></td>
        <td>${formatBytes(doc.size_bytes)}</td>
        <td>${formatDate(doc.modified_at)}</td>
        <td class="text-end">
          <div class="btn-group btn-group-sm">
            <a class="btn btn-outline-secondary" href="/api/alfresco/nodes/${encodeURIComponent(doc.id)}/content" target="_blank">Ver</a>
            <a class="btn btn-primary" href="/documentos/preparar?nodeId=${encodeURIComponent(doc.id)}">Solicitar firma</a>
          </div>
        </td>
      </tr>
    `
    )
    .join("");
}

upFolderButton.addEventListener("click", () => {
  if (folderHistory.length > 0) {
    const prevFolder = folderHistory.pop();
    loadFolder(prevFolder);
  }
});

refreshButton.addEventListener("click", () => {
  loadFolder(currentFolderId);
});

searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = searchInput.value.trim();
  if (!q) return;

  try {
    currentPathText.textContent = `Resultados de búsqueda para: "${q}"`;
    clearSearchButton.hidden = false;
    folderList.innerHTML = `<p class="text-muted mb-0 small">Búsqueda directa de documentos PDF.</p>`;
    documentList.innerHTML = `
      <tr>
        <td colspan="5" class="text-center py-4 text-muted">Buscando documentos...</td>
      </tr>
    `;

    const results = await apiFetch(`/api/alfresco/buscar?q=${encodeURIComponent(q)}`);
    renderDocuments(results);
  } catch (err) {
    showAlert(alerts, "danger", `Error en búsqueda: ${err.message}`);
  }
});

clearSearchButton.addEventListener("click", () => {
  searchInput.value = "";
  clearSearchButton.hidden = true;
  loadFolder(currentFolderId);
});

async function boot() {
  await initUser();
  await loadFolder();
}

boot();

