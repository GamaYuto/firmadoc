const PUBLICATION_CONFLICT_CODES = new Set([
  "LOCAL_HASH_MISMATCH",
  "REMOTE_VERSION_CONFLICT",
  "REMOTE_HASH_CONFLICT",
  "REMOTE_PRECONDITION_MISSING",
  "TEST_NODE_MISMATCH",
]);

export function describeUiError(error, context = "general") {
  const status = Number(error?.status || 0);
  const code = String(error?.code || "REQUEST_FAILED");
  const fallback = error?.message || "No fue posible completar la operacion.";

  if (code === "NETWORK_ERROR") {
    return { type: "danger", title: "Sin conexion", message: fallback, actionLabel: "Reintentar", retryable: true };
  }
  if (code === "ALFRESCO_WRITE_DISABLED") {
    return { type: "info", title: "Publicacion no disponible", message: fallback, retryable: false };
  }
  if (code === "PUBLICATION_RECONCILIATION_REQUIRED") {
    return {
      type: "warning",
      title: "Publicacion por verificar",
      message: "Alfresco recibio la operacion, pero FirmaDoc debe verificar el resultado antes de volver a publicar.",
      actionLabel: "Actualizar estado",
      retryable: true,
    };
  }
  if (PUBLICATION_CONFLICT_CODES.has(code)) {
    return {
      type: "warning",
      title: "El documento cambio",
      message: fallback,
      actionLabel: "Actualizar estado",
      retryable: true,
    };
  }
  if (status === 401) {
    return { type: "warning", title: "Sesion requerida", message: fallback, actionLabel: "Volver a intentar", retryable: true };
  }
  if (status === 403) {
    return { type: "danger", title: "Acceso restringido", message: fallback, retryable: false };
  }
  if (status === 404) {
    return { type: "warning", title: "Recurso no disponible", message: fallback, retryable: false };
  }
  if (status === 409) {
    return {
      type: "info",
      title: context === "publication" ? "No se puede publicar todavia" : "El proceso cambio",
      message: fallback,
      actionLabel: "Actualizar",
      retryable: true,
    };
  }
  if (status === 410) {
    return { type: "warning", title: "Sesion expirada", message: fallback, actionLabel: "Actualizar", retryable: true };
  }
  if (status === 413) {
    return { type: "warning", title: "Firma demasiado grande", message: fallback, retryable: false };
  }
  if (status === 400 || status === 415 || status === 422) {
    return { type: "warning", title: "Revise la informacion", message: fallback, retryable: false };
  }
  if (status === 502 || status === 503 || status >= 500) {
    return {
      type: "danger",
      title: "Servicio temporalmente no disponible",
      message: fallback,
      actionLabel: "Reintentar",
      retryable: true,
    };
  }
  return { type: "danger", title: "No fue posible continuar", message: fallback, retryable: false };
}

export function showUiError(container, error, { context = "general", onRetry = null } = {}) {
  if (!container) return;
  const feedback = describeUiError(error, context);
  container.replaceChildren();

  const alert = document.createElement("div");
  alert.className = `alert alert-${feedback.type} app-feedback`;
  alert.setAttribute("role", feedback.type === "danger" ? "alert" : "status");

  const content = document.createElement("div");
  const title = document.createElement("strong");
  title.className = "feedback-title";
  title.textContent = feedback.title;
  const message = document.createElement("p");
  message.textContent = feedback.message;
  content.append(title, message);

  if (error?.operationId) {
    const reference = document.createElement("small");
    reference.className = "feedback-reference";
    reference.textContent = `Referencia: ${error.operationId}`;
    content.append(reference);
  }
  alert.append(content);

  if (feedback.retryable && typeof onRetry === "function") {
    const action = document.createElement("button");
    action.type = "button";
    action.className = "btn btn-sm btn-outline-secondary";
    action.textContent = feedback.actionLabel || "Reintentar";
    action.addEventListener("click", onRetry, { once: true });
    alert.append(action);
  }
  container.append(alert);
}

export function clearFeedback(container) {
  container?.replaceChildren();
}
