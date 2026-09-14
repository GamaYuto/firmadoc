import { apiFetch, escapeText, showAlert, setSessionUser } from "./api.js";

const alertBox = document.querySelector("#alerts");
const list = document.querySelector("#pendingList");
const refreshButton = document.querySelector("#refreshButton");
const userInput = document.querySelector("[data-user-input]");

const params = new URLSearchParams(window.location.search);
const initialUser = params.get("user") || "firmante";
if (userInput) userInput.value = initialUser;
refreshButton?.addEventListener("click", loadPending);
userInput?.addEventListener("change", loadPending);

loadPending().catch((error) => showAlert(alertBox, "danger", error.message));

async function loadPending() {
  list.innerHTML = rowTemplate(["Documento", "Etapa", "Rol", "Fecha", "Estado", ""], true);
  const currentUser = userInput?.value?.trim() || initialUser;
  await setSessionUser(currentUser);
  const data = await apiFetch("/api/firma/pendientes");
  if (!data.items.length) {
    list.innerHTML += `<div class="pending-row"><div>No hay pendientes para ${escapeText(currentUser)}.</div></div>`;
    return;
  }
  list.innerHTML += data.items
    .map((item) =>
      rowTemplate([
        item.document_name,
        item.etapa,
        item.rol,
        item.fecha ? new Date(item.fecha).toLocaleString() : "",
        item.estado,
        `<a class="btn btn-primary" href="/firmas/${item.firid}?user=${encodeURIComponent(currentUser)}">Abrir</a>`,
      ]),
    )
    .join("");
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
