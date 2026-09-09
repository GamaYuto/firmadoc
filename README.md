# FirmaDoc

## Descripción
FirmaDoc es una aplicación web independiente diseñada para el uso institucional. Su propósito es permitir preparar, diligenciar, revisar, aprobar y firmar documentos PDF aplicables a procesos clínicos, administrativos, jurídicos y corporativos. Se integra de forma completamente desacoplada con Alfresco ACS Community 26.1, manteniendo el repositorio documental externo intacto hasta el final del flujo.

## Estado Actual (V1 - Fase 1 ORM)
El backend implementa los modelos base y las definiciones físicas de la base de datos (PostgreSQL + SQLAlchemy + Alembic).

### Modelos Centrales
- **docfir**: Instancia maestra del proceso documental. Gestiona el ciclo de vida y hashes.
- **flujodoc** y **flupaso**: Definición abstracta, versionada e inmutable de los procesos lógicos y sus pasos (secuenciales). **Nota:** el motor de ejecución todavía no está implementado.
- **plantill** y **tplcamp**: Definición versionada de plantillas documentales con sus campos posicionales `(posx, posy, ancho, alto)`.
- **audifir**: Trazabilidad técnica inmutable de eventos (`FLU_CREA`, `DOC_CREA`, etc.).

**En desarrollo (no implementado aún):**
- Asignación de participantes (`docpart`).
- Captura de firma electrónica y evidencias (`docfirma`, `docevid`).
- Publicación idempotente hacia Alfresco (`docpubl`).
- Gestor de plantillas documentales (`plantill`).
- Definición y configuración de campos posicionados (`tplcamp`).
- Una suite con exactamente 105 pruebas automatizadas que cubren las operaciones mencionadas.

## Arquitectura
- **Repositorio Oficial**: Alfresco es el repositorio documental oficial del sistema.
- **Aplicación Externa**: FirmaDoc funciona como una aplicación externa desacoplada.
- **Persistencia**: PostgreSQL almacena metadatos, control de estados, plantillas, coordenadas y auditoría.
- **Manejo de PDFs**: Los documentos PDF nunca se almacenan permanentemente en PostgreSQL.
- **Descargas Efímeras**: Las descargas de Alfresco se procesan utilizando archivos temporales efímeros para optimizar memoria, eliminándose inmediatamente después de usarse.
- **Solo Lectura**: Alfresco continúa operando estrictamente sin operaciones de escritura desde FirmaDoc.

## Tecnologías
Las tecnologías implementadas en el proyecto son:
- FastAPI
- PostgreSQL
- SQLAlchemy 2
- Alembic
- httpx
- Pydantic v2
- Pytest
- Docker Compose

*Tecnologías previstas para futuras fases:* PDF.js, Signature Pad, PyMuPDF, Editor visual, Firma manuscrita, Escritura de versiones en Alfresco y Autenticación institucional.

## Configuración
### Requisitos previos
- Docker y Docker Compose (para despliegue local de infraestructura).
- Python 3.12 (para ejecución de entorno local y pruebas).

### Variables de entorno
Copiar el archivo `.env.example` a `.env` en la raíz (opcional para Docker).
**Para el entorno local del backend**: crear el archivo `backend/.env` con los valores correspondientes. **Importante:** Está estrictamente prohibido versionar secretos, credenciales o el archivo `.env` en el control de versiones.

**PostgreSQL**:
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`
- `DATABASE_URL` (Ej: `postgresql+psycopg://user:pass@localhost:5432/dbname`)

**Alfresco**:
- `ALFRESCO_BASE_URL` (Ej: `http://localhost:8080`)
- `ALFRESCO_API_URL` (Ej: `/alfresco/api/-default-/public/alfresco/versions/1`)
- `ALFRESCO_USER` / `ALFRESCO_PASSWORD` (Usuario técnico con permisos de solo lectura)
- `ALFRESCO_TIMEOUT_SECONDS` (Timeout)
- `ALFRESCO_VERIFY_SSL` (Validación de certificados HTTPS)
- `ALFRESCO_MAX_DOWNLOAD_MB` (Límite estricto de tamaño de descarga para proteger memoria)

### Docker Compose (Ejecución local)
Para levantar la base de datos de PostgreSQL:
```bash
docker compose up -d postgres
```
Para detener los contenedores:
```bash
docker compose stop
```

## Migraciones
El control de versiones de base de datos se maneja con Alembic. Comandos principales a ejecutar desde la carpeta `backend/`:
- **Aplicar todas las migraciones**: `alembic upgrade head`
- **Revertir la última migración**: `alembic downgrade -1`
- **Ver estado actual**: `alembic current`
- **Ver historial**: `alembic history`

> **ADVERTENCIA**: Está prohibido editar migraciones de Alembic una vez han sido aplicadas y compartidas en el repositorio.

## API actual
A continuación, se documentan todos los endpoints existentes:

### Health
**Propósito:** Verifica el estado general de la aplicación y la conexión a la base de datos.
- `GET /api/health`
  - **Encabezados:** Ninguno.
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/health`
  - **Respuesta esperada:** `{"status": "ok", "database": "online"}`
  - **Errores principales:** `503 Service Unavailable` si la base de datos no está disponible.

### Alfresco
**Propósito:** Interacción de solo lectura con Alfresco para extraer metadatos y descargar el contenido de nodos de forma efímera. Requiere usuario provisional en encabezado.
- `GET /api/alfresco/nodes/{node_id}`
  - **Propósito:** Retorna metadatos de un documento.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/alfresco/nodes/{node_id} -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** Metadatos JSON (nombre, id, is_file, mime_type, etc).
  - **Errores principales:** `404 Not Found`, `403 Forbidden`, `401 Unauthorized`, `422 Unprocessable Entity` (no es archivo), `502 Bad Gateway`.

- `GET /api/alfresco/nodes/{node_id}/content`
  - **Propósito:** Descarga el contenido del archivo PDF.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/alfresco/nodes/{node_id}/content -H "X-FirmaDoc-User: admin" --output doc.pdf`
  - **Respuesta esperada:** Flujo binario con `Content-Type: application/pdf`.
  - **Errores principales:** `415 Unsupported Media Type` (no es PDF), `413 Payload Too Large` (excede tamaño permitido), errores de autenticación o acceso.

### Documentos
**Propósito:** Inicia y administra procesos de firma sobre documentos alojados en Alfresco sin afectar el origen (solo lectura local).
- `POST /api/documentos/iniciar`
  - **Propósito:** Inicia un proceso documental a partir de un nodo de Alfresco.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/documentos/iniciar -H "X-FirmaDoc-User: admin" -H "Content-Type: application/json" -d '{"node_id": "uuid"}'`
  - **Respuesta esperada:** JSON con información del proceso `docfir` creado.
  - **Errores principales:** `409 Conflict` (proceso ya existe), `400 Bad Request`.

- `POST /api/documentos/{docid}/cancelar`
  - **Propósito:** Cancela un proceso documental en curso.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/documentos/{docid}/cancelar -H "X-FirmaDoc-User: admin" -H "Content-Type: application/json" -d '{"motivo": "Cancelado por error"}'`
  - **Respuesta esperada:** Proceso documental en estado `CANCELADO`.
  - **Errores principales:** `404 Not Found`, `400 Bad Request` (transición inválida).

- `GET /api/documentos/{docid}`
  - **Propósito:** Obtener detalle de un proceso documental.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/documentos/{docid} -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON del `docfir`.
  - **Errores principales:** `404 Not Found`.

- `GET /api/documentos`
  - **Propósito:** Listar procesos documentales paginados y con filtros.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/documentos?estado=BORRADOR&limit=50 -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** Listado de procesos con contador total.

### Plantillas
**Propósito:** Gestionar las plantillas documentales (crear, actualizar, activar, inactivar, crear versión).
- `POST /api/plantillas`
  - **Propósito:** Crear una nueva plantilla en estado `BORRADOR`.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/plantillas -H "X-FirmaDoc-User: admin" -H "Content-Type: application/json" -d '{"tplcod": "TPL-01", "tplnom": "Plantilla", "numpag": 1}'`
  - **Respuesta esperada:** JSON de plantilla.
  - **Errores principales:** `409 Conflict`.

- `GET /api/plantillas`
  - **Propósito:** Listar plantillas paginadas.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/plantillas -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON con lista y total.

- `GET /api/plantillas/{tplid}`
  - **Propósito:** Obtener plantilla por su id.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/plantillas/{tplid} -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON de la plantilla.
  - **Errores principales:** `404 Not Found`.

- `PATCH /api/plantillas/{tplid}`
  - **Propósito:** Modificar nombre, páginas u otros atributos de una plantilla borrador.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X PATCH http://localhost:8000/api/plantillas/{tplid} -H "X-FirmaDoc-User: admin" -H "Content-Type: application/json" -d '{"tplnom": "Nuevo Nombre"}'`
  - **Respuesta esperada:** JSON de plantilla actualizada.
  - **Errores principales:** `400 Bad Request` (no se puede editar plantilla activa), `404 Not Found`.

- `POST /api/plantillas/{tplid}/versiones`
  - **Propósito:** Generar una nueva versión en `BORRADOR` copiando los campos activos de una plantilla activa existente.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/plantillas/{tplid}/versiones -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON de la nueva plantilla.
  - **Errores principales:** `400 Bad Request` (estado inválido para clonar), `404 Not Found`.

- `POST /api/plantillas/{tplid}/activar`
  - **Propósito:** Pasar plantilla a estado `ACTIVA`. Si existe otra activa con mismo código, la inactiva.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/plantillas/{tplid}/activar -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON de plantilla.
  - **Errores principales:** `404 Not Found`.

- `POST /api/plantillas/{tplid}/inactivar`
  - **Propósito:** Pasar plantilla a estado `INACTIVA`.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/plantillas/{tplid}/inactivar -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON de plantilla.
  - **Errores principales:** `400 Bad Request` (si está borrador), `404 Not Found`.

### Campos
**Propósito:** Gestionar campos asociados a una plantilla específica.
- `POST /api/plantillas/{tplid}/campos`
  - **Propósito:** Añadir un campo a la plantilla (solo borradores).
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/plantillas/{tplid}/campos -H "X-FirmaDoc-User: admin" -H "Content-Type: application/json" -d '{"camcod":"C1", "camnom":"Campo", "camtip":"TEXTO", "pagina":1, "posx":0.1, "posy":0.1, "ancho":0.2, "alto":0.05, "orden":1}'`
  - **Respuesta esperada:** JSON del campo creado.
  - **Errores principales:** `400 Bad Request` (coordenadas o configuración inválida), `409 Conflict`.

- `GET /api/plantillas/{tplid}/campos`
  - **Propósito:** Listar todos los campos de una plantilla.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/plantillas/{tplid}/campos -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON con lista de campos.
  
- `GET /api/plantillas/{tplid}/campos/{camid}`
  - **Propósito:** Obtener detalle de un campo.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X GET http://localhost:8000/api/plantillas/{tplid}/campos/{camid} -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON del campo.
  - **Errores principales:** `404 Not Found`.

- `PATCH /api/plantillas/{tplid}/campos/{camid}`
  - **Propósito:** Modificar coordenadas, configuración u otros parámetros de un campo.
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X PATCH http://localhost:8000/api/plantillas/{tplid}/campos/{camid} -H "X-FirmaDoc-User: admin" -H "Content-Type: application/json" -d '{"posx":0.2}'`
  - **Respuesta esperada:** JSON del campo actualizado.
  - **Errores principales:** `400 Bad Request`, `404 Not Found`.

- `POST /api/plantillas/{tplid}/campos/{camid}/inactivar`
  - **Propósito:** Inactivar un campo (borrado lógico).
  - **Encabezados:** `X-FirmaDoc-User: username`
  - **Ejemplo curl:** `curl -X POST http://localhost:8000/api/plantillas/{tplid}/campos/{camid}/inactivar -H "X-FirmaDoc-User: admin"`
  - **Respuesta esperada:** JSON del campo inactivo.
  - **Errores principales:** `404 Not Found`.

## Plantillas
La gestión de plantillas sigue reglas estrictas:
- **Estados:** Una plantilla puede estar en `BORRADOR` (editable), `ACTIVA` (solo lectura, en uso) o `INACTIVA`.
- **Versionado:** Si se requiere un cambio en una plantilla activa, se debe generar una nueva versión (se crea en borrador copiando todos los campos activos).
- **Unicidad:** Solo puede existir una (1) versión activa por cada código de plantilla (`tplcod`).
- **Coordenadas normalizadas:** Todos los campos emplean dimensiones (X, Y, Ancho, Alto) normalizadas como porcentajes (`0.000` a `1.000`), evitando depender de píxeles absolutos (Ej. `posx + ancho <= 1`).
- **Tipos de campo:** Soporta TEXTO, TEXLAR, FECHA, CASILLA, OPCION, NOMBRE, TIPDOC, NUMDOC, FIRMA y SELLO.
- **Configuración:** Atributos extras por tipo de campo se validan estrictamente mediante Pydantic y se persisten en una columna PostgreSQL de tipo JSONB.
- **Inmutabilidad:** Las plantillas activas no se pueden modificar directamente para preservar la consistencia documental.

## Pruebas
La funcionalidad central cuenta con pruebas exhaustivas. Se ejecutan con los comandos:
```bash
python -m pytest --collect-only -q
python -m pytest -v
```
Se reportan los siguientes totales comprobados:
- **Total exacto de pruebas:** 105
- `test_alfresco.py`: 17 pruebas
- `test_audit.py`: 4 pruebas
- `test_documentos.py`: 17 pruebas
- `test_health.py`: 1 prueba
- `test_plantillas.py`: 36 pruebas
- `test_flujos.py`: 18 pruebas
- `test_docpaso.py`: 12 pruebas
- **Warnings:** Se emite un warning literal conocido de la librería FastAPI: `StarletteDeprecationWarning: Using 'httpx' with 'starlette.testclient' is deprecated; install 'httpx2' instead.`.

## Limitaciones actuales
- Alfresco sigue en modo de solo lectura (no se escriben datos).
- No hay funcionalidad de firma implementada.
- No hay editor visual desarrollado.
- No hay frontend funcional de ningún tipo.
- El header `X-FirmaDoc-User` es provisional para desarrollo y pruebas.
- No existe autenticación institucional.
- No existe firma digital certificada.
- No se suben nuevas versiones de documentos a Alfresco.
- No existe el flujo secuencial de firmantes todavía.

## Próximas fases
- Diseño y construcción de la arquitectura documental de firma.
- Implementación del flujo de firmas, pasos, participantes y firmantes.
- Soporte para evidencias de audito- Crear una plantilla base.
- Configurar campos.
- Crear definición de flujo (en memoria/schema, sin ejecución).
- Crear un borrador `docfir`.
- (Próximamente: iniciar flujo, notificar a participantes, recolectar firmas y publicar). del PDF final sellado y firmado.
- Publicación de documentos firmados como nueva versión en Alfresco.

```
firmadoc
├─ .agent
│  └─ skills
│     ├─ firmadoc-development
│     │  ├─ agents
│     │  │  └─ openai.yaml
│     │  ├─ references
│     │  │  ├─ alfresco-integration.md
│     │  │  ├─ architecture.md
│     │  │  ├─ data-model-v1.md
│     │  │  ├─ database-rules.md
│     │  │  ├─ development-workflow.md
│     │  │  ├─ security-rules.md
│     │  │  └─ signature-architecture.md
│     │  └─ SKILL.md
│     └─ firmadoc-review
│        ├─ agents
│        │  └─ openai.yaml
│        ├─ references
│        │  ├─ alfresco-document-review.md
│        │  ├─ database-migration-review.md
│        │  ├─ review-checklist.md
│        │  ├─ review-report.md
│        │  ├─ security-audit.md
│        │  └─ testing-evidence.md
│        └─ SKILL.md
├─ backend
│  ├─ alembic
│  │  ├─ env.py
│  │  ├─ README
│  │  ├─ script.py.mako
│  │  └─ versions
│  │     ├─ 27c5eb2328af_implementar_docfirma_y_firpos.py
│  │     ├─ 60952ef5bba7_crear_modelo_docfir.py
│  │     ├─ 62215c760c16_initial_audifir_model.py
│  │     ├─ 82dc189cf4b8_implement_docpaso.py
│  │     ├─ f58b81150be2_implementar_flujodoc_y_flupaso_docfir_.py
│  │     ├─ f61284a6c891_implementar_docpart.py
│  │     └─ f761788400b6_crear_modelos_de_plantillas_y_campos.py
│  ├─ alembic.ini
│  ├─ app
│  │  ├─ api
│  │  │  ├─ alfresco.py
│  │  │  ├─ api_router.py
│  │  │  ├─ documentos.py
│  │  │  ├─ health.py
│  │  │  ├─ plantillas.py
│  │  │  └─ __init__.py
│  │  ├─ core
│  │  │  ├─ config.py
│  │  │  ├─ database.py
│  │  │  ├─ exceptions.py
│  │  │  └─ identity.py
│  │  ├─ crud
│  │  │  ├─ crud_audifir.py
│  │  │  ├─ crud_docfir.py
│  │  │  ├─ crud_docfirma.py
│  │  │  ├─ crud_docpart.py
│  │  │  ├─ crud_docpaso.py
│  │  │  ├─ crud_flujodoc.py
│  │  │  ├─ crud_flupaso.py
│  │  │  ├─ crud_plantill.py
│  │  │  ├─ crud_tplcamp.py
│  │  │  └─ __init__.py
│  │  ├─ main.py
│  │  ├─ models
│  │  │  ├─ audifir.py
│  │  │  ├─ docfir.py
│  │  │  ├─ docfirma.py
│  │  │  ├─ docpart.py
│  │  │  ├─ docpaso.py
│  │  │  ├─ firpos.py
│  │  │  ├─ flujodoc.py
│  │  │  ├─ flupaso.py
│  │  │  ├─ plantill.py
│  │  │  ├─ tplcamp.py
│  │  │  └─ __init__.py
│  │  ├─ schemas
│  │  │  ├─ alfresco.py
│  │  │  ├─ campo.py
│  │  │  ├─ docfirma.py
│  │  │  ├─ docpart.py
│  │  │  ├─ docpaso.py
│  │  │  ├─ documento.py
│  │  │  ├─ flujo.py
│  │  │  ├─ pdf_signature.py
│  │  │  └─ plantilla.py
│  │  ├─ services
│  │  │  ├─ alfresco_client.py
│  │  │  ├─ alfresco_service.py
│  │  │  ├─ audit_service.py
│  │  │  ├─ document_service.py
│  │  │  ├─ flow_service.py
│  │  │  ├─ participant_service.py
│  │  │  ├─ pdf_service.py
│  │  │  ├─ pdf_signature_service.py
│  │  │  ├─ pdf_validation_service.py
│  │  │  ├─ signature_exceptions.py
│  │  │  ├─ signature_image_service.py
│  │  │  ├─ signature_service.py
│  │  │  ├─ step_service.py
│  │  │  ├─ template_service.py
│  │  │  └─ temporary_artifact_service.py
│  │  └─ __init__.py
│  ├─ Dockerfile
│  ├─ fix.py
│  ├─ pytest.ini
│  ├─ requirements.txt
│  └─ tests
│     ├─ conftest.py
│     ├─ test_alfresco.py
│     ├─ test_alfresco_client.py
│     ├─ test_audit.py
│     ├─ test_docfirma.py
│     ├─ test_docfirma_concurrency.py
│     ├─ test_docfirma_migration.py
│     ├─ test_docpart.py
│     ├─ test_docpaso.py
│     ├─ test_documentos.py
│     ├─ test_flujos.py
│     ├─ test_health.py
│     ├─ test_pdf_security.py
│     ├─ test_pdf_signature.py
│     ├─ test_pdf_signature_integration.py
│     ├─ test_pdf_temporary_artifacts.py
│     ├─ test_pdf_validation.py
│     ├─ test_plantillas.py
│     ├─ test_signature_publication.py
│     └─ test_signature_reconciliation.py
├─ database
│  ├─ init.sql
│  └─ migrations
├─ docker-compose.yml
├─ docs
│  └─ architecture
│     ├─ data-model-v1.md
│     ├─ docfirma-design.md
│     ├─ docpart-design.md
│     └─ signature-architecture.md
├─ frontend
│  ├─ Dockerfile
│  ├─ static
│  │  ├─ css
│  │  ├─ js
│  │  └─ vendor
│  └─ templates
└─ README.md

```