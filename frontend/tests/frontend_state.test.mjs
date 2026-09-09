import assert from "node:assert/strict";
import test from "node:test";

import { buildDraftPayload, hasDirtyState } from "../static/js/preparation-state.js";
import { RenderSequencer } from "../static/js/render-sequencer.js";
import { isPngDataUrlWithinLimit, pngDataUrlBinarySize } from "../static/js/signature-utils.js";
import {
  getPublicationControlState,
  getPublicationErrorAlertType,
  isLocalResultReady,
  isPreparationEditableStatus,
  isSignatureAttemptActive,
} from "../static/js/workflow-state.js";

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

test("pngDataUrlBinarySize calcula bytes reales", () => {
  const dataUrl = `data:image/png;base64,${Buffer.from("12345").toString("base64")}`;
  assert.equal(pngDataUrlBinarySize(dataUrl), 5);
  assert.equal(isPngDataUrlWithinLimit(dataUrl, 5), true);
  assert.equal(isPngDataUrlWithinLimit(dataUrl, 4), false);
});
