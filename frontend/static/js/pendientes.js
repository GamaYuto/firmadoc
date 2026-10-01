import { apiFetch, escapeText } from "./api.js?v=12.1";
import { clearFeedback, showUiError } from "./ui-feedback.js?v=12.1";
import { getStatusLabel } from "./workflow-state.js?v=12.1";

const alertBox = document.querySelector("#alerts");
const list = document.querySelector("#pendingList");
const refreshButton = document.querySelector("#refreshButton");

refreshButton?.addEventListener("click", () => loadPending());


loadPending();

async function loadPending() {
  clearFeedback(alertBox);
  list.innerHTML = '<div class="skeleton-row" aria-label="Cargando pendientes"></div>';
    try {
    
    const data = await apiFetch("/api/firma/pendientes");
    list.innerHTML = rowTemplate(["Documento", "Etapa", "Rol", "Fecha", "Estado", ""], true);
    if (!data.items.length) {
      list.innerHTML = `<div class="empty-state"><strong>Sin firmas pendientes</strong><p>No hay documentos asignados a ti.</p></div>`;
      return;
    }
    list.innerHTML += data.items
      .map((item) =>
        rowTemplate([
          item.document_name,
          item.etapa,
          item.rol,
          item.fecha ? new Date(item.fecha).toLocaleString() : "",
          getStatusLabel(item.estado),
          `<a class="btn btn-primary" href="/firmas/${item.firid}">Abrir</a>`,
        ]),
      )
      .join("");
  } catch (error) {
    list.innerHTML = "";
    showUiError(alertBox, error, { onRetry: loadPending });
  }
}

function rowTemplate(values, header = false) {
  return `
    <div class="pending-row${header ? " header" : ""}">
      ${values
        .map((value, index) => {
          if (index === values.length - 1 && String(value).startsWith("<a")) return `<div>${value}</div>`;
          return `<div>${escapeText(value)}</div>`;
        })
        .join("")}
    </div>
  `;
}

