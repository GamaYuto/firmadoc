# Diseño Arquitectónico: Módulo Participantes (docpart)

## 1. Resumen ejecutivo
Este documento establece oficialmente la arquitectura funcional, técnica y de datos para el módulo `docpart` en FirmaDoc V1. Se define a `docpart` como la entidad responsable de gestionar la asignación y el ciclo de vida individual de los participantes dentro de un paso documental (`docpaso`). El diseño resuelve de forma estricta los mecanismos transaccionales de concurrencia agregada, reactivación sobre la misma fila, resolución de identidad centralizada e integridad estructural sin redundancias, garantizando que el paso superior reciba siempre transiciones atómicas y seguras.

## 2. Objetivo
Cerrar el modelo de participantes para iniciar su implementación técnica, garantizando inmutabilidad, idempotencia, concurrencia orquestada y separación de responsabilidades con respecto a las firmas criptográficas.

## 3. Alcance
- Modelo de datos físico y restricciones de base de datos de `docpart`.
- Máquina de estados, transiciones y reactivación sistémica.
- Reglas de agregación hacia el estado del `docpaso` (TODOS, UNO).
- Protocolo estricto de concurrencia mediante `SELECT FOR UPDATE`.
- Definición de servicios e índices.
- Identidad e integración futura.
- **Fuera de alcance:** Implementación de código, endpoints REST, integración con Alfresco, generación de evidencias técnicas, almacenamiento de firmas, imágenes o binarios, delegaciones, suplencias, grupos dinámicos, recordatorios.

## 4. Modelo físico

Tabla `docpart`:
- `parid` (BigInteger): Identificador único autoincremental. PK. No modificable.
- `dpasid` (BigInteger): FK a `docpaso.dpasid`. No modificable tras crear.
- `usrid` (Varchar 60): Identificador estable congelado del usuario. No modificable tras crear.
- `nomcom` (Varchar 150): Nombre visible congelado. No modificable tras crear.
- `correo` (Varchar 100): Correo electrónico congelado. No modificable tras crear.
- `rolpro` (Varchar 100, nullable): Rol o cargo congelado. No modificable tras crear.
- `orden` (Integer): Orden operativo (para SECUENCIAL). Inicia en 1. No modificable tras activación del paso.
- `obliga` (Boolean): Indica si su participación bloquea la resolución (`true` por defecto). Obligatorio.
- `estado` (Varchar 20): Estado operacional único y fuente de verdad.
- `motivo` (Varchar 500, nullable): Justificación exigida para rechazo/omisión/cancelación.
- `result` (JSONB, nullable): Resultados opcionales funcionales (no almacena firmas, PDF, imágenes ni hashes).
- `fecdis` (Timestamptz, nullable): Fecha de pase a DISPONIBLE.
- `fecini` (Timestamptz, nullable): Fecha del primer evento a EN_PROCESO.
- `fecfin` (Timestamptz, nullable): Fecha de alcance de un estado terminal.
- `verlock` (Integer): Bloqueo optimista, inicia en 1, obligatorio.
- `usrcre` (Varchar 60): Usuario creador.
- `feccre` (Timestamptz): Fecha de creación y asignación. Sustituye a `fecasg`.
- `usrmod` (Varchar 60, nullable): Último modificador.
- `fecmod` (Timestamptz, nullable): Última modificación.

*Nota:* Se han eliminado las columnas `docid` (para evitar inconsistencias relacionales, navegando mediante `docpaso`) y `activo` (para consolidar `estado` como única fuente de verdad).

## 5. Constraints definitivas
- **PK**: `parid`
- **FK**: `dpasid` REFERENCES `docpaso(dpasid)` ON DELETE RESTRICT (no hay cascada destructiva).
- **UNIQUE**: `(dpasid, usrid)` - Garantiza un solo registro por usuario en un mismo paso.
- **UNIQUE**: `(dpasid, orden)` - Garantiza consistencia en modalidad secuencial.
- **CHECK `orden`**: `orden > 0`.
- **CHECK `verlock`**: `verlock >= 1`.
- **CHECK `estado`**: In ('PENDIENTE', 'DISPONIBLE', 'EN_PROCESO', 'COMPLETADO', 'RECHAZADO', 'OMITIDO', 'CANCELADO', 'VENCIDO').
- **CHECK `motivo`**: Obligatorio si el estado es 'RECHAZADO', 'OMITIDO' o 'CANCELADO'.
- **CHECK `fecfin`**: `fecfin >= fecini` cuando ambas existan lógicamente.
- **CHECK `fecdis`**: Coherente con estados DISPONIBLE y posteriores.

## 6. Identidad congelada y fuente de identidad
La asignación tomará una "fotografía" de los datos del usuario, los cuales seguirán siendo válidos aunque este cambie de nombre, correo, rol, o desaparezca del directorio.

**Mecanismo actual:** El proyecto utiliza actualmente la cabecera `X-FirmaDoc-User` (Varchar 60) sin un modelo o tabla local de usuarios con integridad referencial.

**Decisión obligatoria (Dependencia explícita):**
- `docpart` **no aceptará como confiables** los campos `nomcom`, `correo` y `rolpro` desde el frontend.
- La operación de asignación recibirá exclusivamente el `usrid`.
- Es dependencia de implementación construir y consumir un componente centralizado conceptual de identidad que resuelva los datos seguros:
  `resolve_user(usrid) -> IdentitySnapshot`
- La asignación **debe fallar** si el usuario no puede validarse en este componente centralizado.
- No se creará una FK ficticia para `usrid`.

## 7. Configuración congelada
Las estrategias de resolución se leerán y congelarán exclusivamente en `docpaso.config`. 
Claves oficiales:
```json
{
  "part_estrategia": "TODOS",
  "part_modo": "PARALELO",
  "rechazo_inmediato": true
}
```
**Valores permitidos:**
- `part_estrategia`: TODOS, UNO
- `part_modo`: PARALELO, SECUENCIAL
- `rechazo_inmediato`: true, false

**Reglas:**
- Por defecto: TODOS, PARALELO, rechazo_inmediato = true. Pasos antiguos sin JSON aplicarán estos valores.
- Instanciación: Se congelan desde `flupaso` al instanciar `docpaso`.
- Inmutabilidad: Tras asignar el primer participante o dejar de estar PENDIENTE, estos valores son inmutables.
- Validaciones: Valores inválidos causarán error. No se usarán valores arbitrarios. Existirá una única función de servicio para interpretar estas claves y unificar el criterio entre `participant_service` y `step_service`.

## 8. Participantes obligatorios y opcionales
- **Regla estricta:** Todo `docpaso` que requiera intervención humana debe poseer **al menos un `docpart` con `obliga = true`**.
- No se permiten pasos compuestos únicamente por opcionales.
- Los participantes opcionales:
  - No bloquean la estrategia TODOS.
  - No satisfacen la estrategia UNO.
  - Al pasar a RECHAZADO (con motivo, dejando registro), no rechazan el paso.
  - Se cancelan sistémicamente si el paso finaliza mientras ellos siguen activos.

## 9. Ventana de asignación
- **PENDIENTE:** Se permite asignar nuevos participantes.
- **DISPONIBLE:** Solo si *ningún* participante ha iniciado (sin `fecini`, sin `EN_PROCESO`, y sin estados terminales). La operación debe reorganizar transaccionalmente.
- **EN_PROCESO o Estados Terminales:** Asignaciones **PROHIBIDAS**.
- Tras la activación de cualquier participante, queda **PROHIBIDO** modificar `usrid`, identidad, `orden` u `obliga`.

## 10. Máquina de estados y Transiciones
1. **PENDIENTE:** Espera turno secuencial o inicio.
2. **DISPONIBLE:** Habilitado para acción.
3. **EN_PROCESO:** El usuario inició su tarea.
4. **COMPLETADO:** Acción finalizada (Terminal).
5. **RECHAZADO:** Acción devuelta (Terminal).
6. **OMITIDO:** Saltado administrativamente (Terminal).
7. **VENCIDO:** Superó plazo (Terminal).
8. **CANCELADO:** Finalizado por el sistema (Terminal).

Toda transición debe procesar el estado anterior, estado nuevo, fecha correspondiente, actor, auditoría, motivo e incremento de `verlock`. Los estados terminales no aceptan transiciones ordinarias hacia estados activos.

## 11. Reactivación de docpaso y docpart
En V1, cuando `docpaso` transiciona de `VENCIDO` → `DISPONIBLE`, se reactiva sobre **las mismas filas docpart** (sin duplicar ni crear intentos nuevos).
- Participantes ya terminales (COMPLETADO, RECHAZADO, OMITIDO, CANCELADO) previo al vencimiento: Se conservan intocables.
- Participantes VENCIDO (Candidatos):
  - **PARALELO:** Pasan a `DISPONIBLE`, `fecdis = now`, `fecfin = null`, `verlock++`.
  - **SECUENCIAL:** El de menor orden pasa a `DISPONIBLE`, los demás VENCIDO pasan a `PENDIENTE` (limpiando `fecdis`), `fecfin = null`, `verlock++`.
- `fecini`: Se conserva inmutable como huella histórica si existía.
- Evento de auditoría: `PAR_REAC`.
- **Restricción:** Solo origina desde reactivación de `docpaso`. Prohibida la ejecución manual aislada.

## 12. Resolución TODOS
Solo resuelven los obligatorios (`obliga = true`).
- `COMPLETADO` si: Existe al menos un COMPLETADO; todos los demás obligatorios son COMPLETADO u OMITIDO; no hay rechazos bloqueantes y no quedan obligatorios activos.
- `OMITIDO` si: Todos los obligatorios son OMITIDO (sin ningún COMPLETADO ni rechazo bloqueante).
- Acción final: Cancela opcionales activos y no terminales, activa el siguiente `docpaso` y sus participantes una sola vez.

## 13. Resolución UNO
- `COMPLETADO` si: El primer participante obligatorio finaliza en COMPLETADO. Cancela al resto. OMITIDO no satisface.
- `OMITIDO` si: Todos los obligatorios terminan en OMITIDO.
- `RECHAZADO` si:
  - `rechazo_inmediato = true` y el primero rechaza. Cancela al resto.
  - `rechazo_inmediato = false` y *todos* rechazan (o la combinación de rechazados y omitidos agota el paso sin ganadores, existiendo al menos un rechazo).
  - Un rechazo con `rechazo_inmediato = false` no detiene la carrera mientras existan otros obligatorios operativos.

## 14. Omisión
Permiso conceptual explícito: `firmadoc.participant.omit`.
- El participante **no puede** omitirse a sí mismo.
- Solo un administrador autorizado puede invocarlo desde PENDIENTE, DISPONIBLE o EN_PROCESO, obligando motivo.
- Deja de bloquear en TODOS, pero no satisface en UNO. Activa al siguiente en SECUENCIAL. Incrementa `verlock`.

## 15. Concurrencia Agregada y Bloqueos
**Protocolo oficial bloqueante para evaluación de resolución de paso:**
1. Abrir transacción única.
2. Bloquear docpaso: `SELECT FOR UPDATE`.
3. Confirmar que docpaso es operativo (evita carrera de doble finalización).
4. Bloquear docpart objetivo y validar `expected_verlock`.
5. Actualizar docpart (`verlock++`).
6. Bloquear los demás `docpart` del paso (orden ascendente por `parid`) para evaluación.
7. Evaluar estrategias (TODOS/UNO) sobre vista consistente.
8. Si se resuelve, actualizar `docpaso` una vez.
9. Cancelar participantes restantes ordenadamente.
10. Si aplica, activar siguiente `docpaso` (previamente bloqueado `FOR UPDATE`) y a sus participantes en orden determinista.
11. Generar auditorías.
12. COMMIT. Rollback absoluto ante cualquier fallo. No usar commit interno en CRUD.

**Carreras explícitas mitigadas:**
- **Dos participantes completan en UNO:** La segunda TX en obtener el lock de docpaso verá que su participante ya fue CANCELADO o el paso completado. No vuelve a completar ni activar pasos.
- **Dos últimos en TODOS:** Serialización por docpaso. La segunda TX evalúa sobre la suma real y completa una sola vez.
- **Completar vs Vencer / Completar vs Omitir:** Lock serializado garantiza un único vencedor de estado atómico.

## 16. Auditoría
- Se debe añadir formalmente a `audifir` la entidad **`PARTICIPANTE`**.
- La inserción asociará `entid = parid`.
- Eventos: `PAR_ASIG`, `PAR_DISP`, `PAR_INIC`, `PAR_COMP`, `PAR_RECH`, `PAR_OMIT`, `PAR_VENC`, `PAR_CANC`, `PAR_REAC`.
- Contenido: Estado previo y nuevo, motivo, actor, fecha. `dpasid` y `docid` derivado irán como metadatos de contexto.
- *Prohibido utilizar `PASO` como sustituto semántico*. Esto requerirá modificación explícita en las migraciones/base de datos futura.

## 17. Índices
Respaldados por necesidades operativas estrictas:
- (Implícitos) UNIQUE `(dpasid, usrid)` y UNIQUE `(dpasid, orden)`.
- Índice por usuario en bandeja: `(usrid, estado)` o índice parcial donde estado en DISPONIBLE/EN_PROCESO.
- No se duplicarán índices que ya estén amparados por los constraints únicos.

## 18. Seguridad
- Solo el `usrid` principal validado contra el token de sesión puede completar o iniciar su propia participación. 
- Los actores administrativos de omisión/cancelación deben dejar un log cruzado (actor != usrid).
- Result sanitizado sin binarios.

## 19. Contrato con docfirma
- La relación física será de `docpart` (1) a `docfirma` (N).
- La FK estará alojada en `docfirma` (`docfirma.parid` -> `docpart.parid`).
- `docpart` **jamás almacenará** hashes, imágenes, identificadores de publicación ni certificados criptográficos.
- Hasta que se implemente `docfirma`, las pruebas de `docpart` podrán fingir éxito, pero el sistema no certificará que un documento fue firmado criptográficamente por la mera existencia de un `COMPLETADO`.
