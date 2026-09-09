export function getCurrentUser(defaultUser = "preparador") {
  const input = document.querySelector("[data-user-input]");
  const value = input?.value?.trim();
  return value || defaultUser;
}

export function labIdentityHeaders(user) {
  return { "X-FirmaDoc-User": user || getCurrentUser() };
}

export async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const identity = labIdentityHeaders(options.user || getCurrentUser());
  for (const [key, value] of Object.entries(identity)) {
    headers.set(key, value);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let message = "No fue posible completar la operacion";
    let detail = null;
    try {
      const data = await response.json();
      detail = data.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail.message === "string") {
        message = detail.message;
      }
    } catch {
      message = response.statusText || message;
    }
    const error = new Error(message);
    error.status = response.status;
    if (detail && typeof detail === "object") {
      error.code = detail.code;
      error.detail = detail;
    }
    throw error;
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

export async function apiFetchBinary(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", options.accept || "application/pdf");
  const identity = labIdentityHeaders(options.user || getCurrentUser());
  for (const [key, value] of Object.entries(identity)) {
    headers.set(key, value);
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let message = "No fue posible completar la operacion";
    let detail = null;
    try {
      const data = await response.json();
      detail = data.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail.message === "string") {
        message = detail.message;
      }
    } catch {
      message = response.statusText || message;
    }
    const error = new Error(message);
    error.status = response.status;
    if (detail && typeof detail === "object") {
      error.code = detail.code;
      error.detail = detail;
    }
    throw error;
  }
  return response.blob();
}

export function escapeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[char];
  });
}

export function showAlert(container, type, message) {
  container.innerHTML = `<div class="alert alert-${type}" role="alert">${escapeText(message)}</div>`;
}

export function setBusy(button, busy, label = "Procesando") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.disabled = true;
    button.textContent = label;
  } else {
    button.disabled = false;
    button.textContent = button.dataset.originalText || button.textContent;
  }
}

export function parseFirmaIdFromPath() {
  const match = window.location.pathname.match(/\/firmas\/(\d+)/);
  return match ? Number(match[1]) : null;
}

export function isUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
