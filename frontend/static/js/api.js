let currentCsrfToken = null;

export function getCsrfToken() {
  return currentCsrfToken;
}

export function setCsrfToken(token) {
  currentCsrfToken = token;
}

export async function setSessionUser(userId) {
  const response = await fetch("/api/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ user_id: userId }),
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("No fue posible establecer la sesion");
  }
  const data = await response.json();
  if (data && data.csrf_token) {
    currentCsrfToken = data.csrf_token;
  }
  return data;
}

export async function getCurrentSession() {
  try {
    const response = await fetch("/api/auth/me", {
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    if (data && data.csrf_token) {
      currentCsrfToken = data.csrf_token;
    }
    return data;
  } catch {
    return null;
  }
}

export async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const method = (options.method || "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    if (!currentCsrfToken) {
      await getCurrentSession();
    }
    if (currentCsrfToken && !headers.has("X-FirmaDoc-CSRF")) {
      headers.set("X-FirmaDoc-CSRF", currentCsrfToken);
    }
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: options.credentials || "same-origin",
  });
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
  const method = (options.method || "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    if (!currentCsrfToken) {
      await getCurrentSession();
    }
    if (currentCsrfToken && !headers.has("X-FirmaDoc-CSRF")) {
      headers.set("X-FirmaDoc-CSRF", currentCsrfToken);
    }
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: options.credentials || "same-origin",
  });
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

export function getCurrentUser(fallback = "usuario") {
  const el = document.querySelector("[data-user-input]");
  return el?.value?.trim() || fallback;
}
