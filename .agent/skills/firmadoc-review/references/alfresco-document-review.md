# Revision de Alfresco y documentos

## Lectura

- URL base fija por configuracion; nunca aceptar host arbitrario del cliente.
- Credenciales fuera del repositorio y logs.
- Timeouts, SSL y limites configurables.
- Validar UUID, nodo, tipo archivo, MIME, tamano real y magic bytes.
- Calcular SHA-256 sobre contenido completo.
- Descargar por bloques y usar temporales aleatorios cuando aplique.

## Escritura futura

- Comparar version actual con version inicial antes de firmar.
- Rechazar concurrencia o cambios posteriores.
- Subir como nueva version con comentario claro.
- No sobrescribir silenciosamente el original.
- Validar respuesta de Alfresco antes de cerrar proceso.
- Diseñar compensacion o estado ERROR si Alfresco y PostgreSQL no pueden compartir transaccion atomica.

## Temporales

- Eliminar en exito, excepcion y cancelacion.
- No registrar rutas locales.
- No usar nombre del documento como ruta temporal.
- No mantener sesion de base abierta durante envio lento.

## Auditoria

- Registrar nodeId, version, hash, tamano, accion y resultado.
- No registrar PDF, firma binaria, password, ticket o Authorization.
