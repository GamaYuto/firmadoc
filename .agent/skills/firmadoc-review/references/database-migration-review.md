# Revision de base de datos y migraciones

## Convenciones

- Tablas: maximo 8 caracteres.
- Columnas: maximo 6 caracteres.
- Nombres de constraints e indices explicitos cuando el downgrade dependa de ellos.

## Integridad

- CHECK en PostgreSQL para estados, valores no vacios, hashes, MIME, limites y coordenadas.
- FK con politica de borrado explicita.
- Indices unicos parciales para reglas condicionales.
- Defaults criticos generados por base de datos cuando corresponda.

## Transacciones

- Confirmar commit unico para operaciones atomicas relacionadas.
- Confirmar rollback en cualquier fallo.
- Detectar commits internos que rompan la unidad transaccional.
- Confirmar que eventos obligatorios de auditoria formen parte de la misma transaccion.

## Alembic

- Revisar `revision` y `down_revision`.
- Confirmar upgrade y downgrade simetricos.
- No aceptar migraciones vacias o `create_all` en startup.
- Probar reversibilidad solo en base desechable.
- Inspeccionar constraints e indices reales despues de migrar.
