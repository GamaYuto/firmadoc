# Resumen del Modelo de Datos V1 (FirmaDoc)

**Documento Maestro:** `docs/architecture/data-model-v1.md`

## Entidades Principales y Relaciones (DD-011 Separación Crítica)
- `flujodoc`: Definición versionada del flujo.
- `flupaso`: Definición abstracta de los pasos.
- `docfir`: Instancia del proceso documental. Congela la versión de flujo y plantilla.
- **`docpaso`:** Instancia en **ejecución** del paso. Controla concurrencia (`verlock`), estados reales y fechas. Vincula `docfir` con `flupaso`.
- `docpart`: Participantes **asignados** al `docpaso`. Un paso automático (ej. Publicar) no requiere participantes; un paso de revisión grupal puede tener múltiples.
- `docfirma`: Evidencia lógica de firma asignada al `docpaso` y `docpart`. Persiste inmutablemente el `nodeId`, `versión` y `hash` de lo firmado.
- `docevid`: Trazabilidad técnica inmutable.
- `docpubl`: Gestor de publicación idempotente.

## Constraints, Políticas e Idempotencia
- **ON DELETE RESTRICT:** Es mandatorio en todas las relaciones de auditoría y ejecución (Ej. `docpart -> docpaso`, `docfirma -> docpaso`, `docevid -> todo`).
- **Verlock:** `docpaso` implementa control de concurrencia optimista. Se incrementa en cada transición. 
- **Idempotencia:** La publicación exige la clave determinista `SHA-256(docid + nodid + verini + hasfir + "PUBLICACION_ALFRESCO")`.
- **Prohibición de Binarios:** Imágenes de firmas y PDFs no se almacenan como blobs en PostgreSQL.

> **Regla de Desarrollo:** No inventar transiciones de estado ni mezclar la entidad de asignación (`docpart`) con la de ejecución de paso (`docpaso`). Ambas tienen dominios de estado distintos.
