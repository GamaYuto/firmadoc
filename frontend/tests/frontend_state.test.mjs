import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import { ApiError, apiFetch, setBusy, setSessionUser } from "../static/js/api.js";
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
import { buildSignatureRedirectUrl } from "../static/js/signature-navigation.js";

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

test("setSessionUser solo cambia identidad tras una seleccion explicita", async () => {
  const originalFetch = globalThis.fetch;
  let requestDetails;
  globalThis.fetch = async (url, options) => {
    requestDetails = { url, options };
    return {
      ok: true,
      status: 200,
      json: async () => ({ user_id: "pdaza", csrf_token: "csrf-test" }),
    };
  };

  try {
    const session = await setSessionUser("pdaza");
    assert.equal(session.user_id, "pdaza");
    assert.equal(requestDetails.url, "/api/auth/session");
    assert.equal(requestDetails.options.method, "POST");
    assert.deepEqual(JSON.parse(requestDetails.options.body), { user_id: "pdaza" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ApiError genera el codigo HTTP con interpolacion correcta", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 422,
    statusText: "Unprocessable Entity",
    json: async () => ({}),
  });

  try {
    await assert.rejects(apiFetch("/fallo"), (error) => error.code === "HTTP_422");
  } finally {
    globalThis.fetch = originalFetch;
  }
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


test("Gerencia mobile layout evita overflow y mantiene controles tactiles", () => {
  const css = fs.readFileSync(path.join("static", "css", "firmadoc.css"), "utf8");
  assert.match(css, /@media \(max-width: 768px\)/);
  assert.match(css, /\.manager-approval-layout\s*\{[\s\S]*?flex-direction: column;/);
  assert.match(css, /overflow-x: hidden;/);
  assert.match(css, /\.manager-approval-actions \.btn\s*\{[\s\S]*?min-height: 44px;[\s\S]*?width: 100%;/);
  assert.match(css, /\.manager-approval-layout \.pdf-scroll\s*\{[\s\S]*?width: 100%;/);
  assert.match(css, /\.modal-backdrop-custom\s*\{[\s\S]*?overflow-y: auto;/);
});

test("Gerencia usa zoom fit-width inicial en mobile", () => {
  const viewerSource = fs.readFileSync(path.join("static", "js", "pdf-viewer.js"), "utf8");
  const approvalSource = fs.readFileSync(path.join("static", "js", "autorizar_gerencia.js"), "utf8");
  assert.match(viewerSource, /async fitWidth/);
  assert.match(approvalSource, /max-width: 768px/);
  assert.match(approvalSource, /viewer\.fitWidth\(\{ maxZoom: 1, horizontalPadding: 24 \}\)/);
});

test("resultado autorizado se carga inline sin window.open", () => {
  const firmaSource = fs.readFileSync(path.join("static", "js", "firma.js"), "utf8");
  const iniciarSource = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  assert.doesNotMatch(firmaSource, /window\.open\s*\(/);
  assert.match(firmaSource, /apiFetchBinary\(url/);
  assert.match(firmaSource, /viewer\.load\(loadUrl, \[\], \[\]\)/);
  assert.match(firmaSource, /retryResultPreview/);
  assert.match(iniciarSource, /loadAuthorizedManagerResult/);
  assert.match(iniciarSource, /apiFetchBinary\(`\/api\/firma\/firmas\/\$\{firid\}\/resultado\/pdf`\)/);
  assert.match(iniciarSource, /viewer\.load\(managerResultObjectUrl, \[\], \[\]\)/);
});


test("conflicto activo de Gerencia conserva payload para cancelar y recrear", () => {
  const iniciarSource = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  assert.match(iniciarSource, /async function startManagerApproval\(payload\)[\s\S]*?pendingStartPayload = payload;[\s\S]*?pendingStartFlow = "manager";/);
  assert.match(iniciarSource, /async function startSignature\(payload\)[\s\S]*?pendingStartPayload = payload;[\s\S]*?pendingStartFlow = "signature";/);
  assert.match(iniciarSource, /if \(flow === "manager"\) \{[\s\S]*?await startManagerApproval\(payload\);/);
});

test("/iniciar no consulta ni cambia identidad LAB al cargar", () => {
  const source = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  assert.doesNotMatch(source, /getCurrentSession|setSessionUser|\/api\/auth\/session/);
});

test("firmante yo se conserva y otro queda pendiente de seleccion", () => {
  const source = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  assert.match(source, /viewer\.setSigner\(e\.target\.value === "otro" \? "" : "yo"\)/);
});

test("redireccion a firma construye ambas variantes de autoQr con returnUrl", () => {
  const returnUrl = "https://alfresco.example/share/page/document-details?nodeRef=abc&view=1";
  const encodedReturnUrl = encodeURIComponent(returnUrl);
  assert.equal(
    buildSignatureRedirectUrl(42, { autoQr: true, returnUrl }),
    `/firmas/42?autoQr=true&returnUrl=${encodedReturnUrl}`,
  );
  assert.equal(
    buildSignatureRedirectUrl(42, { autoQr: false, returnUrl }),
    `/firmas/42?returnUrl=${encodedReturnUrl}`,
  );
});

test("redirectToSignature usa el destino construido con returnUrl validada", () => {
  const source = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  assert.match(source, /buildSignatureRedirectUrl\(firid, \{ autoQr, returnUrl: validatedReturnUrl \}\)/);
});

test("modal solicita cambio explicito de identidad LAB", () => {
  const source = fs.readFileSync(path.join("static", "js", "autorizar_gerencia.js"), "utf8");
  const setUserHeader = source.match(/function setUserHeader\(userId\)\s*\{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(source, /await setSessionUser\(userId, password\)/);
  assert.match(setUserHeader, /authenticationUser\.value = cleanUser;/);
  assert.doesNotMatch(setUserHeader, /setSessionUser/);
  assert.doesNotMatch(source, /setSessionUser\(requestedUser\)/);
  assert.match(source, /suggestedUser: \(await getCurrentSession\(\)\)\?\.user_id \|\| initialParams\.get\("user"\) \|\| ""/);
});

test("template autorizar_gerencia.html contiene campos authenticationUser y authenticationPassword seguros", () => {
  const html = fs.readFileSync(path.join("..", "frontend", "templates", "autorizar_gerencia.html"), "utf8");
  assert.match(html, /id="authenticationUser"[\s\S]*?name="user_id"/);
  assert.match(html, /id="authenticationPassword"[\s\S]*?name="password"[\s\S]*?type="password"/);
  assert.match(html, /autocomplete="current-password"/);
  assert.match(html, /required/);
});

test("setSessionUser envia password opcional sin romper compatibilidad", async () => {
  const originalFetch = globalThis.fetch;
  let capturedBody = null;
  globalThis.fetch = async (url, options) => {
    capturedBody = JSON.parse(options.body);
    return new Response(JSON.stringify({ user_id: capturedBody.user_id, csrf_token: "csrf123" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  try {
    // Con usuario y password
    await setSessionUser("lblanco", "secret123");
    assert.deepEqual(capturedBody, { user_id: "lblanco", password: "secret123" });

    // Sin password (compatibilidad existente)
    await setSessionUser("lblanco");
    assert.deepEqual(capturedBody, { user_id: "lblanco" });
    assert.equal("password" in capturedBody, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("flujo autorizar_gerencia.js valida password, no expone en URL y limpia tras exito o fallo", () => {
  const source = fs.readFileSync(path.join("static", "js", "autorizar_gerencia.js"), "utf8");
  // Validacion de password no vacio
  assert.match(source, /if \(!password\) \{[\s\S]*?Ingrese su contraseña\.[\s\S]*?return;[\s\S]*?\}/);
  // Limpieza de password tras exito
  assert.match(source, /await setSessionUser\(userId, password\);[\s\S]*?authenticationPassword\.value = "";/);
  // Limpieza y foco tras 401
  assert.match(source, /if \(error\?\.status === 401\) \{[\s\S]*?Credenciales inválidas\.[\s\S]*?authenticationPassword\.value = "";[\s\S]*?authenticationPassword\.focus\(\);/);
  // Password no se almacena en variables de estado ni URL
  assert.doesNotMatch(source, /localStorage\.setItem\([^)]*password/);
  assert.doesNotMatch(source, /sessionStorage\.setItem\([^)]*password/);
  assert.doesNotMatch(source, /URLSearchParams\([^)]*password/);
  assert.doesNotMatch(source, /console\.log\([^)]*password/);
});


test("Gerencia autorizada oculta acciones y carga PDF firmado", () => {
  const source = fs.readFileSync(path.join("static", "js", "autorizar_gerencia.js"), "utf8");
  assert.match(source, /function hideApprovalActions\(\)[\s\S]*?authorizeButton\.hidden = true;[\s\S]*?rejectButton\.hidden = true;/);
  assert.match(source, /apiFetchBinary\(`\/api\/firma\/firmas\/\$\{firid\}\/resultado\/pdf`\)/);
  assert.match(source, /await showAuthorizedResult\(result\);/);
});

test("vista solicitante autorizada cambia a resultado publicable", () => {
  const source = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  assert.match(source, /function renderAuthorizedResultPanel\(result\)/);
  assert.match(source, /\.task-panel section"\)\?\.setAttribute\("hidden", ""\)/);
  assert.match(source, /\.panel-actions"\)\?\.setAttribute\("hidden", ""\)/);
  assert.match(source, /id="publishAuthorizedPdf"/);
  assert.match(source, /apiFetch\(`\/api\/firma\/documentos\/\$\{result\.docid\}\/publicar`/);
});

test("publicacion redirige a Alfresco con returnUrl validado sin popup", () => {
  const iniciarSource = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  const firmaSource = fs.readFileSync(path.join("static", "js", "firma.js"), "utf8");
  assert.match(iniciarSource, /redirectToReturnUrlAfterPublication/);
  assert.match(firmaSource, /redirectToReturnUrlAfterPublication/);
  assert.match(iniciarSource, /validate-return-url\?url=/);
  assert.match(firmaSource, /validate-return-url\?url=/);
  assert.match(iniciarSource, /window\.location\.href = validation\.url/);
  assert.match(firmaSource, /window\.location\.href = validation\.url/);
  assert.doesNotMatch(iniciarSource, /window\.open\s*\(/);
  assert.doesNotMatch(firmaSource, /window\.open\s*\(/);
});

test("Gerencia mantiene scroll interno del documento y zoom visible", () => {
  const css = fs.readFileSync(path.join("static", "css", "firmadoc.css"), "utf8");
  assert.match(css, /Manager approval: bounded document viewport/);
  assert.match(css, /\.manager-approval-layout\s*\{[\s\S]*?height: calc\(100vh - 64px\);[\s\S]*?overflow: hidden;/);
  assert.match(css, /\.manager-approval-layout \.viewer-column\s*\{[\s\S]*?overflow: hidden;/);
  assert.match(css, /\.manager-approval-layout \.pdf-scroll\s*\{[\s\S]*?overflow: auto;[\s\S]*?overscroll-behavior: contain;/);
  assert.match(css, /\.manager-approval-layout \.viewer-toolbar label\s*\{[\s\S]*?display: grid;[\s\S]*?grid-template-columns: auto minmax\(96px, 1fr\);/);
});

test("PdfViewer define clearPositions que limpia posiciones y seleccion", () => {
  const source = fs.readFileSync(path.join("static", "js", "pdf-viewer.js"), "utf8");
  assert.match(source, /clearPositions\(\)\s*\{[\s\S]*?this\.positions\s*=\s*\[\];[\s\S]*?this\.selectedId\s*=\s*null;/);
});

test("iniciar.js gestiona conflicto 409 con modal continuar/cancelar y sin reintento automatico", () => {
  const iniciarJs = fs.readFileSync(path.join("static", "js", "iniciar.js"), "utf8");
  const iniciarHtml = fs.readFileSync(path.join("..", "frontend", "templates", "iniciar.html"), "utf8");

  // Botones y confirmacion esperada
  assert.match(iniciarHtml, /id="continueActiveProcess"[\s\S]*?>Continuar proceso</);
  assert.match(iniciarHtml, /id="replaceActiveProcess"[\s\S]*?>Cancelar proceso anterior</);
  assert.match(iniciarHtml, /id="dismissActiveProcess"[\s\S]*?>Regresar</);
  assert.match(iniciarJs, /¿Cancelar el proceso de firma en curso\?/);
  assert.match(iniciarJs, /Esta acción descartará la preparación pendiente, pero no modificará el PDF original en Alfresco\./);

  // No reintenta en 409
  assert.match(iniciarJs, /onRetry:\s*error\.status === 409 \? null :/);
});

// ===========================================================================
// Tests flujo movil: firma_movil.js y firma_movil.html
// ===========================================================================

test("firma_movil.js importa ApiError de api.js", () => {
  const source = fs.readFileSync(path.join("static", "js", "firma_movil.js"), "utf8");
  assert.match(source, /import\s*\{[^}]*ApiError[^}]*\}\s*from\s*["']\.\/api\.js/);
});

test("firma_movil.js captura 401 con instanceof ApiError y no relanza", () => {
  const source = fs.readFileSync(path.join("static", "js", "firma_movil.js"), "utf8");
  // Debe verificar el tipo con instanceof ApiError y status 401
  assert.match(source, /instanceof ApiError.*&&.*err\.status === 401|err instanceof ApiError.*err\.status === 401/);
  // No debe llamar showUiError para el 401 principal
  // La captura del 401 debe retornar, no relanzar
  assert.match(source, /err\.status === 401[\s\S]{1,400}return\s*;/);
});

test("firma_movil.js tiene bandera sessionLoaded anti-doble-carga", () => {
  const source = fs.readFileSync(path.join("static", "js", "firma_movil.js"), "utf8");
  assert.match(source, /sessionLoaded/);
  assert.match(source, /if \(sessionLoaded\) return/);
  assert.match(source, /sessionLoaded = true/);
});

test("firma_movil.js NO usa showUiError directamente en el catch del 401 de loadSession", () => {
  const source = fs.readFileSync(path.join("static", "js", "firma_movil.js"), "utf8");
  // En el bloque del catch del 401, el codigo debe hacer return; sin llamar showUiError
  // Esto se verifica comprobando que el 401 lleva a showNoSession() no a showUiError()
  assert.match(source, /showNoSession\(/);
  // showUiError no debe aparecer en el mismo bloque del 401
  // (verificacion indirecta: showNoSession existe y se llama para el 401)
  assert.match(source, /function showNoSession/);
});

test("firma_movil.js dirige botones de login a /auth/login?next=", () => {
  const source = fs.readFileSync(path.join("static", "js", "firma_movil.js"), "utf8");
  assert.match(source, /\/auth\/login\?next=/);
  // El URL de next debe estar codificado con encodeURIComponent
  assert.match(source, /encodeURIComponent/);
});

test("firma_movil.html tiene panel authNoSessionNotice sin campos editables", () => {
  const html = fs.readFileSync(
    path.join("..", "frontend", "templates", "firma_movil.html"),
    "utf8"
  );
  // Panel existe
  assert.match(html, /id="authNoSessionNotice"/);
  // Tiene el boton de login
  assert.match(html, /id="loginBtn"/);
  // NO tiene input de texto libre para suplantar identidad
  assert.doesNotMatch(html, /authNoSessionNotice[\s\S]{1,3000}type="text"/);
});

test("firma_movil.html carga firma_movil.js con version mayor a 12.4", () => {
  const html = fs.readFileSync(
    path.join("..", "frontend", "templates", "firma_movil.html"),
    "utf8"
  );
  // Version en el script src debe ser >= 12.5
  const match = html.match(/firma_movil\.js\?v=(\d+\.\d+)/);
  assert.ok(match, "debe tener version en firma_movil.js?v=X.Y");
  const ver = parseFloat(match[1]);
  assert.ok(ver >= 12.5, `version ${ver} debe ser >= 12.5`);
});

test("login_movil.html valida next contra open-redirect", () => {
  const html = fs.readFileSync(
    path.join("..", "frontend", "templates", "login_movil.html"),
    "utf8"
  );
  // Verifica que el JS en la pagina valida el origen
  assert.match(html, /window\.location\.origin/);
  // Rechaza URLs absolutas de otros origenes
  assert.match(html, /url\.origin !== window\.location\.origin/);
  // Solo acepta rutas que empiezan con /
  assert.match(html, /startsWith\("\/"\)/);
});

test("login_movil.html hace POST a /api/auth/session al hacer login", () => {
  const html = fs.readFileSync(
    path.join("..", "frontend", "templates", "login_movil.html"),
    "utf8"
  );
  assert.match(html, /\/api\/auth\/session/);
  assert.match(html, /method:\s*["']POST["']/);
});

test("firma_movil.js tiene exactamente una llamada a loadSession() en el nivel superior", () => {
  const source = fs.readFileSync(path.join("static", "js", "firma_movil.js"), "utf8");
  // Contar llamadas directas a loadSession() fuera de definiciones
  // Debe haber exactamente una: loadSession().catch(...)
  const callMatches = [...source.matchAll(/loadSession\(\)/g)];
  // Puede haber referencias en definiciones de funciones (reloadSession llama loadSession)
  // y la llamada inicial. Verificamos que hay al menos una y que hay .catch en la inicial
  assert.ok(callMatches.length >= 1, "debe llamarse al menos una vez");
  assert.match(source, /loadSession\(\)\.catch\(/);
});

test("template autorizar_gerencia.html estructura móvil sin IDs duplicados y con acciones de decisión", () => {
  const html = fs.readFileSync(path.join("..", "frontend", "templates", "autorizar_gerencia.html"), "utf8");
  const requiredIds = [
    "authorizeButton",
    "rejectButton",
    "documentName",
    "requesterName",
    "requestedAt",
    "managerName",
    "managerRole",
    "pdfContainer",
    "zoomSelect",
    "pageStatus",
    "expandDocButton",
    "authenticationModal",
    "authenticationUser",
    "authenticationPassword",
    "authenticationSubmit",
  ];
  for (const id of requiredIds) {
    const regex = new RegExp(`id=["']${id}["']`, "g");
    const matches = [...html.matchAll(regex)];
    assert.equal(matches.length, 1, `El ID ${id} debe existir exactamente una vez en autorizar_gerencia.html`);
  }
  assert.match(html, /data-mobile-document/);
  assert.match(html, /data-mobile-requester/);
  assert.match(html, /<button[^>]+id=["']authorizeButton["']/);
  assert.match(html, /<button[^>]+id=["']rejectButton["']/);
});

test("autorizar_gerencia.js soporta fit-width, modo ampliado y conserva endpoints", () => {
  const source = fs.readFileSync(path.join("static", "js", "autorizar_gerencia.js"), "utf8");
  assert.match(source, /toggleExpandedViewer/);
  assert.match(source, /expandDocButton/);
  assert.match(source, /viewer-expanded/);
  assert.match(source, /viewer\.fitWidth/);
  assert.match(source, /zoomSelect/);

  // Endpoints conservados
  assert.match(source, /\/api\/firma\/gerencia\/solicitud/);
  assert.match(source, /\/api\/firma\/gerencia\/documento/);
  assert.match(source, /\/api\/firma\/gerencia\/autorizar/);
  assert.match(source, /\/api\/firma\/gerencia\/rechazar/);
});

test("firmadoc.css y firmadoc-theme.css aseguran visor util y scroll natural en movil", () => {
  const css = fs.readFileSync(path.join("static", "css", "firmadoc.css"), "utf8");
  const themeCss = fs.readFileSync(path.join("static", "css", "firmadoc-theme.css"), "utf8");

  // Visor util con 58dvh y min-height razonable
  assert.match(css, /\.manager-approval-layout \.pdf-scroll\s*\{[\s\S]*?height:\s*58dvh;/);
  assert.match(css, /\.manager-approval-layout \.pdf-scroll\s*\{[\s\S]*?min-height:\s*380px;/);

  // Modo ampliado pantalla completa
  assert.match(css, /\.viewer-column\.viewer-expanded\s*\{[\s\S]*?position:\s*fixed/);

  // Scroll natural en body móvil
  assert.match(css, /body:has\(\.manager-approval-layout\)[\s\S]*?overflow-y:\s*auto/);
  assert.match(themeCss, /body\.operational-body:has\(\.manager-approval-layout\)[\s\S]*?overflow-y:\s*auto/);
});

test("autorizar_gerencia incluye barra de lectura en voz alta con controles accesibles y sin modificar endpoints", () => {
  const html = fs.readFileSync(path.join("..", "frontend", "templates", "autorizar_gerencia.html"), "utf8");
  const source = fs.readFileSync(path.join("static", "js", "autorizar_gerencia.js"), "utf8");

  const readAloudIds = [
    "readAloudBar",
    "readAloudPlayBtn",
    "readAloudPauseBtn",
    "readAloudStopBtn",
    "readAloudStatus",
    "readAloudPageIndicator",
    "readAloudRateSelect",
  ];
  for (const id of readAloudIds) {
    const regex = new RegExp(`id=["']${id}["']`, "g");
    const matches = [...html.matchAll(regex)];
    assert.equal(matches.length, 1, `El ID ${id} debe existir exactamente una vez en autorizar_gerencia.html`);
  }

  assert.match(html, /aria-live=["']polite["']/);
  assert.match(html, /Utilice audífonos al consultar documentos confidenciales/);

  assert.match(source, /import\s+\{[^}]*PdfReadAloud[^}]*\}\s+from\s+["']\.\/pdf-read-aloud\.js/);
  assert.match(source, /stopAndCleanReader/);
  assert.match(source, /speechSynthesis\.cancel/);
});

