# Arquitectura y Modelo Conceptual de Firma

**Documento maestro completo**: `docs/architecture/signature-architecture.md`

## Principios Obligatorios
- Alfresco es el repositorio oficial.
- FirmaDoc administra flujos, pero no guarda permanentemente el PDF.
- Las firmas pertenecen a una versión exacta (`nodeId`, `versión` y `hash` original).
- **Prohibición**: Está absolutamente prohibido inventar o agregar transiciones, estados o tipos de firma que no estén explícitamente detallados en las matrices formales.
- Las operaciones críticas deben ser idempotentes.

## Retención de Imagen y PDF
- La imagen cruda manuscrita **no se retiene permanentemente**. Se cifra temporalmente y se destruye tras incorporarse al PDF, conservando solo evidencia técnica y hashes en PostgreSQL.
- Los PDFs temporales post-firma se cifran y descartan. Solo se retienen efímeramente si hay fallo en Alfresco, con TTL estricto.

## Idempotencia de Publicación
- Utiliza la clave: `SHA-256(docid + nodid + verini + hasfir + "PUBLICACION_ALFRESCO")`.
- Exige verificación (`GET`) en Alfresco antes de cualquier reintento (`POST`), protegiendo el ecosistema frente a respuestas perdidas (timeout).

## Estados Permitidos
- **Proceso**: BORRADOR, PREPARADO, EN_CURSO, PENDIENTE_FIRMA, FIRMADO_PARCIAL, PENDIENTE_PUBLICACION, COMPLETADO, RECHAZADO, CANCELADO, ERROR_PUBLICACION. (COMPLETADO significa estrictamente: publicado en Alfresco).
- **Paso**: PENDIENTE, DISPONIBLE, EN_PROCESO, COMPLETADO, RECHAZADO, OMITIDO, CANCELADO, VENCIDO.
- **Firma**: PENDIENTE, CAPTURADA, CONFIRMADA, INCORPORADA, INVALIDADA.
- **Publicación**: PENDIENTE, EN_PROCESO, VERIFICANDO, PUBLICADA, ERROR, REINTENTO, REVISION_MANUAL.

## Tipos de Firma (Versión 1)
- **MANUSCRITA_CAPTURADA**: Para pacientes/externos.
- **ELECTRONICA_AUTENTICADA**: Para usuarios institucionales.

> **CRÍTICO:** Nunca se deben inventar transiciones no reversibles o estados intermedios. Todo modelo ORM y flujo funcional debe apegarse al documento maestro.
