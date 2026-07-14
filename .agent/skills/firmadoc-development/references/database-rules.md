# Reglas de base de datos

## Convenciones de nombres y diseño

### Tablas

- Usar nombres descriptivos en minúsculas.
- Preferir nombres de máximo 8 caracteres.
- Mantener los nombres existentes cuando ya hayan sido publicados mediante migraciones.
- No renombrar tablas únicamente para reducir su longitud si no existe un beneficio funcional.

### Columnas

- Usar nombres cortos, descriptivos y consistentes.
- Preferir nombres de máximo 8 caracteres.
- Permitir excepciones justificadas cuando mejoren claramente la legibilidad.
- No abreviar nombres hasta volver ambiguo su significado.
- No renombrar columnas existentes exclusivamente para cumplir una longitud arbitraria.
- Las reglas de longitud aplican principalmente a nuevos desarrollos y no de forma retroactiva.

### Diseño e integridad

- Usar claves primarias explícitas.
- Definir claves foráneas, índices y restricciones de unicidad donde correspondan.
- Usar fechas con zona horaria para eventos de auditoría cuando la arquitectura lo permita.
- No almacenar PDFs completos ni secretos.
- No almacenar una firma reutilizable como activo permanente del firmante.
- Toda excepción de nombres debe ser comprensible, consistente y estar justificada.

## Entidades iniciales

### `docfir`

Proceso documental vinculado a Alfresco.

Campos orientativos:

- `docid`: clave primaria.
- `nodid`: `nodeId` de Alfresco.
- `docnom`: nombre del documento.
- `tplid`: plantilla.
- `estado`: estado del proceso.
- `verini`: versión inicial.
- `verfin`: versión final.
- `hasori`: hash original.
- `hasfir`: hash final.
- `usrcre`, `feccre`, `usrmod`, `fecmod`.

### `firmant`

Participantes y orden de firma.

- `firid`, `docid`.
- `tipfir`, `nomfir`, `tipdoc`, `numdoc`.
- `usrid`, `rolfir`, `orden`, `estado`, `fecfir`, `iporig`.

### `audifir`

Eventos de auditoría.

- `audid`, `docid`, `evento`, `usrid`, `iporig`, `detalle`, `fecope`.

### `plantill` y `tplcamp`

Plantillas documentales versionadas y campos posicionados sobre el PDF.

- `plantill` representa el encabezado, código, versión, páginas y estado de la plantilla.
- `tplcamp` representa campos configurables, tipo, página, coordenadas normalizadas, dimensiones, orden y configuración JSONB.
- Solo una versión activa puede existir por código de plantilla.
- Las plantillas activas no deben modificarse directamente; los cambios requieren una nueva versión.

## Migraciones

- Toda modificación de esquema debe usar Alembic.
- No editar migraciones ya aplicadas en entornos compartidos.
- Incluir `upgrade` y `downgrade` coherentes.
- Revisar claridad, consistencia y longitud recomendada de nombres antes de generar la migración.
- Agregar índices para consultas por `nodid`, `estado`, usuario asignado y fechas cuando estén justificadas.

## Transacciones

- Mantener consistencia entre base de datos y Alfresco con estados intermedios y operaciones idempotentes.
- No marcar un proceso como `FIRMADO` antes de verificar la nueva versión en Alfresco.
- Si la carga falla, conservar evidencia del intento y permitir reintento seguro sin duplicar versiones.
