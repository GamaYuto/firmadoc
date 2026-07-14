# Revision de seguridad

## Secretos

- Buscar credenciales, tickets, claves, `.env` rastreados y Authorization en logs.
- Confirmar `.env.example` sin secretos reales.
- Confirmar permisos minimos del usuario tecnico de Alfresco.

## Entrada y salida

- Validar UUID, longitudes, enums y contenido JSONB.
- Sanitizar nombres en Content-Disposition.
- No permitir HTML, JavaScript o codigo en configuraciones.
- No devolver trazas, SQL, rutas locales ni respuestas internas.

## Identidad

- Tratar `X-FirmaDoc-User` como dato no confiable y provisional.
- No atribuir valor probatorio a ese encabezado.
- Separar operador asistido, firmante externo y usuario autenticado.

## Integridad documental

- Hash SHA-256 original y final.
- Version exacta antes de firmar.
- Control de doble proceso y doble firma.
- Auditoria inmutable por flujo ordinario.
- No guardar imagen reutilizable de firma de directivos.
