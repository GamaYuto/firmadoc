# Arquitectura y alcance de FirmaDoc

## Propósito

FirmaDoc es una aplicación externa y desacoplada de Alfresco Share. Alfresco conserva documentos, permisos, carpetas, metadatos y versiones. FirmaDoc administra preparación, diligenciamiento, aprobación, firma, generación del PDF final y auditoría.

## Stack fijo

- Backend: FastAPI sobre Python 3.12.
- Persistencia: PostgreSQL, SQLAlchemy 2 y Alembic.
- Frontend inicial: HTML, JavaScript y Bootstrap.
- Visor: PDF.js.
- Captura manuscrita: Signature Pad.
- Procesamiento: PyMuPDF.
- Repositorio: Alfresco ACS Community 26.1 mediante REST API.
- Despliegue: Docker Compose; proxy Traefik cuando corresponda.
- Pruebas: Pytest.

## Límites de responsabilidad

### Alfresco

- Conservar contenido original y firmado.
- Mantener historial de versiones.
- Aplicar permisos documentales.
- Exponer metadatos y contenido por API.

### FirmaDoc

- Mantener procesos, participantes, estados, campos y auditoría.
- Validar versión antes de una acción irreversible.
- Generar el PDF firmado.
- Subir una nueva versión a Alfresco.
- No convertirse en repositorio documental alterno.

## Estados iniciales

- BORRADOR
- PENDIENTE_DILIGENCIAR
- PENDIENTE_REVISAR
- PENDIENTE_APROBAR
- PENDIENTE_FIRMAR
- FIRMADO_PARCIAL
- FIRMADO
- RECHAZADO
- ANULADO
- ERROR

## Flujos de referencia

Clínico:

`PREPARADO -> DILIGENCIADO -> FIRMADO_PACIENTE -> CERRADO`

Administrativo:

`ELABORADO -> REVISADO -> APROBADO -> FIRMADO -> CERRADO`

## Regla de diseño

Implementar primero el circuito completo mínimo:

`nodeId -> metadatos -> descarga -> visualización -> firma -> PDF final -> hash -> nueva versión -> auditoría`.

No construir primero bandejas o editores visuales si el circuito documental todavía no está probado.
