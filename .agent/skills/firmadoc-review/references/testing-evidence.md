# Revision de pruebas y evidencia

## Descubrimiento

Ejecutar y reportar:

```text
python -m pytest --collect-only -q
python -m pytest -v
python -m pytest -W default -v
```

Indicar pruebas por archivo, total, aprobadas, fallidas, omitidas y warnings.

## Cobertura funcional minima

- Camino exitoso.
- Entradas invalidas.
- Errores de dependencias externas.
- Restricciones de base.
- Rollback.
- Concurrencia e `IntegrityError`.
- Auditoria exitosa y fallida.
- Limpieza de temporales.
- Regresion de endpoints anteriores.

## Calidad de las pruebas

- Verificar que las aserciones prueben comportamiento, no solo codigo HTTP.
- Verificar que los mocks representen la API real.
- No contar varias aserciones como varias pruebas.
- No aceptar "cobertura completa" sin reporte de cobertura y umbral definido.
- Las pruebas SQLite no demuestran comportamiento PostgreSQL.

## Warnings

Capturar categoria, mensaje, archivo y linea literales. No inventar dependencia ni suprimir globalmente sin causa.
