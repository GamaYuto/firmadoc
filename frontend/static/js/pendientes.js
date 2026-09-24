import { apiFetch, escapeText, setSessionUser } from "./api.js?v=12.1";
import { clearFeedback, showUiError } from "./ui-feedback.js?v=12.1";
import { getStatusLabel } from "./workflow-state.js?v=12.1";

const alertBox = document.querySelector("#alerts");
const list = document.querySelector("#pendingList");
const refreshButton = document.querySelector("#refreshButton");
const userInput = document.querySelector("[data-user-input]");

const params = new URLSearchParams(window.location.search);
const initialUser = params.get("user") || "firmante";
if (userInput) userInput.value = initialUser;
refreshButton?.addEventListener("click", () => loadPending());
userInput?.addEventListener("change", () => loadPending());

loadPending();

async function loadPending() {
  clearFeedback(alertBox);
  list.innerHTML = '<div class="skeleton-row" aria-label="Cargando pendientes"></div>';
  const currentUser = userInput?.value?.trim() || initialUser;
  try {
    await setSessionUser(currentUser);
    const data = await apiFetch("/api/firma/pendientes");
    list.innerHTML = rowTemplate(["Documento", "Etapa", "Rol", "Fecha", "Estado", ""], true);
    if (!data.items.length) {
      list.innerHTML = `<div class="empty-state"><strong>Sin firmas pendientes</strong><p>No hay documentos asignados a ${escapeText(currentUser)}.</p></div>`;
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
          `<a class="btn btn-primary" href="/firmas/${item.firid}?user=${encodeURIComponent(currentUser)}">Abrir</a>`,
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
