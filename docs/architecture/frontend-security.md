# FirmaDoc frontend security notes

## Identidad de laboratorio

Las pantallas HTML actuales aceptan temporalmente el encabezado `X-FirmaDoc-User`
para pruebas locales y validacion manual. La interfaz lo marca como
`MODO LABORATORIO` y el backend lo centraliza en una unica dependencia.

Esta modalidad no es autenticacion real. En integracion, el navegador no debe
enviar identidad inventada por JavaScript: el encabezado debe eliminarse del
cliente y ser inyectado por un proxy o componente autenticado. Para despliegues
no laboratorio, `FIRMADOC_LAB_IDENTITY_ENABLED=false` bloquea estas rutas hasta
que exista una identidad confiable.

## CSRF

La arquitectura actual no tiene sesiones web ni token CSRF compartido entre
plantillas y API. Mientras la identidad sea de laboratorio por encabezado, no
se implementa una proteccion CSRF incompleta.

Cuando se habilite una sesion real, el contrato esperado es:

- token CSRF emitido por backend en una cookie o plantilla no sensible;
- envio del token en encabezado dedicado para operaciones mutables;
- validacion backend antes de crear participantes, posiciones o firmas;
- rechazo consistente con HTTP 403 cuando el token falte o no coincida.

## Frontend Dockerfile

`docker-compose.yml` no define un servicio frontend. FastAPI sirve:

- `frontend/templates` mediante `FileResponse`;
- `frontend/static` mediante `StaticFiles`.

`frontend/Dockerfile` se mantiene como stub documentado para evitar un archivo
vacio ambiguo, pero no forma parte del arranque actual.
