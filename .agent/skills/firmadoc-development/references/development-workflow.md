# Flujo de desarrollo y validación

## Antes de modificar

1. Leer los archivos completos directamente involucrados.
2. Buscar usos, rutas, modelos, esquemas y pruebas relacionadas.
3. Confirmar el comportamiento actual con evidencia del código.
4. Identificar si la tarea cambia API, esquema, estado, seguridad o integración.
5. No asumir que una carpeta vacía implica libertad para inventar arquitectura distinta.

## Durante la implementación

- Mantener capas: API, esquemas, servicios, repositorios/modelos y configuración.
- Mantener la integración de Alfresco fuera de controladores HTTP.
- Mantener procesamiento PDF fuera de servicios de persistencia.
- Usar tipado y validación con Pydantic.
- Manejar excepciones específicas y traducirlas en respuestas HTTP consistentes.
- Evitar funciones monolíticas y duplicación, sin refactorizaciones generales.

## Pruebas mínimas por área

### API

- Caso exitoso.
- Entrada inválida.
- Usuario no autorizado.
- Recurso inexistente.
- Error del servicio dependiente.

### Alfresco

- Mock de consulta y descarga.
- Timeout, 401/403, 404 y conflicto de versión.
- Subida y verificación de nueva versión.

### PDF

- PDF válido.
- Archivo no PDF o corrupto.
- Firma vacía.
- Inserción dentro de límites de página.
- Hash reproducible del contenido generado.

### Base de datos

- Restricciones, transiciones de estado y transacciones.
- Idempotencia de reintentos críticos.

## Comandos esperados

Usar los comandos disponibles en el repositorio. Cuando no existan todavía, preferir:

```bash
pytest
python -m compileall backend/app
```

Para Docker:

```bash
docker compose config
docker compose build
docker compose up -d
```

No ejecutar cambios destructivos, migraciones sobre producción ni limpieza de volúmenes sin autorización explícita.

## Resumen final obligatorio

- Análisis realizado.
- Archivos modificados.
- Cambios aplicados.
- Pruebas y resultados exactos.
- Riesgos o pendientes.
- Confirmación de que no se amplió el alcance.
