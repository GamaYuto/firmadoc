# Diseño Arquitectónico Completo del Módulo docfirma

## 1. Estado del Diseño

**Estado:** DISEÑO CORREGIDO (Auditoría arquitectónica integrada, todas las decisiones cerradas).

## 2. Objetivo

Especificar el diseño completo y definitivo para el módulo `docfirma`, el cual orquesta la ejecución de intentos de firma sobre pasos de tipo `FIRMAR` en FirmaDoc. Define el modelo de persistencia, concurrencia, idempotencia, posiciones lógicas (`firpos`), auditoría y la frontera transaccional entre PostgreSQL y Alfresco.

## 3. Alcance

- Definición de tablas `docfirma` y `firpos` con constraints y convenciones de base de datos.
- Tipos de firma autorizados V1: código persistido `MANUSCRITA` (nombre funcional: Firma Manuscrita Capturada) y código persistido `INTERNA` (nombre funcional: Firma Electrónica Interna Institucional).
- Protocolo transaccional por fases entre PostgreSQL y Alfresco con revalidación previa obligatoria.
- Reconciliación idempotente por `opeid` con tratamiento diferenciado por número de coincidencias.
- Servicio central de finalización `finalize_verified_signature` compartido entre flujo normal y reconciliación.
- Matriz de estados, excepciones de dominio, riesgos y catálogo detallado de pruebas.

## 4. Exclusiones

No se incluye: certificados digitales PKI/X.509, sellado de tiempo certificado, biometría, firma masiva, OTP, delegaciones, `docevid`, `docpubl`, participantes externos sin identidad institucional ni almacenamiento permanente de imágenes o PDFs en PostgreSQL.

## 5. Terminología

- **docfirma:** Entidad que registra un intento concreto de firma.
- **firpos:** Posición(es) visual(es) congelada(s) de la marca gráfica sobre el PDF.
- **Firma Manuscrita Capturada:** Trazo gráfico capturado en tiempo real en frontend y procesado de forma efímera. Código persistido en `tipfir`: `MANUSCRITA`.
- **Firma Electrónica Interna Institucional:** Firma realizada por un usuario autenticado basada en sus credenciales, rol e intención declarada congelada. No equivale a firma digital certificada, PKI ni X.509. Código persistido en `tipfir`: `INTERNA`.
- **opeid:** Identificador UUID v4 único de la operación de firma, generado exclusivamente por el backend.
- **finalize_verified_signature:** Servicio interno que consolida la finalización de una firma verificada, usado tanto en flujo normal como en reconciliación.

## 6. Entidades Relacionadas y Jerarquía

- `docfir` (1) -> (N) `docpaso` (1) -> (N) `docpart` (1) -> (N) `docfirma` (1) -> (N) `firpos`
- `audifir`: Registro de auditoría inmutable de eventos de firma (`enttip = 'FIRMA'`).

## 7. Modelo de Datos docfirma

**Tabla `docfirma`**

- `firid` (BigInteger, PK, autoincrement)
- `docid` (BigInteger, FK a `docfir.docid`, ON DELETE RESTRICT, NOT NULL)
- `parid` (BigInteger, FK a `docpart.parid`, ON DELETE RESTRICT, NOT NULL)
- `opeid` (UUID nativo PostgreSQL, UNIQUE, NOT NULL) — Generado exclusivamente por el backend. No aceptar UUID del frontend.
- `secuen` (Integer, NOT NULL) — Secuencia global por documento (`docid`). `secuen > 0`.
- `intnum` (Integer, NOT NULL) — Intento incremental por participante (`parid`). `intnum > 0`.
- `tipfir` (String(30), NOT NULL) — `MANUSCRITA` o `INTERNA`.
- `estado` (String(20), NOT NULL) — `INICIADA`, `GENERADA`, `SUBIENDO`, `CARGADA`, `VERIFICANDO`, `COMPLETADA`, `CONFLICTO`, `FALLIDA`, `CANCELADA`.
- `verori` (String(20), NOT NULL) — Versión fuente descargada de Alfresco.
- `hasori` (String(64), NOT NULL) — Hash SHA-256 (hex 64 minúsculas) del PDF fuente.
- `verfin` (String(20), Nullable) — Versión resultante asignada por Alfresco.
- `hasfin` (String(64), Nullable) — Hash SHA-256 (hex 64 minúsculas) del PDF firmado.
- `errcod` (String(40), Nullable) — Código sanitizado de error.
- `motivo` (String(500), Nullable) — Motivo de cancelación sanitizado. No sustituye `errcod`. No almacena trazas ni datos clínicos.
- `result` (JSONB, Nullable) — Metadata operativa bajo schema cerrado (ver sección 8).
- `fecini` (Timestamptz, NOT NULL) — Fecha/hora de inicio.
- `fecfin` (Timestamptz, Nullable) — Fecha/hora de finalización.
- `revnum` (Integer, NOT NULL, default 1) — Control optimista de concurrencia. `revnum >= 1`.
- `usrcre` (String(60), NOT NULL)
- `feccre` (Timestamptz, NOT NULL, server_default=now())
- `usrmod` (String(60), Nullable)
- `fecmod` (Timestamptz, NOT NULL, server_default=now())

**Reglas de fecmod:**
- se inicializa con el mismo now transaccional de feccre;
- se actualiza en cada transición;
- se actualiza al cambiar result;
- se actualiza al incrementar revnum;
- nunca queda null;
- el retorno idempotente sobre COMPLETADA no lo modifica.

## 8. Contrato JSON Schema de result

El campo `result` (`JSONB`) debe ceñirse estrictamente al siguiente schema Pydantic versionado con `extra = forbid`:

### Ejemplo de publicación

```json
{
  "schema_ver": 1,
  "fase": "PUBLICACION",
  "remcod": 201,
  "remmsg": "Created",
  "recint": 0,
  "verchk": false,
  "haschk": false,
  "flags": ["ALFRESCO_OK"]
}
```

### Ejemplo de verificación exitosa

```json
{
  "schema_ver": 1,
  "fase": "VERIFICACION",
  "remcod": 200,
  "remmsg": "Verified",
  "recint": 0,
  "verchk": true,
  "haschk": true,
  "flags": ["ALFRESCO_OK", "HASH_MATCH"]
}
```

No usar un estado de docfirma como valor de fase.

Tipos y valores permitidos:

- `schema_ver`: entero, valor fijo `1`.
- `fase`: enum de fases conocidas (`RESERVA`, `GENERACION`, `REVALIDACION`, `PUBLICACION`, `VERIFICACION`, `FINALIZACION`, `RECONCILIACION`).
- `remcod`: entero nullable (código HTTP de respuesta remota).
- `remmsg`: texto sanitizado, máximo 200 caracteres (mensaje de respuesta remota).
- `recint`: entero >= 0 (número de intentos de reconciliación realizados).
- `verchk`: boolean (verificación de versión superada).
- `haschk`: boolean (verificación de hash superada).
- `flags`: lista cerrada de enums (`ALFRESCO_OK`, `HASH_MATCH`, `HASH_MISMATCH`, `VERSION_CONFLICT`, `TIMEOUT`, `RECOVERY_PENDING`).

Prohibición explícita: No se permite almacenar Authorization, cookies, tokens, headers completos, stack traces, base64, imágenes, PDFs, datos clínicos ni identidad completa. Campos no definidos son rechazados por Pydantic (`extra = forbid`).

## 9. Modelo de Datos firpos

**Tabla `firpos`**

- `posid` (BigInteger, PK, autoincrement)
- `firid` (BigInteger, FK a `docfirma.firid`, ON DELETE RESTRICT, NOT NULL)
- `pagina` (Integer, NOT NULL) — Página 1-based (`pagina > 0`).
- `posx` (Numeric(12,4), NOT NULL) — X en Puntos PDF (`posx >= 0`).
- `posy` (Numeric(12,4), NOT NULL) — Y en Puntos PDF (`posy >= 0`).
- `ancho` (Numeric(12,4), NOT NULL) — Ancho en Puntos PDF (`ancho > 0`).
- `alto` (Numeric(12,4), NOT NULL) — Alto en Puntos PDF (`alto > 0`).
- `rotaci` (Integer, NOT NULL, default 0) — Rotación (`0, 90, 180, 270`).
- `orden` (Integer, NOT NULL) — Orden de visualización (`orden > 0`).
- `camid` (BigInteger, FK a `tplcamp.camid`, ON DELETE RESTRICT, Nullable) — Referencia informativa a plantilla base. Solo para trazabilidad de origen. Puede ser null en posicionamiento manual. ON DELETE RESTRICT cuando no sea null.
- `feccre` (Timestamptz, NOT NULL, server_default=now())

**Regla de Inmutabilidad firpos:** Una vez insertado en `firpos`, el motor de estampado PyMuPDF lee exclusivamente las coordenadas de `firpos`. `camid` no es fuente operativa después de la inserción. Modificaciones posteriores a `tplcamp` no afectan ni alteran las firmas existentes.

## 10. Coordenadas Canónicas

- **Unidades persistidas:** Exclusivamente Puntos PDF (72 puntos = 1 pulgada). Precisión `NUMERIC(12,4)` — 12 dígitos totales con 4 decimales, suficiente para documentos de hasta ~1.38 millones de puntos (>19,000 pulgadas) con resolución de décima de milímetro.
- **Origen:** Esquina superior izquierda (0,0). X hacia la derecha, Y hacia abajo.
- **Conversión:** Píxeles, porcentajes o fracciones enviados desde la API frontend se convierten a Puntos PDF en la capa de servicios del backend antes de guardar en BD, validándolos contra las dimensiones reales de la página obtenidas con PyMuPDF.
- No se persisten fracciones, porcentajes ni píxeles como formato alternativo.

## 11. Justificación de docid Redundante en docfirma

Se incluye `docid` en `docfirma` a pesar de ser accesible vía `parid -> dpasid -> docid` para:

1. Bloqueo atómico `SELECT FOR UPDATE` sobre `docfir` para serializar firmas en un documento.
2. Garantizar el constraint parcial `UNIQUE` de máximo 1 intento activo por `docid`.
3. Asignación determinista de `secuen` dentro del documento.
4. Consultas operativas sin JOINs.

El servicio valida en la transacción de reserva que `docpart.dpasid -> docpaso.docid` coincida con el `docid` almacenado. El frontend no puede inyectar `docid` como fuente de verdad.

## 12. Estados, Campos Obligatorios y Transiciones

### INICIADA

- **Obligatorios:** `firid`, `docid`, `parid`, `opeid`, `secuen`, `intnum`, `tipfir`, `verori`, `hasori`, `fecini`, `revnum`.
- **Nulos:** `hasfin`, `verfin`, `fecfin`, `errcod`, `motivo`.
- **Efecto externo:** Ninguno.
- **Transiciones:** → `GENERADA` | `CONFLICTO` | `FALLIDA` | `CANCELADA`.
- **Auditoría:** `FIR_INIC`.

### GENERADA

- **Obligatorio adicional:** `hasfin`.
- **Nulos:** `verfin`, `fecfin`, `errcod`, `motivo`.
- **Efecto externo:** Existe PDF temporal local. No existe publicación remota.
- **Transiciones:** → `SUBIENDO` | `CONFLICTO` | `FALLIDA` | `CANCELADA`.
- **Auditoría:** `FIR_GENE`.

### SUBIENDO

- **Obligatorios:** `hasfin`.
- **Nulos:** `verfin` puede ser null, `fecfin`, `errcod`, `motivo`.
- **Efecto externo:** La publicación puede haber ocurrido aunque no exista respuesta. El resultado externo es incierto.
- **Transiciones:** → `CARGADA` | `VERIFICANDO` (reconciliación) | `CONFLICTO` | `FALLIDA` (solo con evidencia positiva de ausencia).
- **Auditoría:** `FIR_SUBE`.

### CARGADA

- **Obligatorios:** `hasfin`, `verfin`.
- **Nulos:** `fecfin`, `errcod`, `motivo`.
- **Efecto externo:** La versión ya existe en Alfresco.
- **Transiciones:** → `VERIFICANDO` | `CONFLICTO` | `FALLIDA`.
- **Auditoría:** `FIR_CARG`.

### VERIFICANDO

- **Obligatorios:** `hasfin`, `verfin`.
- **Nulos:** `fecfin`, `errcod`, `motivo`.
- **Efecto externo:** Pendiente confirmar hash remoto.
- **Transiciones:** → `COMPLETADA` | `CONFLICTO` | `FALLIDA`.
- **Auditoría:** `FIR_VERI`.

### COMPLETADA (Terminal)

- **Obligatorios:** `hasfin`, `verfin`, `fecfin`, `result.verchk = true`, `result.haschk = true`, `result` requerido.
- **Regla:** `errcod` debe ser NULL; `motivo` debe ser NULL.
- **Efecto externo:** Versión remota verificada. `docpart` completado.
- **Transiciones:** Ninguna. Estado terminal inmutable.
- **Auditoría:** `FIR_COMP`.

### CONFLICTO (Terminal)

- **Obligatorios:** `errcod`, `fecfin`.
- **Conserva:** `hasfin` si ya fue generado; `verfin` si se detectó versión remota. No se borran para ocultar publicaciones.
- **Efecto externo:** Incierto o detectado.
- **Transiciones:** Ninguna. Estado terminal inmutable. Requiere intervención manual.
- **Auditoría:** `FIR_CONF`.

### FALLIDA (Terminal)

- **Obligatorios:** `errcod`, `fecfin`.
- **Conserva:** `hasfin` y `verfin` si ya existían. No se borran para aparentar ausencia de efecto remoto.
- **Efecto externo:** Fallo operativo comprobado con evidencia positiva.
- **Transiciones:** Ninguna. Estado terminal. El participante puede iniciar un nuevo intento (`intnum + 1`).
- **Auditoría:** `FIR_FALL`.

### CANCELADA (Terminal)

- **Obligatorios:** `motivo`, `fecfin`.
- **Efecto externo:** Solo permitida antes de un efecto externo irreversible (antes de `SUBIENDO`).
- **Transiciones:** Ninguna. Estado terminal inmutable.
- **Auditoría:** `FIR_CANC`.

## 13. Diferenciación de Transacciones y Orden de Locks (Prevención de Deadlocks)

Se definen dos categorías de transacciones para garantizar la seguridad sin bloqueos innecesarios:

### A. Transacciones del agregado documental

Aplican a operaciones que modifican el estado de los componentes superiores: `begin_signature`, `finalize_verified_signature`, `cancel_signature` (cuando afecta `docpart`), reconciliación (cuando va a finalizar), vencimiento, rechazo y omisión.

Orden obligatorio e invariable:
1. `docpaso` (Paso actual) — `SELECT FOR UPDATE`
2. `docpart` (Participante objetivo) — `SELECT FOR UPDATE`
3. `docfir` (Proceso documental) — `SELECT FOR UPDATE`
4. `docfirma` (Intento activo) — `SELECT FOR UPDATE`
5. `docpart` restantes (Demás participantes del paso, ordenados por `parid` ascendente) — `SELECT FOR UPDATE`
6. `docpaso` siguiente (Siguiente paso en la secuencia si el paso concluye) — `SELECT FOR UPDATE`
7. `docpart` del siguiente paso (Participantes del siguiente paso, ordenados por `parid` ascendente)

Puede existir una lectura inicial no bloqueante para resolver relaciones, pero los locks posteriores deben respetar siempre el orden aprobado.

### B. Transiciones locales del intento

Aplican exclusivamente al avance interno del intento de firma: `INICIADA` → `GENERADA`, `GENERADA` → `SUBIENDO`, `SUBIENDO` → `CARGADA`, `CARGADA` → `VERIFICANDO`, y actualización de `result` y `revnum`.

**Regla obligatoria:**
Pueden bloquear únicamente `docfirma` cuando:
- no modifican `docpart`;
- no modifican `docpaso`;
- no modifican `docfir`;
- no adquieren posteriormente locks superiores dentro de la misma transacción.

Una transacción que ya bloqueó `docfirma` no puede después intentar bloquear `docfir`, `docpart` o `docpaso`. Si necesita modificar el agregado, debe terminar y reiniciar usando el orden completo de la categoría A.

## 14. Servicio Central de Finalización

```
finalize_verified_signature(
    firid: int,
    expected_revnum: int,
    expected_participant_verlock: int
) -> None
```

Este servicio interno es el único punto de entrada para completar una firma verificada. Lo utilizan tanto la finalización normal como la reconciliación. No existe una ruta alternativa para actualizar `docpart`, `docpaso` o `docfir` tras una firma.

Responsabilidades en una sola transacción PostgreSQL:

1. Resolver relaciones mediante lectura no bloqueante (`firid -> parid -> dpasid -> docid`).
2. Bloquear `docpaso` (`SELECT FOR UPDATE`).
3. Bloquear `docpart` objetivo (`SELECT FOR UPDATE`).
4. Bloquear `docfir` (`SELECT FOR UPDATE`).
5. Bloquear `docfirma` (`SELECT FOR UPDATE`).
6. **Control de Idempotencia:** Si `estado = COMPLETADA`, la consistencia debe comprobarse exclusivamente con la propia fila bloqueada:
   - `verfin` no es null;
   - `hasfin` no es null;
   - `result.schema_ver = 1`;
   - `result.fase` IN ('VERIFICACION', 'FINALIZACION');
   - `result.verchk = true`;
   - `result.haschk = true`;
   - `HASH_MATCH` está presente;
   - `docpart.estado = 'COMPLETADO'`.

   No utilizar valores recibidos del cliente para decidir si `verfin` o `hasfin` "coinciden".

   Si todo es consistente:
   - retornar éxito idempotente;
   - no validar el `expected_revnum` antiguo;
   - no modificar `fecmod`;
   - no incrementar `revnum`;
   - no registrar otro `FIR_COMP`.
7. **Integridad en Idempotencia:** Si la evidencia persistida es inconsistente:
   - lanzar `SignatureIntegrityError`;
   - registrar alerta de sistema;
   - no modificar el agregado.
8. Validar `expected_revnum` contra `docfirma.revnum`. Si difiere: `SignatureConcurrencyError`.
9. Validar `expected_participant_verlock` contra `docpart.verlock`. Si difiere: `SignatureConcurrencyError`.
10. Validar que `docfirma.estado` sea `VERIFICANDO`.
11. Comprobar obligatoriamente en la fila bloqueada que existe evidencia persistida de verificación escrita por el backend (no recibir del payload de usuario):
    - `result` no es null (es obligatorio en `VERIFICANDO` y `COMPLETADA`).
    - `result.schema_ver = 1`
    - `result.fase = 'VERIFICACION'` (o equivalente validado por Pydantic).
    - `result.verchk = true`
    - `result.haschk = true`
    - Flag `HASH_MATCH` está presente.
    - `verfin` no es null y coincide con la versión verificada.
    - `hasfin` no es null.
12. Marcar `docfirma` como `COMPLETADA` (`fecfin = now()`, `revnum++`).
13. Completar `docpart` idempotentemente (solo si no está ya `COMPLETADO`).
14. Resolver `docpaso` una sola vez (evaluar estrategia TODOS/UNO, cancelar participantes sobrantes).
15. Activar el siguiente paso una sola vez (si el paso se resolvió).
16. Actualizar `docfir.estado` según el estado real del flujo (ver sección 22).
17. Registrar `FIR_COMP` en auditoría.
18. `COMMIT` atómico. Rollback completo ante cualquier fallo.

## 15. Protocolo Completo de Publicación

1. **Fase A (Reserva Atómica):**
   - Resolver relaciones sin lock.
   - Bloquear `docpaso` (`SELECT FOR UPDATE`).
   - Bloquear `docpart` objetivo (`SELECT FOR UPDATE`).
   - Bloquear `docfir` (`SELECT FOR UPDATE`).
   - Validar actor, paso y participante.
   - Calcular `secuen`.
   - Calcular `intnum`.
   - Crear `docfirma` `INICIADA`.
   - Crear todas las filas `firpos`.
   - Registrar `FIR_INIC`.
   - `COMMIT` único de la Fase A. (Si falla una posición: rollback completo de `docfirma` y `firpos`, no queda operación activa ni índice parcial bloqueando el documento).
2. Descargar el PDF de la versión `verori` desde Alfresco (sin locks de BD).
3. Validar `hasori` contra el hash del contenido descargado.
4. Generar el PDF temporal firmado usando PyMuPDF.
5. Calcular `hasfin` (SHA-256 hex 64 minúsculas).
6. Transacción corta: bloquear `docfirma`, validar `revnum`, pasar `INICIADA → GENERADA`, guardar `hasfin`, registrar `FIR_GENE`. `COMMIT`.
7. **Revalidación previa al POST (sin locks de BD):** Consultar nuevamente el nodeId en Alfresco, obtener la versión vigente y su hash.
8. Comparar versión vigente con `verori` y hash vigente con `hasori`. Verificar que el nodo siga siendo PDF.
9. Si hay divergencia: transacción corta para pasar a `CONFLICTO`, registrar `FIR_CONF`. `COMMIT`. Abortar publicación.
10. Transacción corta: bloquear `docfirma`, validar `revnum`, pasar `GENERADA → SUBIENDO`, registrar `FIR_SUBE`. `COMMIT`.
11. **Solo después del COMMIT de SUBIENDO:** Iniciar llamada REST POST a Alfresco enviando el PDF firmado y el comentario `FirmaDoc:<docid>:<firid>:<opeid>`.
12. Recibir respuesta de Alfresco.
13. Si HTTP 2xx: obtener `verfin` asignado por Alfresco.
14. Transacción corta: bloquear `docfirma`, registrar `verfin`, pasar a `CARGADA`, registrar `FIR_CARG`. `COMMIT`.
15. Transacción corta: pasar a `VERIFICANDO`, registrar `FIR_VERI`. `COMMIT`.
16. **Verificación remota (sin locks de BD):** Descargar o consultar los bytes de la versión `verfin` en Alfresco.
17. Calcular hash remoto sobre los bytes descargados.
18. Comparar hash remoto con `hasfin`.
19. Si no coincide: transacción corta para marcar `CONFLICTO`, registrar `FIR_CONF`. `COMMIT`. Abortar.
20. Transacción corta obligatoria:
    - bloquear únicamente `docfirma`;
    - validar que estado = `VERIFICANDO`;
    - validar `expected_revnum`;
    - persistir `result`:
      - `schema_ver` = 1;
      - `fase` = `VERIFICACION`;
      - `verchk` = true;
      - `haschk` = true;
      - `flags` contiene `HASH_MATCH`;
      - `remcod` y `remmsg` sanitizados cuando existan;
    - actualizar `fecmod`;
    - incrementar `revnum`;
    - `COMMIT`;
    - obtener el nuevo `revnum`.
21. Ejecutar `finalize_verified_signature` usando ese nuevo `revnum`.
22. Limpiar archivos temporales en bloque `finally`.

No aceptar esta evidencia desde frontend, payload, controlador ni argumento booleano.

Prohibiciones:

- No cargar antes de persistir `SUBIENDO`.
- No mantener locks PostgreSQL durante HTTP.
- No reintentar ciegamente una carga en `SUBIENDO`.
- No marcar `FALLIDA` si se desconoce si Alfresco creó la versión.

La revalidación previa reduce la ventana de carrera, pero no la elimina por sí sola. La precondición remota es el control preferido; durante la implementación debe verificarse qué mecanismo atómico (como `If-Match` o ETag) soporta realmente Alfresco ACS 26.1. Si no existe precondición atómica, el riesgo residual debe registrarse, pero cualquier error 409 o 412 desde Alfresco produce `CONFLICTO`. No se debe afirmar control total sin una precondición remota.

## 16. Reconciliación Idempotente (reconcile_signature_attempt)

### Para INICIADA antigua

- Cancelar o marcar `FALLIDA` si no existe procesamiento pendiente.
- No consultar Alfresco salvo evidencia de efecto externo.

### Para GENERADA antigua

- Puede cancelarse directamente.
- No subir automáticamente sin una acción explícita autorizada.

### Para SUBIENDO

Consultar historial de versiones del nodo en Alfresco buscando el comentario con `opeid`.

**Caso 1 versión encontrada:**

1. Validar nodeId.
2. Validar comentario.
3. Obtener `verfin`.
4. Descargar bytes exactos.
5. Calcular hash remoto.
6. Comparar con `hasfin`.
7. Si coincide: pasar a `CARGADA`, luego `VERIFICANDO`.
8. Persistir evidencia de verificación en `result` bajo nueva transacción corta:
   - bloquear únicamente `docfirma`;
   - validar que estado = `VERIFICANDO`;
   - validar `expected_revnum`;
   - persistir `result` con `HASH_MATCH` y `fase` = `VERIFICACION`;
   - actualizar `fecmod`;
   - incrementar `revnum`;
   - `COMMIT`;
   - obtener el nuevo `revnum`.
9. Ejecutar `finalize_verified_signature` usando ese nuevo `revnum`.
10. No volver a publicar.

**Caso 0 coincidencias:**

Para declarar evidencia suficiente de ausencia de publicación y permitir marcar el intento como `FALLIDA`, se exige estrictamente que se cumplan **todas** estas condiciones:
1. Alfresco está disponible y el nodeId es accesible.
2. El historial completo paginado del nodo fue descargado.
3. Todas las páginas del historial fueron consultadas.
4. La versión actual fue consultada.
5. Ninguna versión posterior a `verori` contiene el comentario con `opeid`.
6. Ninguna versión posterior a `verori` posee un hash igual a `hasfin`.
7. Se alcanzó el número mínimo configurable de consultas.
8. La ventana de espera/timeout está agotada.

Si **no** se cumplen todas estas condiciones, la ausencia no está demostrada. El reconciliador debe:
- Mantener el estado `SUBIENDO`.
- Lanzar `SignatureRecoveryRequiredError`.
- No habilitar otro intento ni marcar como `FALLIDA`.
- Programar reconciliación posterior.

**Caso >1 versión encontrada con el mismo opeid:**

1. Pasar `docfirma` a `CONFLICTO`.
2. Establecer `errcod` específico (`DUPLICATE_REMOTE_VERSION`).
3. Registrar `FIR_CONF` y `FIR_RECO`.
4. No elegir una versión automáticamente.
5. No completar `docpart`.
6. No resolver `docpaso`.
7. Bloquear nuevos intentos sobre ese documento.
8. Exigir intervención administrativa.

El UNIQUE local de `opeid` no evita duplicados remotos. No se describe como control suficiente sobre Alfresco.

La reconciliación es idempotente y segura ante ejecución simultánea.

### Para CARGADA

- Verificar la versión ya registrada en `verfin`.
- CARGADA → VERIFICANDO → commit.
- Descargar versión remota.
- Calcular hash y comparar.
- Persistir evidencia `HASH_MATCH` en transacción corta (actualizando `fecmod` y `revnum`) → commit.
- Ejecutar `finalize_verified_signature` usando el nuevo `revnum`.

### Para VERIFICANDO

- Repetir verificación de hash (descargar y comparar) sin publicar nuevamente.
- Persistir evidencia `HASH_MATCH` en transacción corta (actualizando `fecmod` y `revnum`) → commit.
- Ejecutar `finalize_verified_signature` usando el nuevo `revnum`.

## 17. Fallo de Commit Local Post-Publicación

Escenario:

1. `docfirma` está en `SUBIENDO`.
2. Alfresco crea la versión.
3. Se recibe respuesta HTTP 2xx.
4. Falla el commit local que pasa a `CARGADA`.
5. PostgreSQL permanece en `SUBIENDO`.

Recuperación:

- Nunca se vuelve a ejecutar el POST.
- La reconciliación encuentra `opeid` en el historial de Alfresco.
- Recupera `verfin`.
- Valida hash.
- Persiste evidencia de verificación (`HASH_MATCH`, `fase = 'VERIFICACION'`) actualizando `fecmod` y `revnum` en transacción corta → `COMMIT`.
- Ejecuta `finalize_verified_signature` usando el nuevo `revnum`.

PostgreSQL no puede revertir la versión creada en Alfresco. La consistencia se recupera mediante reconciliación idempotente.

## 18. Firma Manuscrita Capturada

Código persistido en `tipfir`: `MANUSCRITA`.

**Alcance en V1:** La firma manuscrita se realiza por un participante institucional autenticado, o mediante modalidad asistida por un operador institucional expresamente autorizado y auditado. Los pacientes o firmantes externos sin identidad institucional quedan fuera del alcance de esta fase. No se crean participantes externos dentro de `docfirma`. No se usa la firma manuscrita como atajo para evitar el modelo de identidad.

Reglas de almacenamiento efímero:

- PNG validado por contenido (magic bytes `\x89PNG`).
- Trazo no vacío (verificar que la imagen no sea uniforme).
- Tamaño máximo configurable (`FIRMADOC_MAX_PNG_SIZE`, ej. 500 KB).
- Dimensiones máximas configurables.
- Nombre de archivo impredecible UUID.
- Permisos `0600`.
- Directorio temporal no público, configurable (`FIRMADOC_TMP_DIR`). No se hardcodea una ruta específica.
- Vinculación a `firid` y `opeid` del intento actual.
- Uso exclusivo en el intento actual. No reutilizable.
- Eliminación física incondicional en bloque `try ... finally` tras incrustar en PDF.
- Limpieza background por TTL como contingencia (configurable, ej. 30 minutos).
- Prohibido guardar PNG, Base64, vectores o trazos en PostgreSQL, logs o cachés funcionales.
- Prohibido registrar imagen o datos de trazo en auditoría.

## 19. Firma Electrónica Interna Institucional

Código persistido en `tipfir`: `INTERNA`.

- Representa la firma de usuarios autenticados institucionalmente.
- No utiliza firmas manuscritas ni imágenes cargadas por el usuario.
- El backend (usando PyMuPDF) construye el bloque visual con la información congelada del `docpart`: nombre (`nomcom`), cargo/rol (`rolpro`), fecha/hora del servidor, hash del documento.
- El frontend no envía texto ni imagen para esta marca. Texto del cliente es rechazado.
- Requiere intención explícita del firmante.
- No equivale a firma digital certificada, PKI ni X.509. No se denomina "certificada", "digital", "sello certificado" ni ninguna expresión que implique certificación criptográfica.

## 20. Tecnologías Aprobadas para PDF

- Frontend: PDF.js, Signature Pad.
- Backend: PyMuPDF (`fitz`).
- PyPDF y ReportLab quedan excluidas del proyecto. No se instalan ni agregan.

## 21. NodeId Compartido y Control de Procesos

**Estado actual:** `docfir.py` posee el índice `ix_docfir_nodid_verini_unique` sobre las columnas `(nodid, verini)` con condición parcial `WHERE activo = true AND estado != 'CANCELADO'`. Este índice impide dos procesos sobre el mismo nodo con la misma versión inicial, pero no prohíbe múltiples procesos activos si las versiones iniciales difieren.

**Propuesta de migración conceptual futura (docfir y docfirma):**
- Modificar `docfirma`: Agregar `server_default=now()` a `fecmod` y garantizar su actualización transaccional.
- Se creará un índice único parcial adicional en `docfir`:

```sql
CREATE UNIQUE INDEX uq_docfir_nodid_activo ON docfir (nodid)
WHERE estado IN (
    'BORRADOR', 'PREPARADO', 'EN_CURSO',
    'PENDIENTE_FIRMA', 'FIRMADO_PARCIAL',
    'PENDIENTE_PUBLICACION', 'ERROR_PUBLICACION'
);
```

Garantiza como máximo 1 proceso `docfir` activo por `nodid`.

**Estrategia de migración:**

- Antes de crear el índice, ejecutar consulta de diagnóstico para identificar duplicados existentes: `SELECT nodid, COUNT(*) FROM docfir WHERE estado IN (...) GROUP BY nodid HAVING COUNT(*) > 1`.
- Si existen duplicados, la migración se bloquea hasta resolverlos manualmente (cancelar o completar los procesos redundantes).
- Estados terminales excluidos: `COMPLETADO`, `RECHAZADO`, `CANCELADO`.
- El servicio debe manejar `IntegrityError` del índice y rechazar el inicio de un segundo proceso activo.

## 22. Estados Reales de docfir y Transiciones

Los estados lógicos reales de `docfir` son estrictamente los definidos en `backend/app/models/docfir.py`:

`BORRADOR`, `PREPARADO`, `EN_CURSO`, `PENDIENTE_FIRMA`, `FIRMADO_PARCIAL`, `PENDIENTE_PUBLICACION`, `COMPLETADO`, `RECHAZADO`, `CANCELADO`, `ERROR_PUBLICACION`.

No existen los estados `FIRMADO`, `CERRADO` ni `ERROR` genérico.

### Transiciones tras firma verificada

La transición de `docfir.estado` la calcula `flow_service` (o la lógica de resolución de flujo) dentro de la transacción de `finalize_verified_signature`, bajo lock de `docfir` y control optimista:

- **Quedan firmas obligatorias pendientes:** `docfir` → `FIRMADO_PARCIAL`.
- **No quedan firmas, pero quedan pasos posteriores del flujo:** `docfir` → `EN_CURSO`.
- **No quedan firmas ni pasos posteriores:** `docfir` → `COMPLETADO`.

### PENDIENTE_PUBLICACION

En V1, `docfirma` ya publica cada firma como una nueva versión en Alfresco durante el intento. `PENDIENTE_PUBLICACION` solo aplica si existe una operación final adicional expresamente configurada (ej. generación de un PDF consolidado final distinto) y gestionada por `docpubl`. En el circuito normal de `docfirma` V1, `PENDIENTE_PUBLICACION` no se usa.

### Definiciones de estados de docfir relevantes

- **PENDIENTE_FIRMA:** Existen participantes por firmar y no se ha completado ninguna firma.
- **FIRMADO_PARCIAL:** Al menos una firma fue completada y quedan firmantes obligatorios pendientes.
- **COMPLETADO:** El flujo completo terminó y la última versión requerida fue verificada.

## 23. Secuencia e Intentos

- `secuen` se asigna bajo lock de `docfir` (paso 1 de la reserva). Consulta `MAX(secuen)` sobre `docfirma WHERE docid = ?` dentro de la sección serializada por `SELECT FOR UPDATE` de `docfir`.
- `intnum` se asigna bajo lock de `docpart` (paso 1 de la reserva). Consulta `MAX(intnum)` sobre `docfirma WHERE parid = ?` dentro de la sección serializada por `SELECT FOR UPDATE` de `docpart`.
- Las restricciones `UNIQUE(docid, secuen)` y `UNIQUE(parid, intnum)` actúan como segunda defensa contra carreras.
- El servicio captura `IntegrityError` del UNIQUE y lo trata como `SignatureConcurrencyError` para reintento.
- Los intentos fallidos no se sobrescriben ni reutilizan.
- Una reactivación de `docpart` incrementa `intnum` creando una nueva fila de `docfirma`.
- Solo puede existir una `docfirma` con `estado = 'COMPLETADA'` por `parid` (garantizado por índice parcial).

## 24. opeid

- UUID v4 nativo de PostgreSQL (`uuid` type).
- Generado exclusivamente por el backend. No se acepta UUID arbitrario del frontend.
- UNIQUE global.
- Inmutable tras creación.
- Utilizado en: auditoría (`detalle`), logs, comentario de versión Alfresco.
- No reutilizable entre intentos.
- Formato del comentario de versión: `FirmaDoc:<docid>:<firid>:<opeid>`. No incluye nombres, documentos de identidad ni datos clínicos.

## 25. Hashes

- SHA-256, 64 caracteres, hexadecimal minúsculo.
- `hasori`: calculado obligatoriamente por el backend sobre los bytes exactos descargados de Alfresco. Obligatorio en todos los estados.
- `hasfin`: calculado obligatoriamente por el backend sobre los bytes exactos del PDF generado. Obligatorio desde `GENERADA`. Se conserva en `CONFLICTO` y `FALLIDA` si ya fue generado. No se confía en hashes del frontend.
- Hash remoto: calculado obligatoriamente sobre los bytes de la versión `verfin` descargada de Alfresco. Obligatorio para que `finalize_verified_signature` apruebe `COMPLETADA`.

### Reglas de hasfin por estado

| Estado | hasfin |
| :--- | :--- |
| `INICIADA` | NULL |
| `GENERADA` | Obligatorio |
| `SUBIENDO` | Obligatorio |
| `CARGADA` | Obligatorio |
| `VERIFICANDO` | Obligatorio |
| `COMPLETADA` | Obligatorio |
| `CONFLICTO` | Conservar si existía |
| `FALLIDA` | Conservar si existía |
| `CANCELADA` | NULL (pre-generación) o conservar si existía |

### Reglas de verfin por estado

| Estado | verfin |
| :--- | :--- |
| `INICIADA` | NULL |
| `GENERADA` | NULL |
| `SUBIENDO` | NULL |
| `CARGADA` | Obligatorio |
| `VERIFICANDO` | Obligatorio |
| `COMPLETADA` | Obligatorio |
| `CONFLICTO` | Conservar si se detectó versión remota |
| `FALLIDA` | Conservar si se detectó versión remota |
| `CANCELADA` | NULL |

## 26. Seguridad

- Actor derivado del principal autenticado de sesión. Nunca recibido desde payload HTTP.
- Comparación del actor con `docpart.usrid` antes de permitir la firma.
- Capacidades verificadas en backend (paso de tipo `FIRMAR`, participante en estado `DISPONIBLE` o `EN_PROCESO`).
- `expected_revnum` validado contra `docfirma.revnum`.
- `expected_participant_verlock` validado contra `docpart.verlock`.
- Protección contra replay: `opeid` único por intento.
- `opeid` generado por backend. No aceptar del frontend.
- CSRF en sesiones web.
- Tamaño máximo de PNG configurable (`FIRMADOC_MAX_PNG_SIZE`).
- Tamaño máximo de PDF configurable.
- Cantidad máxima de páginas configurable.
- Dimensiones máximas de imagen configurable.
- Validación de magic bytes PNG (`\x89PNG`).
- Validación MIME real (no confiar en Content-Type del cliente).
- Validación estructural del PDF con PyMuPDF antes de estampar.
- Rechazo de PDF cifrado no soportado.
- Coordenadas validadas dentro de los límites reales de la página PDF.
- Temporales con permisos `0600`.
- Ruta temporal configurable (`FIRMADOC_TMP_DIR`).
- Limpieza en `finally`.
- Limpieza por TTL como contingencia.
- Secretos (credenciales Alfresco) mediante variables de entorno.
- Usuario técnico Alfresco con permisos mínimos necesarios.
- Prohibición de usar usuario `admin` de Alfresco.
- No registrar `Authorization`, cookies ni tokens en logs ni auditoría.
- Agente de usuario sanitizado en auditoría.
- Mensajes de error sanitizados (sin rutas, IPs internas, stack traces).
- Correlación mediante `opeid`.
- `ProductionIdentityResolver` permanece en modo fail-closed.

## 27. Auditoría Post Efectos Externos

### Antes del HTTP POST

Si falla la auditoría o el commit: rollback completo de PostgreSQL. No se ejecuta el POST.

### Después del HTTP POST

Si falla la auditoría o el commit local:

- Alfresco no puede revertirse.
- El estado local permanece en `SUBIENDO` (estado recuperable).
- La reconciliación busca `opeid` en Alfresco.
- Reconstruye `verfin` y hash.
- Registra `FIR_RECO` posteriormente.
- No se afirma "reversión completa" cuando existe un efecto externo irreversible.

## 28. Auditoría (Extensión audifir)

En una migración Alembic futura se actualizará el constraint de `audifir.enttip` (actualmente `String(20)`) para incorporar el valor `'FIRMA'`. El campo `evento` es `String(40)`.

Valores actuales de `enttip`: `'DOCUMENTO'`, `'PLANTILLA'`, `'CAMPO'`, `'SISTEMA'`, `'FLUJODOC'`, `'FLUPASO'`, `'PASO'`, `'PARTICIPANTE'`. Se añadirá `'FIRMA'`.

### Eventos de auditoría

| Evento | Descripción |
| :--- | :--- |
| `FIR_INIC` | Intento de firma creado |
| `FIR_GENE` | PDF firmado generado localmente |
| `FIR_CONF` | Conflicto detectado (revalidación o hash) |
| `FIR_SUBE` | Estado SUBIENDO persistido antes del POST |
| `FIR_CARG` | Versión cargada en Alfresco |
| `FIR_VERI` | Verificación de hash remoto iniciada |
| `FIR_COMP` | Firma completada y verificada |
| `FIR_FALL` | Intento fallido |
| `FIR_CANC` | Intento cancelado |
| `FIR_RECO` | Reconciliación ejecutada |

Cada registro almacena en `detalle` (texto sanitizado): `parid`, `dpasid`, `opeid`, estado anterior, estado nuevo, `verori`, `verfin`, `hasori`, `hasfin`, `errcod`, `motivo`, IP del actor, agente de usuario sanitizado.

No se incluyen imágenes, PDFs, base64, secretos ni datos clínicos en la auditoría.

## 29. Matriz de Constraints en PostgreSQL

### docfirma

| Constraint | Tipo | Descripción | Implementación |
| :--- | :--- | :--- | :--- |
| `pk_docfirma` | PK | `firid` | PostgreSQL |
| `fk_docfirma_docfir` | FK | `docid` → `docfir.docid` ON DELETE RESTRICT | PostgreSQL |
| `fk_docfirma_docpart` | FK | `parid` → `docpart.parid` ON DELETE RESTRICT | PostgreSQL |
| `uq_docfirma_opeid` | UNIQUE | `opeid` | PostgreSQL |
| `uq_docfirma_docid_secuen` | UNIQUE | `(docid, secuen)` | PostgreSQL |
| `uq_docfirma_parid_intnum` | UNIQUE | `(parid, intnum)` | PostgreSQL |
| `ck_docfirma_secuen_pos` | CHECK | `secuen > 0` | PostgreSQL |
| `ck_docfirma_intnum_pos` | CHECK | `intnum > 0` | PostgreSQL |
| `ck_docfirma_revnum_pos` | CHECK | `revnum >= 1` | PostgreSQL |
| `ck_docfirma_tipfir` | CHECK | `tipfir IN ('MANUSCRITA', 'INTERNA')` | PostgreSQL |
| `ck_docfirma_estado` | CHECK | `estado IN ('INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO', 'COMPLETADA', 'CONFLICTO', 'FALLIDA', 'CANCELADA')` | PostgreSQL |
| `ck_docfirma_hasori_hex` | CHECK | `hasori ~ '^[0-9a-f]{64}$'` | PostgreSQL |
| `ck_docfirma_hasfin_hex` | CHECK | `hasfin IS NULL OR hasfin ~ '^[0-9a-f]{64}$'` | PostgreSQL |
| `ck_docfirma_fechas` | CHECK | `fecfin IS NULL OR fecfin >= fecini` | PostgreSQL |
| `ck_docfirma_errcod_fallida` | CHECK | `estado != 'FALLIDA' OR errcod IS NOT NULL` | PostgreSQL |
| `ck_docfirma_errcod_conflicto` | CHECK | `estado != 'CONFLICTO' OR errcod IS NOT NULL` | PostgreSQL |
| `ck_docfirma_motivo_cancelada` | CHECK | `estado != 'CANCELADA' OR (motivo IS NOT NULL AND TRIM(motivo) != '')` | PostgreSQL |
| `ck_docfirma_fecfin_terminal` | CHECK | `estado NOT IN ('COMPLETADA','CONFLICTO','FALLIDA','CANCELADA') OR fecfin IS NOT NULL` | PostgreSQL |
| `ck_docfirma_fecfin_operativo` | CHECK | `estado IN ('COMPLETADA','CONFLICTO','FALLIDA','CANCELADA') OR fecfin IS NULL` | PostgreSQL |
| `uq_docfirma_docid_activa` | INDEX PARCIAL | UNIQUE `(docid)` WHERE `estado IN ('INICIADA','GENERADA','SUBIENDO','CARGADA','VERIFICANDO')` | PostgreSQL |
| `uq_docfirma_parid_compl` | INDEX PARCIAL | UNIQUE `(parid)` WHERE `estado = 'COMPLETADA'` | PostgreSQL |
| `ck_docfirma_hasfin_req` | CHECK | `estado NOT IN ('GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO', 'COMPLETADA') OR hasfin IS NOT NULL` | PostgreSQL, Pydantic y Servicio |
| `ck_docfirma_verfin_req` | CHECK | `estado NOT IN ('CARGADA', 'VERIFICANDO', 'COMPLETADA') OR verfin IS NOT NULL` | PostgreSQL, Pydantic y Servicio |
| `ck_docfirma_result_req` | CHECK | `estado NOT IN ('VERIFICANDO', 'COMPLETADA') OR result IS NOT NULL` | PostgreSQL, Pydantic y Servicio |
| `ck_docfirma_completada_limpia` | CHECK | `estado != 'COMPLETADA' OR (errcod IS NULL AND motivo IS NULL)` | PostgreSQL, Pydantic y Servicio |
| `ck_docfirma_fecmod_req` | PROPIEDAD | `fecmod IS NOT NULL` | PostgreSQL |
| (Consistencia docid/parid) | Servicio | `docpart.dpasid → docpaso.docid == docfirma.docid` | Validación de servicio |

### firpos

| Constraint | Tipo | Descripción | Implementación |
| :--- | :--- | :--- | :--- |
| `pk_firpos` | PK | `posid` | PostgreSQL |
| `fk_firpos_docfirma` | FK | `firid` → `docfirma.firid` ON DELETE RESTRICT | PostgreSQL |
| `fk_firpos_tplcamp` | FK | `camid` → `tplcamp.camid` ON DELETE RESTRICT | PostgreSQL |
| `uq_firpos_firid_orden` | UNIQUE | `(firid, orden)` | PostgreSQL |
| `ck_firpos_pagina` | CHECK | `pagina > 0` | PostgreSQL |
| `ck_firpos_posx` | CHECK | `posx >= 0` | PostgreSQL |
| `ck_firpos_posy` | CHECK | `posy >= 0` | PostgreSQL |
| `ck_firpos_ancho` | CHECK | `ancho > 0` | PostgreSQL |
| `ck_firpos_alto` | CHECK | `alto > 0` | PostgreSQL |
| `ck_firpos_orden` | CHECK | `orden > 0` | PostgreSQL |
| `ck_firpos_rotaci` | CHECK | `rotaci IN (0, 90, 180, 270)` | PostgreSQL |

## 30. Índices

### docfirma

| Índice | Tipo | Columnas | Justificación |
| :--- | :--- | :--- | :--- |
| `uq_docfirma_opeid` | UNIQUE | `opeid` | Ya cubre búsquedas por opeid |
| `uq_docfirma_docid_secuen` | UNIQUE | `(docid, secuen)` | Prefijo izquierdo cubre búsquedas por docid |
| `uq_docfirma_parid_intnum` | UNIQUE | `(parid, intnum)` | Prefijo izquierdo cubre búsquedas por parid |
| `uq_docfirma_docid_activa` | UNIQUE PARCIAL | `(docid) WHERE estado IN (operativos)` | Máximo 1 intento activo por documento |
| `uq_docfirma_parid_compl` | UNIQUE PARCIAL | `(parid) WHERE estado = 'COMPLETADA'` | Máximo 1 firma completada por participante |
| `ix_docfirma_estado_fecmod` | INDEX | `(estado, fecmod)` | `fecmod` se inicializa con `feccre` al crear `docfirma` y es NOT NULL. El índice localiza intentos huérfanos de manera eficiente. |

No se crean índices redundantes por `(docid, estado)` ni `(parid, estado)` porque los índices UNIQUE por `(docid, secuen)` y `(parid, intnum)` cubren las búsquedas por su prefijo izquierdo.

### firpos

| Índice | Tipo | Columnas | Justificación |
| :--- | :--- | :--- | :--- |
| `uq_firpos_firid_orden` | UNIQUE | `(firid, orden)` | Ya cubre búsquedas por firid mediante el prefijo izquierdo. No se agrega `idx_firpos_firid` porque sería redundante. |

## 31. Matriz de Excepciones de Dominio

| Excepción | Fase | Estado Resultante | Recuperable | Auditoría | Reintento | Mensaje Sanitario |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `SignatureNotFoundError` | Validación | N/A | No | No | No | "Intento de firma no encontrado" |
| `SignatureNotAllowedError` | Autorización | N/A | No | `PAR_FDEN`/`PAS_FDEN` si no hay `firid` | No | "No autorizado para firmar este paso" |
| `SignatureConcurrencyError` | Lock/Optimismo | N/A | Sí | No | Sí | "Conflicto de concurrencia (revnum o verlock obsoleto)" |
| `SignatureAlreadyCompletedError` | Reserva | N/A | No | No | No | "El participante ya completó la firma" |
| `SignatureActiveAttemptError` | Reserva | N/A | Sí | No | Sí | "Existe un intento de firma activo" |
| `SignaturePayloadError` | Validación | `FALLIDA` | No | `FIR_FALL` | No | "Payload de firma inválido o corrupto" |
| `SignaturePdfError` | Estampado | `FALLIDA` | No | `FIR_FALL` | No | "Error al procesar la estructura PDF" |
| `SignaturePlacementError` | Coordenadas | `FALLIDA` | No | `FIR_FALL` | No | "Posición de firma fuera de límites PDF" |
| `SignatureVersionConflictError` | Revalidación | `CONFLICTO` | No | `FIR_CONF` | No | "Conflicto en la versión fuente del documento" |
| `SignatureIntegrityError` | Verificación | `CONFLICTO` | No | `FIR_CONF` | No | "Hash remoto no coincide con hash local" |
| `SignatureUploadError` | Publicación | `SUBIENDO` | Sí | `FIR_RECO` | Sí | "Error al transmitir nueva versión a Alfresco" |
| `SignatureVerificationError` | Post-carga | `VERIFICANDO` | Sí | `FIR_RECO` | Sí | "Error al verificar la publicación en Alfresco" |
| `SignatureRecoveryRequiredError` | Reconciliación | `SUBIENDO` | Sí | `FIR_RECO` | Sí | "Se requiere reconciliación de intento" |
| `SignatureIdentityError` | Identidad | N/A | No | `SIS_IDER` si no hay `firid` | No | "Error al validar la identidad del firmante" |

**Nota sobre eventos previos a la reserva:** Los errores `SignatureNotAllowedError` e `SignatureIdentityError` pueden ocurrir antes de crear `docfirma`. Si no existe `firid`, no se registrará `FIR_FALL`. En su lugar se registrará:
- `enttip = PARTICIPANTE` y `entid = parid` (evento `PAR_FDEN`) si existe participante.
- `enttip = PASO` y `entid = dpasid` (evento `PAS_FDEN`) si el fallo pertenece al paso.
- `enttip = SISTEMA` (evento `SIS_IDER`) si el proveedor de identidad falla.
`FIR_FALL` solo se registra si `docfirma` ya existe.

## 32. Matriz de Riesgos (13 Escenarios)

| # | Riesgo | Prob. | Imp. | Prevención | Detección | Recuperación | Riesgo Res. |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Timeout post-carga | Media | Alto | Comentario con `opeid` | Reconciliación | Reconciliador busca `opeid` | Bajo |
| 2 | Versión duplicada | Media | Alto | SUBIENDO antes de HTTP; no reintento ciego | Historial Alfresco por `opeid`; conteo coincidencias | 1 coincidencia=validar; 0=reintentar con evidencia; >1=CONFLICTO | Bajo |
| 3 | Modificación externa | Baja | Alto | Revalidación antes del POST | Hash/versión mismatch | CONFLICTO; FIR_CONF | Bajo |
| 4 | Hash divergente | Baja | Alto | Recalcular hash local vs remoto | Hash mismatch | CONFLICTO | Bajo |
| 5 | PDF malicioso/corrupto | Baja | Medio | Parseo PyMuPDF; validación estructura | Parser Error | FALLIDA | Bajo |
| 6 | Coordenadas fuera de rango | Media | Medio | Bounds check contra dimensiones reales del PDF | Validation Error | Rechazar petición | Bajo |
| 7 | Reutilización de firma | Media | Alto | Destruir PNG en `finally`; TTL backup | Auditoría | Eliminación FS inmediata | Bajo |
| 8 | Firmantes simultáneos | Media | Alto | Index parcial activo por `docid` | Unique Constraint | Serialización transaccional | Bajo |
| 9 | Doble proceso por nodid | Baja | Alto | Index parcial activo en `docfir` | Unique Constraint | Rechazar inicio de proceso | Bajo |
| 10 | Temporal abandonado | Baja | Bajo | Guardar en `FIRMADOC_TMP_DIR` | Cron de purga | Purga por TTL | Bajo |
| 11 | Identidad no disponible | Baja | Alto | Fail-closed identity resolver | Identity Error | Abortar intento | Bajo |
| 12 | Auditoría incompleta pre-POST | Baja | Alto | Misma TX SQL antes de HTTP | TX Rollback | Rollback completo, no POST | Bajo |
| 13 | Commit local fail post HTTP | Rara | Alto | TX cortas | Reconciliación | Reconciliador recupera `opeid`; no re-POST | Bajo |

## 33. Matriz Detallada de Pruebas Esperadas

### Modelo y Constraints

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `test_docfirma_creacion_valida` | ORM | docpart, docpaso | Insertar registro con UUID y estado INICIADA | revnum=1, firid asignado |
| 2 | `test_docfirma_fk_docid_invalida` | ORM | N/A | Insertar con docid inexistente | IntegrityError FK |
| 3 | `test_docfirma_fk_parid_invalida` | ORM | N/A | Insertar con parid inexistente | IntegrityError FK |
| 4 | `test_docfirma_opeid_duplicado` | ORM | docfirma existente | Insertar con mismo opeid | IntegrityError UNIQUE |
| 5 | `test_docfirma_secuen_duplicada` | ORM | docfirma existente | Insertar con mismo (docid, secuen) | IntegrityError UNIQUE |
| 6 | `test_docfirma_intnum_duplicado` | ORM | docfirma existente | Insertar con mismo (parid, intnum) | IntegrityError UNIQUE |
| 7 | `test_docfirma_estado_invalido` | ORM | docpart | Insertar con estado='INEXISTENTE' | IntegrityError CHECK |
| 8 | `test_docfirma_tipfir_invalido` | ORM | docpart | Insertar con tipfir='DIGITAL' | IntegrityError CHECK |
| 9 | `test_docfirma_revnum_invalido` | ORM | docpart | Insertar con revnum=0 | IntegrityError CHECK |
| 10 | `test_docfirma_hasori_invalido` | ORM | docpart | Insertar con hasori='ABCG' | IntegrityError CHECK |
| 11 | `test_docfirma_hasfin_invalido` | ORM | docpart | Actualizar hasfin='XYZ' | IntegrityError CHECK |
| 12 | `test_docfirma_errcod_req_fallida` | ORM | docfirma INICIADA | Pasar a FALLIDA sin errcod | IntegrityError CHECK |
| 13 | `test_docfirma_errcod_req_conflicto` | ORM | docfirma INICIADA | Pasar a CONFLICTO sin errcod | IntegrityError CHECK |
| 14 | `test_docfirma_motivo_req_cancelada` | ORM | docfirma INICIADA | Pasar a CANCELADA sin motivo | IntegrityError CHECK |
| 15 | `test_docfirma_fecfin_req_terminal` | ORM | docfirma | Pasar a COMPLETADA sin fecfin | IntegrityError CHECK |
| 16 | `test_docfirma_fecfin_prohibido_operativo` | ORM | docfirma INICIADA | Asignar fecfin en estado INICIADA | IntegrityError CHECK |
| 17 | `test_docfirma_hasfin_req_desde_generada` | Servicio | docfirma | Pasar a GENERADA sin hasfin | Error de validación |
| 18 | `test_docfirma_verfin_req_desde_cargada` | Servicio | docfirma | Pasar a CARGADA sin verfin | Error de validación |
| 19 | `test_docfirma_operacion_activa_duplicada` | ORM | docfirma INICIADA | Insertar segundo intento INICIADA para mismo docid | IntegrityError UNIQUE parcial |
| 20 | `test_docfirma_completada_duplicada` | ORM | docfirma COMPLETADA | Insertar segunda COMPLETADA para mismo parid | IntegrityError UNIQUE parcial |
| 20A | `test_docfirma_hasfin_estado` | ORM | docfirma | Insertar GENERADA sin hasfin | IntegrityError CHECK |
| 20B | `test_docfirma_verfin_estado` | ORM | docfirma | Insertar CARGADA sin verfin | IntegrityError CHECK |
| 20C | `test_docfirma_result_estado` | ORM | docfirma | Insertar VERIFICANDO sin result | IntegrityError CHECK |
| 20D | `test_docfirma_fecmod_inicializado` | ORM | docfirma | Insertar INICIADA sin fecmod | fecmod no es null y coincide con el timestamp transaccional de creación. |
| 20E | `test_docfirma_fecmod_actualizado_transicion` | ORM | docfirma | Transición de estado o result | fecmod se actualiza al nuevo timestamp |
| 20F | `test_finalizacion_idempotente_no_actualiza_fecmod` | Servicio | docfirma COMPLETADA | Ejecutar finalize | fecmod no se modifica |
| 20G | `test_docfirma_completada_limpia` | ORM | docfirma | Insertar COMPLETADA con errcod | IntegrityError CHECK |

### firpos

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 21 | `test_firpos_posicion_valida` | ORM | docfirma | Insertar con coordenadas válidas | posid asignado |
| 22 | `test_firpos_multiples_posiciones` | ORM | docfirma | Insertar 3 posiciones con ordenes 1,2,3 | 3 registros creados |
| 23 | `test_firpos_pagina_cero` | ORM | docfirma | Insertar con pagina=0 | IntegrityError CHECK |
| 24 | `test_firpos_pagina_inexistente` | Servicio | docfirma, PDF | Insertar con pagina=999 | SignaturePlacementError |
| 25 | `test_firpos_posx_negativa` | ORM | docfirma | Insertar con posx=-1 | IntegrityError CHECK |
| 26 | `test_firpos_posy_negativa` | ORM | docfirma | Insertar con posy=-1 | IntegrityError CHECK |
| 27 | `test_firpos_ancho_cero` | ORM | docfirma | Insertar con ancho=0 | IntegrityError CHECK |
| 28 | `test_firpos_alto_cero` | ORM | docfirma | Insertar con alto=0 | IntegrityError CHECK |
| 29 | `test_firpos_orden_cero` | ORM | docfirma | Insertar con orden=0 | IntegrityError CHECK |
| 30 | `test_firpos_orden_duplicado` | ORM | firpos existente | Insertar con mismo (firid, orden) | IntegrityError UNIQUE |
| 31 | `test_firpos_rotacion_invalida` | ORM | docfirma | Insertar con rotaci=45 | IntegrityError CHECK |
| 32 | `test_firpos_camid_null` | ORM | docfirma | Insertar sin camid | posid asignado |
| 33 | `test_firpos_camid_valido` | ORM | docfirma, tplcamp | Insertar con camid existente | posid asignado |
| 34 | `test_firpos_snapshot_inmutable` | ORM+Servicio | firpos, tplcamp | Modificar tplcamp.posx después de crear firpos | firpos.posx sin cambios |
| 34A | `test_reserva_atomica_exitosa` | Servicio | payload válido | Crear intento con 5 posiciones | 1 docfirma y 5 firpos creados en 1 TX |
| 34B | `test_reserva_atomica_rollback` | Servicio | 1 pos inválida | Crear intento 4 pos válidas, 1 inválida | Rollback completo, 0 filas en BD |

### Autorización e Identidad

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 35 | `test_autorizacion_actor_correcto` | Servicio | docpart asignado | Firmar con usrid coincidente | Firma procesada |
| 36 | `test_autorizacion_actor_incorrecto` | Servicio | docpart asignado | Firmar con usrid diferente | SignatureNotAllowedError |
| 37 | `test_autorizacion_capacidad_ausente` | Servicio | docpart sin rol | Firmar sin capacidad | SignatureNotAllowedError |
| 38 | `test_autorizacion_participante_pendiente` | Servicio | docpart PENDIENTE | Intentar firmar | SignatureNotAllowedError |
| 39 | `test_autorizacion_participante_disponible` | Servicio | docpart DISPONIBLE | Intentar firmar | Firma procesada |
| 40 | `test_autorizacion_participante_en_proceso` | Servicio | docpart EN_PROCESO | Intentar firmar | Firma procesada |
| 41 | `test_autorizacion_participante_terminal` | Servicio | docpart COMPLETADO | Intentar firmar | SignatureAlreadyCompletedError |
| 42 | `test_autorizacion_paso_no_firmar` | Servicio | docpaso tipo REVISAR | Intentar firmar | SignatureNotAllowedError |
| 43 | `test_identidad_fail_closed` | Servicio | ProductionResolver | Firmar | SignatureIdentityError |
| 44 | `test_identidad_inyectada_rechazada` | Servicio | payload con nomcom | Enviar nomcom en body | ValidationError de Pydantic por extra = forbid |
| 45 | `test_verlock_obsoleto` | Servicio | docpart | Firmar con verlock antiguo | SignatureConcurrencyError |
| 46 | `test_revnum_obsoleto` | Servicio | docfirma | Finalizar con revnum antiguo | SignatureConcurrencyError |
| 46A | `test_auditoria_denegacion_sin_firid_par` | Servicio | docpart sin capacidad | Denegar antes de reserva | PAR_FDEN registrado, sin FIR_FALL |
| 46B | `test_auditoria_denegacion_sin_firid_sis` | Servicio | falla identidad | Denegar antes de reserva | SIS_IDER registrado, sin FIR_FALL |

### Firma Manuscrita

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 47 | `test_manuscrita_png_valido` | Servicio | PNG real | Validar y procesar | Firma procesada |
| 48 | `test_manuscrita_magic_bytes_falsos` | Servicio | JPEG renombrado | Validar | SignaturePayloadError |
| 49 | `test_manuscrita_mime_falso` | Servicio | GIF con extension .png | Validar | SignaturePayloadError |
| 50 | `test_manuscrita_trazo_vacio` | Servicio | PNG blanco uniforme | Validar | SignaturePayloadError |
| 51 | `test_manuscrita_archivo_excesivo` | Servicio | PNG > MAX_SIZE | Validar | SignaturePayloadError |
| 52 | `test_manuscrita_dimensiones_excesivas` | Servicio | PNG 10000x10000 | Validar | SignaturePayloadError |
| 53 | `test_manuscrita_temporal_eliminado_exito` | Servicio | PNG válido | Procesar exitosamente | Archivo no existe en FS |
| 54 | `test_manuscrita_temporal_eliminado_error` | Servicio | Fallo en PyMuPDF | Procesar con error | Archivo no existe en FS |
| 55 | `test_manuscrita_no_reutilizable` | Servicio | Intentar usar mismo PNG | Segundo intento | SignaturePayloadError |
| 56 | `test_manuscrita_operador_asistido` | Servicio | Operador autorizado | Firmar asistiendo | Firma procesada y operador auditado |
| 57 | `test_manuscrita_externo_sin_modelo` | Servicio | Sin identidad institucional | Firmar | SignatureIdentityError |

### Firma Interna

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 58 | `test_interna_marca_backend` | Servicio | docpart congelado | Generar marca | Contiene nomcom y rolpro |
| 59 | `test_interna_nombre_desde_docpart` | Servicio | docpart | Verificar marca | Nombre = docpart.nomcom |
| 60 | `test_interna_rol_desde_docpart` | Servicio | docpart | Verificar marca | Rol = docpart.rolpro |
| 61 | `test_interna_fecha_desde_servidor` | Servicio | docpart | Verificar marca | Fecha/hora del servidor |
| 62 | `test_interna_texto_cliente_rechazado` | Servicio | payload con texto extra | Enviar texto de marca | ValidationError de Pydantic por extra = forbid |
| 63 | `test_interna_no_usa_certificado` | Servicio | N/A | Verificar que no haya PKI | Sin referencia a X.509 |
| 64 | `test_interna_no_acepta_imagen` | Servicio | Imagen en payload | Enviar imagen | SignaturePayloadError |

### PyMuPDF

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 65 | `test_pymupdf_pdf_valido` | Servicio | PDF real | Estampar firma | PDF modificado válido |
| 66 | `test_pymupdf_archivo_no_pdf` | Servicio | Archivo .txt | Abrir con PyMuPDF | SignaturePdfError |
| 67 | `test_pymupdf_pdf_corrupto` | Servicio | PDF truncado | Abrir | SignaturePdfError |
| 68 | `test_pymupdf_pdf_cifrado` | Servicio | PDF con password | Abrir | SignaturePdfError |
| 69 | `test_pymupdf_pagina_inexistente` | Servicio | PDF 3 páginas | Estampar en página 5 | SignaturePlacementError |
| 70A | `test_pymupdf_coordenadas_exactas` | Servicio | PDF real | Rectángulo exactamente dentro de página | Firma procesada |
| 70B | `test_pymupdf_coordenadas_exceden_limite` | Servicio | PDF real | Rectángulo excede límite por 0.0001 puntos | SignaturePlacementError |
| 71 | `test_pymupdf_rotacion` | Servicio | PDF con rotación | Estampar | Marca estampada con rotación aplicada |
| 72 | `test_pymupdf_varias_posiciones` | Servicio | firpos × 3 | Estampar | 3 marcas en el PDF |
| 73 | `test_pymupdf_hash_reproducible` | Servicio | Mismo PDF + misma firma | Estampar 2 veces | Mismo hasfin |
| 74 | `test_pymupdf_limpieza_recursos` | Servicio | PDF | Abrir y cerrar | Handles liberados |

### Estados y Transiciones

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 75 | `test_transicion_iniciada_generada` | Servicio | docfirma INICIADA | Transicionar | GENERADA con hasfin |
| 76 | `test_transicion_generada_subiendo` | Servicio | docfirma GENERADA | Transicionar | SUBIENDO |
| 77 | `test_transicion_invalida_iniciada_cargada` | Servicio | docfirma INICIADA | Intentar CARGADA | Error transición |
| 78 | `test_terminal_inmutable` | Servicio | docfirma COMPLETADA | Intentar FALLIDA | Error transición |
| 79 | `test_cancelacion_antes_post` | Servicio | docfirma GENERADA | Cancelar con motivo | CANCELADA |
| 80 | `test_cancelacion_prohibida_post` | Servicio | docfirma SUBIENDO | Cancelar | Error (reconciliación requerida) |
| 81 | `test_conflicto_revalidacion` | Servicio | Versión cambió | Revalidar | CONFLICTO + FIR_CONF |
| 82 | `test_fallo_confirmado` | Servicio | Error de parseo | Procesar | FALLIDA + errcod |

### Revalidación Previa al POST

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 83 | `test_revalidacion_misma_version` | Servicio | Alfresco sin cambios | Revalidar | Continúa a SUBIENDO |
| 84 | `test_revalidacion_version_diferente` | Servicio | Alfresco actualizado | Revalidar | CONFLICTO |
| 85 | `test_revalidacion_hash_diferente` | Servicio | Contenido cambiado | Revalidar | CONFLICTO |
| 86 | `test_revalidacion_nodid_inexistente` | Servicio | Nodo eliminado | Revalidar | CONFLICTO |
| 87 | `test_revalidacion_no_pdf` | Servicio | MIME cambiado | Revalidar | CONFLICTO |

### Alfresco (HTTP)

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 88 | `test_alfresco_401` | Servicio | Mock 401 | POST | SignatureUploadError |
| 89 | `test_alfresco_403` | Servicio | Mock 403 | POST | SignatureUploadError |
| 90 | `test_alfresco_404` | Servicio | Mock 404 | POST | SignatureUploadError |
| 91 | `test_alfresco_409` | Servicio | Mock 409 | POST | SignatureVersionConflictError |
| 92 | `test_alfresco_412` | Servicio | Mock 412 | POST | SignatureVersionConflictError |
| 93 | `test_alfresco_413` | Servicio | Mock 413 | POST | SignaturePayloadError |
| 94 | `test_alfresco_timeout` | Servicio | Mock timeout | POST | SignatureRecoveryRequiredError |
| 95 | `test_alfresco_5xx` | Servicio | Mock 500 | POST | SignatureUploadError |
| 96 | `test_alfresco_respuesta_inesperada` | Servicio | Mock body inválido | POST | SignatureUploadError |
| 97 | `test_alfresco_2xx_sin_verfin` | Servicio | Mock 201 sin version | POST | SignatureUploadError |
| 82A | `test_transicion_local_lock` | Servicio | docfirma GENERADA | Avanzar a SUBIENDO | Solo docfirma es bloqueada en TX corta |
| 82B | `test_prohibicion_parent_locks` | Servicio | TX transicion local | Intentar FOR UPDATE en docpaso | Error transaccional/arquitectónico forzado |

### Reconciliación

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 98 | `test_reconciliacion_cero_primer_intento` | Servicio | Mock sin versiones | Reconciliar | Continúa reintentando |
| 99 | `test_reconciliacion_cero_tras_reintentos_incierto` | Servicio | Mock sin versiones × N | Reconciliar | SignatureRecoveryRequiredError |
| 100 | `test_reconciliacion_cero_con_evidencia_ausencia` | Servicio | Alfresco OK, nodo accesible | Reconciliar con evidencia | FALLIDA |
| 101 | `test_reconciliacion_una_hash_correcto` | Servicio | Mock 1 versión match | Reconciliar | COMPLETADA |
| 102 | `test_reconciliacion_una_hash_incorrecto` | Servicio | Mock 1 versión mismatch | Reconciliar | CONFLICTO |
| 103 | `test_reconciliacion_varias_coincidencias` | Servicio | Mock 2 versiones | Reconciliar | CONFLICTO + DUPLICATE_REMOTE_VERSION |
| 104 | `test_reconciliacion_repetida` | Servicio | docfirma COMPLETADA | Reconciliar otra vez | Retorna éxito idempotente sin modificar BD |
| 105 | `test_reconciliacion_concurrente` | Servicio | 2 hilos | Reconciliar simultáneo | Lock serializa, segundo hilo retorna éxito idempotente |
| 106 | `test_reconciliacion_no_segundo_post` | Servicio | Estado SUBIENDO | Reconciliar | No se ejecuta POST |
| 107 | `test_commit_local_fallido_post_http` | Servicio | Fallo commit | Reconciliar | Recupera vía opeid |
| 107A | `test_ausencia_no_demostrada` | Servicio | timeout 1 consulta | Reconciliar sin historial completo | Mantiene SUBIENDO y SignatureRecoveryRequiredError |
| 107B | `test_historial_paginado_completo` | Servicio | Mock 3 páginas | Reconciliar 0 coincidencias | Consultadas todas las páginas, marca FALLIDA |

### Finalización (finalize_verified_signature)

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 108 | `test_finalizacion_normal` | Servicio | docfirma VERIFICANDO | Finalizar | COMPLETADA |
| 109 | `test_finalizacion_desde_reconciliacion` | Servicio | docfirma reconciliada | Finalizar | COMPLETADA (mismo servicio) |
| 110 | `test_finalizacion_orden_locks` | Servicio | docfirma | Verificar orden | docpaso→docpart→docfir→docfirma |
| 111 | `test_finalizacion_stale_revnum` | Servicio | revnum desactualizado | Finalizar | SignatureConcurrencyError |
| 112 | `test_finalizacion_stale_verlock` | Servicio | verlock desactualizado | Finalizar | SignatureConcurrencyError |
| 113 | `test_finalizacion_docpart_una_vez` | Servicio | docpart ya COMPLETADO | Finalizar | Idempotente, no duplica |
| 114 | `test_finalizacion_docpaso_una_vez` | Servicio | docpaso ya resuelto | Finalizar | Idempotente, no duplica |
| 115 | `test_finalizacion_siguiente_paso_una_vez` | Servicio | Siguiente paso ya activo | Finalizar | Idempotente |
| 116 | `test_finalizacion_auditoria_una_vez` | Servicio | docfirma | Finalizar | Un solo FIR_COMP |
| 116A | `test_finalizacion_idempotente` | Servicio | docfirma COMPLETADA | Ejecutar finalize_verified_signature | Retorna éxito sin modificar BD |
| 116B | `test_finalizacion_repetida_sin_auditoria` | Servicio | docfirma COMPLETADA | Ejecutar finalize | No registra FIR_COMP duplicado |
| 116C | `test_finalizacion_completada_hash_dif` | Servicio | docfirma COMPLETADA | Ejecutar con hash alterado | SignatureIntegrityError |
| 116D | `test_finalizacion_result_requerido` | Servicio | docfirma VERIFICANDO sin result | Ejecutar finalize | ValidationError/IntegrityError |

### Interacciones y Concurrencia

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 117 | `test_firma_vs_vencimiento` | Servicio | docpart DISPONIBLE | Firmar y vencer | Lock serializa |
| 118 | `test_firma_vs_rechazo` | Servicio | docpart EN_PROCESO | Firmar y rechazar | Lock serializa |
| 119 | `test_firma_vs_omision` | Servicio | docpart DISPONIBLE | Firmar y omitir | Lock serializa |
| 120 | `test_firma_vs_cancelacion` | Servicio | docfirma GENERADA | Firmar y cancelar | Lock serializa |
| 121 | `test_dos_participantes_simultaneos` | Servicio | 2 docpart | Firmar en paralelo | Index parcial impide |
| 122 | `test_dos_procesos_nodid` | Servicio | 2 docfir mismo nodid | Crear | Index parcial impide |
| 123 | `test_secuen_unica` | Servicio | 2 intentos | Asignar secuen | Sin duplicados |
| 124 | `test_intnum_unico` | Servicio | 2 intentos | Asignar intnum | Sin duplicados |

### Integración docfir

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 125 | `test_docfir_firmado_parcial` | Servicio | Firmas pendientes | Completar una | FIRMADO_PARCIAL |
| 126 | `test_docfir_en_curso` | Servicio | Sin firmas, con pasos | Completar firmas | EN_CURSO |
| 127 | `test_docfir_completado` | Servicio | Sin firmas, sin pasos | Completar firmas | COMPLETADO |
| 128 | `test_docfir_pendiente_publicacion` | Servicio | Configuración extra | Completar firmas | PENDIENTE_PUBLICACION solo con config |
| 129 | `test_docfir_no_estados_inexistentes` | Servicio | N/A | Verificar enums | No FIRMADO, no CERRADO |

### Auditoría

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 130 | `test_auditoria_rollback_pre_http` | Servicio | Fallo antes de POST | Verificar | No FIR_SUBE registrado |
| 131 | `test_auditoria_recuperacion_post_http` | Servicio | Fallo después de POST | Reconciliar | FIR_RECO registrado |
| 132 | `test_auditoria_fir_inic` | ORM | docfirma creada | Verificar audifir | FIR_INIC presente |
| 133 | `test_auditoria_fir_gene` | ORM | docfirma GENERADA | Verificar audifir | FIR_GENE presente |
| 134 | `test_auditoria_fir_conf` | ORM | Conflicto detectado | Verificar audifir | FIR_CONF presente |
| 135 | `test_auditoria_fir_sube` | ORM | docfirma SUBIENDO | Verificar audifir | FIR_SUBE presente |
| 136 | `test_auditoria_fir_carg` | ORM | docfirma CARGADA | Verificar audifir | FIR_CARG presente |
| 137 | `test_auditoria_fir_veri` | ORM | docfirma VERIFICANDO | Verificar audifir | FIR_VERI presente |
| 138 | `test_auditoria_fir_comp` | ORM | docfirma COMPLETADA | Verificar audifir | FIR_COMP presente |
| 139 | `test_auditoria_fir_fall` | ORM | docfirma FALLIDA | Verificar audifir | FIR_FALL presente |
| 140 | `test_auditoria_fir_canc` | ORM | docfirma CANCELADA | Verificar audifir | FIR_CANC presente |
| 141 | `test_auditoria_fir_reco` | ORM | Reconciliación | Verificar audifir | FIR_RECO presente |
| 142 | `test_auditoria_no_secretos` | ORM | Cualquier evento | Verificar detalle | Sin tokens/passwords |
| 143 | `test_auditoria_no_binarios` | ORM | Cualquier evento | Verificar detalle | Sin base64/PNG |
| 144 | `test_auditoria_no_datos_clinicos` | ORM | Cualquier evento | Verificar detalle | Sin datos clínicos |

### Alcance

| # | Nombre | Capa | Fixture | Acción | Resultado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 145 | `test_no_pdf_en_postgresql` | ORM | docfirma | Verificar columnas | Sin columnas bytea |
| 146 | `test_no_png_persistente` | ORM | docfirma | Verificar columnas | Sin columnas de imagen |
| 147 | `test_no_base64` | ORM | docfirma | Verificar columnas | Sin columnas base64 |
| 148 | `test_no_certificados` | ORM | docfirma | Verificar tipfir | Solo MANUSCRITA/INTERNA |
| 149 | `test_no_docevid` | ORM | N/A | Verificar tablas | docevid no existe aún |
| 150 | `test_no_docpubl` | ORM | N/A | Verificar tablas | docpubl no existe aún |
| 151 | `test_no_credenciales` | Config | N/A | Verificar código | Sin secrets hardcodeados |
| 152 | `test_no_admin_alfresco` | Config | N/A | Verificar config | Sin usuario admin |
| 153 | `test_no_escritura_alfresco_unitarias` | Servicio | Mock Alfresco | Pruebas unitarias | Mock, no escritura real |

## 34. Límite con docevid

`docfirma` administra: operación, estados, hashes, versiones, posiciones, idempotencia y errores.

`docevid` se reserva para: manifiesto legal extendido de evidencia (cadena de custodia, IPs, User-Agent legal, timestamps firmados, exportación jurídica, adjuntos probatorios). No se duplican responsabilidades.

## 35. Clasificación de Dependencias

| Dependencia | Bloquea Diseño | Bloquea Implementación | Bloquea Integración | Bloquea Producción |
| :--- | :--- | :--- | :--- | :--- |
| Proveedor Institucional Identidad | No | No (Usa Mock fail-closed) | Sí | Sí |
| Credenciales REST Alfresco | No | No (Usa Mock service) | Sí | Sí |
| Autorización de escritura Alfresco | No | No | Sí | Sí |
| Entorno Alfresco accesible | No | No | Sí | Sí |
| Texto institucional de firma interna | No | No | No | Sí |
| Motor PyMuPDF | No | No | No | No |

## 36. Entorno Objetivo

- La suite histórica actual se ejecuta con Python 3.10.11.
- El stack objetivo obligatorio del proyecto es Python 3.12.
- Antes de implementar `docfirma` debe crearse o validarse un entorno Python 3.12.
- La suite completa debe ejecutarse y aprobarse en Python 3.12.
- No se puede declarar compatibilidad final con el entorno objetivo usando únicamente Python 3.10.11.
- No se modifica el entorno en esta tarea documental.

## 37. Confirmación de Alcance e Invariables

- No se implementaron modelos ORM ni migraciones en esta tarea.
- No se modificó código productivo en `backend/`.
- No se modificaron pruebas.
- No se generó migración.
- No se agregó dependencia.
- PyMuPDF permanece como motor aprobado. PyPDF y ReportLab quedan excluidas.
- No se escribió en Alfresco. Alfresco permaneció en solo lectura.
- No se implementó `docevid`.
- No se implementó `docpubl`.