# Seguridad, integridad y auditoría

## Secretos

- Leer credenciales y claves desde variables de entorno o un gestor de secretos.
- Mantener `.env` fuera de Git y publicar solo `.env.example` sin valores reales.
- No imprimir secretos en logs, excepciones o respuestas HTTP.

## Autorización

- Aplicar permisos en backend, no solo ocultar botones.
- Separar roles de administrador, gestor, diligenciador, revisor, aprobador, firmante, operador asistido y auditor.
- Verificar que el usuario pueda actuar sobre el proceso y en la etapa actual.

## Integridad documental

- Calcular SHA-256 del PDF descargado y del PDF final.
- Verificar versión antes de firmar.
- No modificar contenido ajeno a los campos y evidencias previstos.
- No permitir acciones posteriores incompatibles con un documento cerrado.

## Firma manuscrita

- Validar que el trazo no esté vacío.
- Asociar la captura al proceso y sesión actuales.
- Insertarla en el PDF final y eliminar archivos temporales después de completar o fallar.
- No ofrecer una biblioteca de firmas reutilizables de personas.

## Firma interna

- Exigir sesión autenticada y autorización para la etapa.
- Registrar usuario, rol, fecha, IP, versión y hash.
- No considerar una simple imagen pegada como suficiente evidencia de la acción interna.

## Auditoría mínima

Registrar:

- Apertura, diligenciamiento, revisión, aprobación y rechazo.
- Inicio, cancelación y finalización de firma.
- Hash original y final.
- Versión inicial y final.
- Conflictos de versión.
- Errores de integración y reintentos.
- Usuario, firmante, IP, agente de usuario, fecha y resultado.

No guardar datos clínicos completos en el campo de detalle de auditoría. Registrar identificadores y contexto mínimo.

## Archivos temporales

- Usar rutas temporales no públicas.
- Generar nombres no predecibles.
- Limitar tamaño y validar MIME y estructura PDF.
- Eliminar temporales en bloques `finally` o mecanismos equivalentes.

## Respuestas y logs

- Mostrar mensajes útiles al usuario sin exponer trazas internas.
- Mantener correlación mediante identificador de operación.
- Sanitizar nombres de archivo y datos enviados por clientes.
