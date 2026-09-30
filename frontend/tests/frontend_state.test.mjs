import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, setBusy } from "../static/js/api.js";
import { buildDraftPayload, hasDirtyState } from "../static/js/preparation-state.js";
import { RenderSequencer } from "../static/js/render-sequencer.js";
import { isPngDataUrlWithinLimit, pngDataUrlBinarySize } from "../static/js/signature-utils.js";
import {
  getPublicationControlState,
  getPublicationErrorAlertType,
  getResultPdfSource,
  getStatusLabel,
  isLocalResultReady,
  isManagerApprovalFlow,
  isPreparationEditableStatus,
  isSignatureAttemptActive,
  requiresLiveSignaturePreparation,
} from "../static/js/workflow-state.js";
import { describeUiError } from "../static/js/ui-feedback.js";
import { consumeApprovalToken } from "../static/js/manager-approval-token.js";

test("buildDraftPayload conserva propiedades individuales", () => {
  const payload = buildDraftPayload([
    {
      pagina: 1,
      posx: 10,
      posy: 20,
      ancho: 100,
      alto: 50,
      rotaci: 0,
      orden: 1,
      tipfir: "MANUSCRITA",
      usrid: " Firmante ",
    },
    {
      pagina: 2,
      posx: 30,
      posy: 40,
      ancho: 120,
      alto: 60,
      rotaci: 0,
      orden: 2,
      tipfir: "INTERNA",
      usrid: "Interno",
    },
  ]);

  assert.deepEqual(payload.participants, [
    { usrid: "firmante", orden: 1, obliga: true },
    { usrid: "interno", orden: 2, obliga: true },
  ]);
  assert.equal(payload.positions[0].tipfir, "MANUSCRITA");
  assert.equal(payload.positions[1].tipfir, "INTERNA");
});

test("buildDraftPayload rechaza un firmante con ordenes distintos", () => {
  assert.throws(
    () =>
      buildDraftPayload([
        { pagina: 1, posx: 1, posy: 1, ancho: 20, alto: 20, orden: 1, tipfir: "INTERNA", usrid: "firmante" },
        { pagina: 2, posx: 1, posy: 1, ancho: 20, alto: 20, orden: 2, tipfir: "INTERNA", usrid: "firmante" },
      ]),
    /ordenes distintos/,
  );
});

test("RenderSequencer descarta renderizados antiguos", async () => {
  const sequencer = new RenderSequencer();
  const committed = [];
  const first = sequencer.schedule(async ({ isCurrent }) => {
    await new Promise((resolve) => setTimeout(resolve, 5));
    if (isCurrent()) committed.push("first");
  });
  const second = sequencer.schedule(async ({ isCurrent }) => {
    if (isCurrent()) committed.push("second");
  });
  await Promise.all([first, second]);
  assert.deepEqual(committed, ["second"]);
});

test("hasDirtyState detecta nuevos, modificados y eliminados", () => {
  assert.equal(hasDirtyState({ dirty: false, positions: [{ saved: true, dirty: false }] }), false);
  assert.equal(hasDirtyState({ dirty: false, positions: [{ saved: false, dirty: true }] }), true);
  assert.equal(hasDirtyState({ dirty: true, positions: [] }), true);
});

test("workflow-state distingue edicion, firma activa y resultado local", () => {
  assert.equal(isPreparationEditableStatus("BORRADOR"), true);
  assert.equal(isPreparationEditableStatus("PENDIENTE_PUBLICACION"), false);
  assert.equal(isSignatureAttemptActive("INICIADA"), true);
  assert.equal(isSignatureAttemptActive("GENERADA"), false);
  assert.equal(isLocalResultReady("PENDIENTE_PUBLICACION"), true);
  assert.equal(isLocalResultReady("COMPLETADO"), true);
  assert.equal(isLocalResultReady("FIRMADO_PARCIAL"), false);
});

test("resultado PDF usa artefacto local antes de publicar y Alfresco despues", () => {
  assert.equal(getResultPdfSource("PENDIENTE_PUBLICACION"), "LOCAL");
  assert.equal(getResultPdfSource("COMPLETADO"), "ALFRESCO");
  assert.equal(getResultPdfSource("FIRMADO_PARCIAL"), null);
});

test("publication control refleja autorizacion e interruptor", () => {
  assert.deepEqual(
    getPublicationControlState({ can_publish_alfresco: true, alfresco_write_enabled: false }),
    {
      visible: true,
      disabled: true,
      disabledMessage: "Publicacion deshabilitada en este entorno.",
    },
  );
  assert.equal(getPublicationControlState({ can_publish_alfresco: false, alfresco_write_enabled: true }).visible, false);
  assert.equal(getPublicationControlState({ can_publish_alfresco: true, alfresco_write_enabled: true }).disabled, false);
});

test("publication errors muestran conflictos y expiraciones como informativos", () => {
  assert.equal(getPublicationErrorAlertType({ status: 409 }), "info");
  assert.equal(getPublicationErrorAlertType({ status: 410 }), "info");
  assert.equal(getPublicationErrorAlertType({ code: "PUBLICATION_RECONCILIATION_REQUIRED" }), "info");
  assert.equal(getPublicationErrorAlertType({ status: 500 }), "danger");
});

test("describeUiError distingue permisos, expiracion y fallos temporales", () => {
  assert.equal(describeUiError({ status: 401, message: "Debe autenticarse" }).title, "Autenticacion requerida");
  assert.equal(describeUiError({ status: 403, message: "No autorizado" }).title, "Usuario no autorizado");
  assert.equal(describeUiError({ status: 410, message: "Expirada" }).title, "Sesion expirada");
  assert.equal(describeUiError({ status: 503, message: "No disponible" }).retryable, true);
  assert.equal(describeUiError({ code: "PUBLICATION_RECONCILIATION_REQUIRED" }).title, "Publicacion por verificar");
});

test("Gerencia APROBAR no se trata como firma interna activa", () => {
  const detail = {
    step_type: "APROBAR",
    estado: "COMPLETADA",
    document_status: "PENDIENTE_PUBLICACION",
  };
  assert.equal(isManagerApprovalFlow(detail), true);
  assert.equal(requiresLiveSignaturePreparation(detail), false);
  assert.equal(isSignatureAttemptActive(detail.estado), false);
});

test("getStatusLabel traduce estados del flujo sin alterar desconocidos", () => {
  assert.equal(getStatusLabel("PENDIENTE_PUBLICACION"), "Listo para publicar");
  assert.equal(getStatusLabel("COMPLETADO"), "Publicado en Alfresco");
  assert.equal(getStatusLabel("ESTADO_NUEVO"), "ESTADO NUEVO");
});

test("ApiError conserva codigo y referencia de operacion", () => {
  const error = new ApiError("Conflicto", { status: 409, code: "REMOTE_VERSION_CONFLICT", operationId: "op-123" });
  assert.equal(error.status, 409);
  assert.equal(error.code, "REMOTE_VERSION_CONFLICT");
  assert.equal(error.operationId, "op-123");
});

test("setBusy restaura el estado deshabilitado original", () => {
  const button = { disabled: true, textContent: "Publicar", dataset: {} };
  setBusy(button, true, "Publicando");
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, "Publicando");
  setBusy(button, false);
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, "Publicar");
});

test("pngDataUrlBinarySize calcula bytes reales", () => {
  const dataUrl = `data:image/png;base64,${Buffer.from("12345").toString("base64")}`;
  assert.equal(pngDataUrlBinarySize(dataUrl), 5);
  assert.equal(isPngDataUrlWithinLimit(dataUrl, 5), true);
  assert.equal(isPngDataUrlWithinLimit(dataUrl, 4), false);
});

test("token de Gerencia se consume y desaparece inmediatamente de la URL", () => {
  const calls = [];
  const token = consumeApprovalToken(
    { hash: "#token-secreto-de-gerencia" },
    { replaceState: (...args) => calls.push(args) },
  );
  assert.equal(token, "token-secreto-de-gerencia");
  assert.deepEqual(calls, [[null, "", "/autorizar-gerencia"]]);
});
