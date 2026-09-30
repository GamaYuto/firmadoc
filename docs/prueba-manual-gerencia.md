# Prueba manual E2E: autorización de Gerencia

## Objetivo

Comprobar el recorrido completo de autorización sin crear pasos `FIRMAR` artificiales:

`solicitante crea solicitud -> gerente revisa y autoriza -> solicitante ve el resultado APROBAR`

## Requisitos

- PostgreSQL local disponible. Desde la raíz del repositorio:

  ```powershell
  docker compose up -d postgres
  ```

- Backend configurado en `backend/.env`, con Alfresco de laboratorio accesible y `FIRMADOC_GERENCIA_USER_ID` configurado con el usuario de Gerencia.
- El usuario introducido en el modal LAB debe coincidir con `FIRMADOC_GERENCIA_USER_ID`.
- Para iniciar el backend, desde `backend/`:

  ```powershell
  .\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  ```

- Abrir `http://127.0.0.1:8000`. FirmaDoc sirve las páginas y los recursos estáticos desde el backend.
- Usar un PDF de prueba autorizado en Alfresco. No incluir credenciales, cookies ni el token de Gerencia en capturas, tickets o logs.

## Crear la solicitud

1. Abrir el PDF de prueba desde Alfresco con FirmaDoc en `/iniciar?nodeId=<UUID-del-PDF>`.
2. En “¿Quién debe firmar?”, seleccionar **Autorizado por gerencia**.
3. Dibujar la posición del sello en el PDF y pulsar **Confirmar posición**.
4. Copiar el enlace de autorización generado y compartirlo únicamente con el gerente asignado.
5. Mantener abierta la página del solicitante para observar la actualización automática del estado.

## Revisar y autorizar

1. Abrir el enlace en otra ventana o perfil de navegador.
2. Después de cargar la página, confirmar que la dirección ya no contiene `#<token>`.
3. Si aparece el modal LAB, escribir el usuario exacto configurado en `FIRMADOC_GERENCIA_USER_ID` y autenticarse. La página debe continuar sin recargar ni reconstruir el enlace.
4. Verificar que se muestran documento, solicitante, fecha, gerente, cargo y preview PDF.
5. Pulsar **Autorizar** una sola vez.
6. Confirmar el mensaje de autorización y volver a la ventana del solicitante.

El token se captura del fragmento de URL una sola vez, se elimina de la barra de direcciones y permanece en memoria JavaScript. No volver a cargar la página del gerente después de quitar el fragmento. Si se pierde el contexto, abrir de nuevo el enlace original que conserva el solicitante.

## Resultado esperado

- Antes de autorizar: el estado del solicitante permanece pendiente; la página del gerente muestra el PDF y no presenta controles de firma ordinaria.
- Después de autorizar: el polling del solicitante informa **AUTORIZADO** y abre `/firmas/{firid}`.
- La pantalla indica **Autorizado por Gerencia**, presenta el resultado PDF con el sello en la posición elegida y muestra el estado **Listo para publicar** (`PENDIENTE_PUBLICACION`).
- El detalle de firma debe indicar `step_type: APROBAR` y `tipfir: INTERNA`. No debe aparecer **Revisar y firmar** ni **Paso de firma no encontrado**.
- No pulsar publicar durante esta prueba salvo que el objetivo incluya expresamente verificar escritura en Alfresco y `FIRMADOC_ALFRESCO_WRITE_ENABLED=true`. Con escritura deshabilitada, `PENDIENTE_PUBLICACION` es el resultado esperado.

## Diagnóstico rápido

- **401**: falta sesión LAB; completar el modal y comprobar que la carga se reintenta.
- **403**: el usuario LAB no coincide con el gerente asignado en la configuración.
- **404 “Solicitud de Gerencia no encontrada”**: revisar que GET de solicitud/documento y POST de autorización usen el mismo token raw original. El backend busca `SHA-256(token)` en `sesionqr.tokhas` y valida la relación `sesionqr -> docfirma -> docpart -> docpaso`, con `pastip=APROBAR`.
- **409 al crear**: existe un proceso activo para el mismo documento y versión; continuar ese proceso o cancelarlo por la ruta autorizada antes de crear otro.
- **Resultado ya usado**: los tokens son de un solo uso; generar una nueva solicitud para repetir la autorización.

## Cancelar procesos de prueba

No borrar filas ni editar estados directamente en PostgreSQL. La cancelación oficial requiere una sesión autenticada con CSRF y un usuario propietario del proceso o administrador:

`POST /api/documentos/{docid}/cancelar`

Cuerpo JSON:

```json
{"motivo":"Limpieza antes de repetir la prueba manual E2E"}
```

La ruta deja auditoría, cancela firmas, sesiones QR pendientes, participantes y pasos asociados, y limpia artefactos temporales del proceso. Solo cancelar documentos de prueba identificados; los procesos `COMPLETADO`, `RECHAZADO` o ya `CANCELADO` no son cancelables.
