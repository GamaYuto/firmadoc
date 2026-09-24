let currentCsrfToken = null;

// Shared HTTP error contract for every frontend flow.

export class ApiError extends Error {
  constructor(message, { status = 0, code = "REQUEST_FAILED", detail = null, operationId = "", sourceVersion = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.operationId = operationId;
    this.sourceVersion = sourceVersion;
  }
}

export function getCsrfToken() {
  return currentCsrfToken;
}

export function setCsrfToken(token) {
  currentCsrfToken = token;
}

export async function setSessionUser(userId) {
  const response = await request("/api/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ user_id: userId }),
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw await buildApiError(response);
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
  const response = await request(path, { ...options, headers });
  if (!response.ok) {
    throw await buildApiError(response);
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
  const response = await request(path, { ...options, headers });
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return response.blob();
}

async function request(path, options) {
  try {
    return await fetch(path, {
      ...options,
      credentials: options.credentials || "same-origin",
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new ApiError("No fue posible conectar con FirmaDoc. Compruebe la conexion e intente nuevamente.", {
      code: "NETWORK_ERROR",
    });
  }
}

async function buildApiError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  const detail = payload?.detail ?? payload;
  let message = "No fue posible completar la operacion";
  if (typeof detail === "string" && detail.trim()) {
    message = detail;
  } else if (detail && typeof detail.message === "string" && detail.message.trim()) {
    message = detail.message;
  } else if (Array.isArray(detail) && detail.length) {
    message = detail.map((item) => item?.msg).filter(Boolean).join(" ") || message;
  } else if (response.statusText) {
    message = response.statusText;
  }

  return new ApiError(message, {
    status: response.status,
    code: detail?.code || `HTTP_${response.status}`,
    detail,
    operationId: detail?.operation_id || "",
    sourceVersion: detail?.source_version || "",
  });
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
    if (button.dataset.busy !== "true") {
      button.dataset.originalText = button.textContent;
      button.dataset.wasDisabled = String(button.disabled);
    }
    button.dataset.busy = "true";
    button.disabled = true;
    button.textContent = label;
  } else {
    button.disabled = button.dataset.wasDisabled === "true";
    button.textContent = button.dataset.originalText || button.textContent;
    delete button.dataset.busy;
    delete button.dataset.wasDisabled;
    delete button.dataset.originalText;
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

export function labIdentityHeaders(user) {
  return {};
}
