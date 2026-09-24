const EDITABLE_PREPARATION_STATUSES = new Set(["BORRADOR", "PREPARADO"]);
const ACTIVE_SIGNATURE_STATUSES = new Set(["INICIADA"]);
const LOCAL_RESULT_STATUSES = new Set(["PENDIENTE_PUBLICACION", "COMPLETADO"]);

function normalizeStatus(value) {
  return String(value || "").trim().toUpperCase();
}

export function isPreparationEditableStatus(status) {
  return EDITABLE_PREPARATION_STATUSES.has(normalizeStatus(status));
}

export function isSignatureAttemptActive(status) {
  return ACTIVE_SIGNATURE_STATUSES.has(normalizeStatus(status));
}

export function isLocalResultReady(status) {
  return LOCAL_RESULT_STATUSES.has(normalizeStatus(status));
}

export function getResultPdfSource(status) {
  const normalized = normalizeStatus(status);
  if (normalized === "PENDIENTE_PUBLICACION") return "LOCAL";
  if (normalized === "COMPLETADO") return "ALFRESCO";
  return null;
}

export function getPreparationMode(status) {
  return isPreparationEditableStatus(status) ? "editable" : "readonly";
}

export function getPublicationControlState(result) {
  const visible = Boolean(result?.can_publish_alfresco);
  const writeEnabled = Boolean(result?.alfresco_write_enabled);
  return {
    visible,
    disabled: visible && !writeEnabled,
    disabledMessage: visible && !writeEnabled ? "Publicacion deshabilitada en este entorno." : "",
  };
}

export function getPublicationErrorAlertType(error) {
  if (error?.code === "PUBLICATION_RECONCILIATION_REQUIRED") return "info";
  if (error?.status === 409 || error?.status === 410) return "info";
  return "danger";
}

const STATUS_LABELS = {
  BORRADOR: "En preparacion",
  PREPARADO: "Preparado",
  EN_CURSO: "En curso",
  PENDIENTE_FIRMA: "Pendiente de firma",
  FIRMADO_PARCIAL: "Firma parcial",
  PENDIENTE_PUBLICACION: "Listo para publicar",
  COMPLETADO: "Publicado en Alfresco",
  CANCELADO: "Cancelado",
  RECHAZADO: "Rechazado",
  ERROR_PUBLICACION: "Error de publicacion",
  INICIADA: "Firma pendiente",
  COMPLETADA: "Firma completada",
};

export function getStatusLabel(status) {
  const normalized = normalizeStatus(status);
  return STATUS_LABELS[normalized] || String(status || "Estado desconocido").replaceAll("_", " ");
}
