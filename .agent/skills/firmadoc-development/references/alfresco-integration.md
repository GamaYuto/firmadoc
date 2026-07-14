# Integración con Alfresco

## Principios

- Usar la API REST pública de Alfresco.
- Identificar documentos mediante `nodeId`.
- Centralizar llamadas en un cliente o servicio dedicado.
- Aplicar tiempos de espera, control de errores y mensajes sanitizados.
- No exponer credenciales de servicio al navegador.
- Usar usuario técnico con permisos mínimos en las carpetas autorizadas.

## Operaciones mínimas

1. Consultar nodo y metadatos.
2. Descargar contenido.
3. Consultar versión actual e historial.
4. Validar que el contenido sea PDF.
5. Subir el resultado como nueva versión.
6. Añadir comentario de versión.
7. Actualizar propiedades de firma autorizadas.

## Control de concurrencia

Al iniciar un proceso, guardar como mínimo:

- `nodeId`.
- Identificador o etiqueta de versión inicial.
- SHA-256 del contenido descargado.

Antes de firmar o finalizar:

1. Consultar nuevamente el nodo.
2. Comparar versión y, cuando sea necesario, hash.
3. Rechazar la operación si el documento cambió.
4. Registrar el conflicto en auditoría.

## Versionado

- Nunca reemplazar el original sin versionado.
- Usar nueva versión menor inicialmente, salvo regla funcional expresa.
- Incluir comentario reconocible, por ejemplo: `FirmaDoc: documento firmado, proceso <codigo>`.
- Verificar después de subir que Alfresco reporta la nueva versión.

## Metadatos sugeridos

No crear modelos personalizados sin una tarea específica. Cuando existan, considerar:

- Estado de firma.
- Código del proceso.
- Fecha de firma.
- Firmante principal.
- Tipo de firma.
- Hash final.
- Versión firmada.

## Fallos que deben manejarse

- 401/403: credenciales o permisos.
- 404: `nodeId` inexistente o fuera del alcance.
- 409/412: conflicto o concurrencia.
- 413: archivo demasiado grande.
- 5xx y timeout: indisponibilidad temporal.
- Respuesta inesperada o contenido no PDF.

No registrar contraseñas, tokens, cabeceras de autorización ni el contenido completo del documento en logs.
