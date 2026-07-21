---
name: firmadoc-development
description: desarrollo controlado del módulo firmadoc integrado con alfresco acs community 26.1. usar para analizar, implementar, corregir, probar o revisar funcionalidades del backend fastapi, integración rest con alfresco, procesamiento y firma de pdf, persistencia postgresql, auditoría, seguridad, flujos secuenciales y frontend web del proyecto firmadoc. aplicar cuando la tarea afecte archivos del repositorio firmadoc o requiera decisiones técnicas del módulo. no usar para trabajos ajenos al proyecto.
---

# Objetivo

Desarrollar FirmaDoc sin ampliar el alcance, sustituir tecnologías ni modificar archivos no relacionados.

FirmaDoc es una aplicación web independiente e integrada con Alfresco. Debe permitir preparar, diligenciar, revisar, aprobar y firmar secuencialmente documentos PDF clínicos y administrativos, guardar el resultado como nueva versión en Alfresco y conservar auditoría e integridad documental.

# Flujo obligatorio de trabajo

1. Leer la tarea y clasificarla: arquitectura, Alfresco, base de datos, PDF/firma, seguridad, flujo, frontend o pruebas.
2. Leer solo las referencias aplicables:
   - Arquitectura y alcance: `references/architecture.md`.
   - API y versionado de Alfresco: `references/alfresco-integration.md`.
   - Modelo y nombres de base de datos genéricos: `references/database-rules.md`.
   - Esquema físico, entidades, restricciones y ORM: `references/data-model-v1.md` (Solo cuando la tarea involucre modelos ORM, Alembic, Pydantic, consultas, transacciones, workflow, firma o publicación).
   - Seguridad, integridad y auditoría: `references/security-rules.md`.
   - Flujos, participantes, firmas y publicación: `references/signature-architecture.md` (Solo cuando la tarea involucre firmantes, participantes, flujos, pasos, firmas, evidencias, generación final de PDF o publicación en Alfresco).
   - Forma de inspeccionar, implementar y validar: `references/development-workflow.md`.
3. Inspeccionar los archivos existentes antes de proponer o modificar código.
4. Identificar el flujo actual, dependencias, pruebas y efectos laterales.
5. Presentar un plan breve cuando la tarea implique más de un archivo, cambie contratos o afecte datos.
6. Implementar el cambio mínimo suficiente.
7. Añadir o actualizar pruebas relacionadas.
8. Ejecutar validaciones disponibles. No declarar éxito sin resultados verificables.
9. Informar archivos modificados, pruebas ejecutadas, resultados, riesgos y pendientes reales.

# Reglas invariables

- Mantener FastAPI, Python 3.12, SQLAlchemy 2, Alembic, PostgreSQL, PDF.js, Signature Pad, PyMuPDF, Docker Compose y Alfresco REST API.
- Tratar Alfresco como repositorio documental oficial. No guardar copias permanentes del PDF en la base de datos.
- Vincular cada proceso documental al `nodeId` de Alfresco.
- Descargar y validar la versión vigente antes de firmar.
- Subir el PDF firmado como nueva versión. Nunca sobrescribir silenciosamente el original.
- Calcular SHA-256 del contenido original y final.
- Registrar auditoría de acciones críticas y errores.
- No guardar una imagen reutilizable de la firma manuscrita de un directivo.
- No usar credenciales reales, `admin` de Alfresco ni secretos en código o Git.
- Mantener tablas con máximo 8 caracteres y columnas con máximo 6 caracteres.
- No agregar dependencias sin necesidad, licencia compatible y justificación.
- No refactorizar módulos ajenos a la tarea.
- No implementar funciones excluidas de la versión 1 sin autorización explícita.

# Alcance de la versión 1

Incluir:

- Documentos PDF almacenados en Alfresco.
- Firma manuscrita capturada y firma electrónica de usuario interno autenticado.
- Campos configurables de texto, fecha, casilla, selección, identificación y firma.
- Plantillas y posicionamiento de campos por página.
- Flujos secuenciales de diligenciar, revisar, aprobar, firmar, rechazar y anular.
- Uno o varios firmantes en orden.
- Bandeja de pendientes.
- Versionado, metadatos, auditoría, hash e integridad.
- Uso clínico, administrativo, jurídico y corporativo.

Excluir salvo orden expresa:

- Certificados digitales y sellado de tiempo certificado.
- OTP, firma pública remota, biometría o reconocimiento facial.
- Firma masiva, delegación, flujos paralelos y aplicación móvil nativa.
- Edición o firma directa de DOCX, XLSX u otros formatos.
- Integraciones comerciales externas.

# Criterios de terminación

Una tarea solo está terminada cuando:

- El comportamiento solicitado está implementado.
- Las rutas de error relevantes están controladas.
- Las pruebas nuevas o existentes pasan, o se explica con evidencia por qué no pudieron ejecutarse.
- No se modificaron archivos fuera del alcance sin justificación.
- No se introdujeron secretos, credenciales o datos sensibles.
- El resumen final distingue hechos verificados de pendientes.

# Formato de respuesta final

Entregar siempre:

1. **Análisis realizado**
2. **Archivos modificados**
3. **Cambios aplicados**
4. **Pruebas ejecutadas y resultado**
5. **Riesgos o pendientes**
6. **Confirmación de alcance**
