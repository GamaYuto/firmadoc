# Arquitectura Documental y Modelo Conceptual de Firma

## 1. Propósito institucional
FirmaDoc es la plataforma institucional de diligenciamiento, revisión, aprobación y firma documental integrada con Alfresco. 

Debe servir para procesos transversales, incluyendo:
- Consentimientos informados
- Autorizaciones
- Documentos clínicos
- Tutelas
- Respuestas jurídicas
- Oficios
- Actas
- Documentos de gerencia
- Talento humano
- Contratación
- Compras
- Proveedores
- Procesos corporativos

## 2. Principios de arquitectura
Como principios obligatorios del sistema:
1. Alfresco es el repositorio documental oficial.
2. FirmaDoc administra procesos, plantillas, participantes, firmas y evidencia.
3. Cada firma pertenece a una versión exacta del documento.
4. Todo documento debe identificarse mediante nodeId, versión y hash.
5. No almacenar PDFs completos en PostgreSQL.
6. No almacenar firmas reutilizables como perfil permanente.
7. No sobrescribir silenciosamente documentos firmados.
8. No eliminar físicamente información auditada.
9. Toda operación crítica debe ser idempotente.
10. La auditoría debe formar parte de la transacción lógica.
11. La publicación en Alfresco debe tolerar fallos y reintentos.
12. La firma visible no sustituye la evidencia técnica.
13. Revisión, aprobación y firma son acciones diferentes.
14. Una firma no puede trasladarse a otro documento o versión.
15. Los documentos corregidos deben iniciar un nuevo proceso.

## 3. Glosario
- **Documento fuente**: Documento original en Alfresco sobre el cual se inicia el proceso.
- **Proceso documental**: Entidad lógica que agrupa un flujo de trabajo sobre una versión específica de un documento.
- **Plantilla**: Configuración que define la ubicación y comportamiento de los campos sobre el documento.
- **Campo**: Elemento interactivo (texto, fecha, firma, etc.) posicionado en la plantilla.
- **Flujo**: Secuencia definida de pasos a seguir para completar un proceso documental.
- **Paso**: Tarea específica dentro de un flujo asignada a un participante.
- **Participante**: Persona o entidad que interviene en un paso del proceso.
- **Firmante**: Participante cuyo paso requiere registrar una firma.
- **Operador asistido**: Usuario institucional que facilita y da fe del proceso de un firmante externo (ej. auxiliar con paciente).
- **Firma manuscrita capturada**: Trazo gráfico recogido en tiempo real mediante un dispositivo.
- **Firma electrónica autenticada**: Firma realizada mediante credenciales institucionales sin requerir trazo manual.
- **Evidencia**: Datos técnicos y de contexto recolectados para dar soporte jurídico y técnico a una acción.
- **Auditoría**: Registro inmutable de eventos ocurridos en el sistema.
- **Documento final**: Documento PDF generado que incorpora los campos diligenciados y las firmas.
- **Publicación**: Acción de subir el documento final a Alfresco como una nueva versión.
- **Versión inicial**: Versión del documento fuente al momento de iniciar el proceso.
- **Versión final**: Versión resultante en Alfresco tras la publicación.
- **Hash original**: Código hash del documento fuente descargado.
- **Hash final**: Código hash del documento final generado antes de publicar.
- **Rechazo**: Acción de devolver un proceso por inconformidad o error en un paso.
- **Cancelación**: Finalización forzada de un proceso documental sin llegar a su término natural.
- **Invalidación**: Estado de una firma o paso que pierde su efecto en el proceso.
- **Sustitución**: Acción de crear un nuevo proceso para corregir un documento previamente firmado.

## 4. Actores y roles

### Roles del sistema
Representan los permisos a nivel de la plataforma:
- ADMINISTRADOR
- GESTOR
- AUDITOR

### Roles dentro de un proceso
Representan la función específica en un flujo documental:
- DILIGENCIADOR
- REVISOR
- APROBADOR
- FIRMANTE_INTERNO
- OPERADOR_ASISTIDO
- FIRMANTE_EXTERNO
- TESTIGO

*Nota: Una persona puede tener un rol institucional y otro rol dentro de un proceso. Ejemplo: un usuario institucional "auxiliar asistencial" puede tener el rol de "operador asistido" en el proceso, donde el "firmante real" es el paciente.*

## 5. Entidades conceptuales

### Proceso documental
- identificador interno
- nodeId
- versión inicial
- versión final
- hash original
- hash final
- plantilla
- flujo
- estado
- usuario iniciador
- fechas
- resultado de publicación

### Flujo
- código
- nombre
- versión
- pasos
- orden
- reglas
- estados
- condiciones de finalización

### Paso
- tipo (DILIGENCIAR, REVISAR, APROBAR, FIRMAR, ATESTIGUAR, CERRAR, PUBLICAR)
- orden
- rol requerido
- participante asignado
- obligatoriedad
- estado
- fecha disponible
- fecha límite
- resultado
- motivo de rechazo

### Participante
- tipo de participante
- nombre
- identificación
- usuario institucional, si existe
- rol
- orden
- estado
- datos mínimos de contacto cuando corresponda

### Firmante
- interno autenticado
- externo asistido
- testigo
- representante
- paciente
- familiar
- proveedor
- directivo

### Firma
- proceso
- paso
- firmante
- documento
- nodeId
- versión
- hash
- campo de firma
- tipo de firma
- fecha y hora
- estado
- evidencia

### Evidencia
- código único
- proceso
- acción
- documento
- nodeId
- versión
- hash
- firmante
- identificación
- usuario institucional
- operador asistido
- rol
- fecha y hora
- zona horaria
- IP
- agente de usuario
- dispositivo
- declaración aceptada
- resultado
- motivo, cuando aplique

### Publicación Alfresco
- intento
- idempotency key
- versión objetivo
- hash final
- estado
- número de reintentos
- fecha de último intento
- error sanitizado
- versión confirmada en Alfresco

## 6. Tipos de firma de la versión 1

### MANUSCRITA_CAPTURADA
Aplicable a: pacientes, familiares, testigos, proveedores, externos y personas sin cuenta institucional.
- **Medios:** pantalla táctil, lápiz digital, mouse.
- **Retención de firma (Política oficial):**
  - La imagen cruda **no se conserva permanentemente**.
  - Se almacena temporalmente cifrada en el sistema de archivos o caché seguro.
  - Se **elimina** inmediatamente después de incorporarla al PDF final.
  - Se conserva su SHA-256 y la evidencia técnica en la base de datos (PostgreSQL no almacena imágenes).
  - Si falla la incorporación, el temporal tiene un TTL configurable (inicialmente 30 minutos).
  - Si falla Alfresco *después* de generar el PDF, se conserva temporalmente el PDF final cifrado (no la imagen cruda) para su reintento.
- **Reglas:** no reutilizable; vinculada a un único proceso; asociada a identidad declarada; asociada al operador asistido cuando corresponda; no almacenada como firma de perfil.

### ELECTRONICA_AUTENTICADA
Aplicable a: gerencia, jurídica, coordinadores, jefes y usuarios internos autorizados.
- **Reglas:** requiere autenticación institucional; exige confirmación explícita; vinculada a la versión exacta; registra usuario, rol, fecha, hora, IP y hash; puede representarse visualmente mediante nombre, cargo y sello; no requiere una imagen manuscrita permanente.

*Quedan fuera de la versión 1:* certificado digital, firma criptográfica, biometría, reconocimiento facial, OTP, SMS, firma pública remota, firma masiva, firma offline, sellado de tiempo certificado.

## 7. Estados del proceso
- **BORRADOR:** En preparación, se asignan participantes y campos. Se permite editar.
- **PREPARADO:** Listo para iniciar. No se permiten cambios estructurales.
- **EN_CURSO:** Pasos iniciales en ejecución.
- **PENDIENTE_FIRMA:** En espera de acciones de firma.
- **FIRMADO_PARCIAL:** Algunas firmas recopiladas, faltan otras.
- **PENDIENTE_PUBLICACION:** PDF final generado, firmas incorporadas, a la espera de ser enviado a Alfresco.
- **COMPLETADO:** PDF final publicado y confirmado en Alfresco. (Única definición aceptable).
- **RECHAZADO:** Proceso devuelto por un participante. Requiere corrección o nuevo proceso.
- **CANCELADO:** Terminado forzosamente sin éxito.
- **ERROR_PUBLICACION:** Falló el envío a Alfresco tras agotar reintentos inmediatos.

## 8. Estados del paso
- **PENDIENTE:** En cola, esperando su turno.
- **DISPONIBLE:** Listo para ser ejecutado por el participante.
- **EN_PROCESO:** El participante lo está gestionando.
- **COMPLETADO:** Acción ejecutada con éxito (no puede ejecutarse nuevamente).
- **RECHAZADO:** Acción devuelta (exige motivo).
- **OMITIDO:** Saltado deliberadamente (requiere autorización explícita).
- **CANCELADO:** Abortado por la finalización del proceso.
- **VENCIDO:** Superó fecha límite.

## 9. Estados de firma
- **PENDIENTE:** Esperando captura.
- **CAPTURADA:** La firma fue recibida y se encuentra cifrada temporalmente.
- **CONFIRMADA:** El firmante confirmó la acción legalmente.
- **INCORPORADA:** Forma parte del PDF final generado y se eliminó el temporal crudo.
- **INVALIDADA:** Se conserva como evidencia (log), pero pierde efecto en el proceso.

## 10. Estados de Publicación
- **PENDIENTE:** Intento de publicación encolado.
- **EN_PROCESO:** Enviando datos a Alfresco.
- **VERIFICANDO:** Validando que la versión se haya creado correctamente en Alfresco tras un error o timeout.
- **PUBLICADA:** Confirmada exitosamente en Alfresco.
- **ERROR:** Fallo temporal, elegible para reintento.
- **REINTENTO:** Nuevo intento de envío.
- **REVISION_MANUAL:** Incertidumbre tras demasiados timeouts, requiere intervención técnica para no duplicar versiones.

## 11. Matrices Exhaustivas de Transición

### 11.1 Proceso Documental
| Origen | Destino | Condición | Actor | Auditoría | Reversible |
|---|---|---|---|---|---|
| (Inicio) | BORRADOR | Creación exitosa del registro | GESTOR / SISTEMA | DOC_CREADO | N/A |
| BORRADOR | PREPARADO | Confirmación de parámetros | GESTOR | DOC_PREPARADO | Sí (a BORRADOR) |
| PREPARADO | BORRADOR | Necesidad de editar campos | GESTOR | DOC_EDICION | Sí |
| PREPARADO | EN_CURSO | Inicio formal del flujo | GESTOR | DOC_INICIADO | No |
| EN_CURSO | PENDIENTE_FIRMA | Llegada a paso de tipo FIRMA | SISTEMA | DOC_ESPERA_FIRMA | No |
| PENDIENTE_FIRMA | FIRMADO_PARCIAL | Al menos una firma completada, pero faltan | SISTEMA | DOC_FIRMA_PARCIAL | No |
| FIRMADO_PARCIAL | PENDIENTE_PUBLICACION | Todas las firmas incorporadas, PDF final listo | SISTEMA | DOC_PDF_GENERADO | No |
| PENDIENTE_PUBLICACION| COMPLETADO | Confirmación de carga en Alfresco | SISTEMA | DOC_PUBLICADO | No |
| PENDIENTE_PUBLICACION| ERROR_PUBLICACION | Agotados reintentos automáticos | SISTEMA | DOC_PUB_FALLO | Sí (mediante reintento) |
| ERROR_PUBLICACION | PENDIENTE_PUBLICACION | Acción de reintento manual/automático | ADMIN / SISTEMA | DOC_PUB_REINTENTO | No |
| ERROR_PUBLICACION | COMPLETADO | Confirmación exitosa tras reintento/verificación | SISTEMA | DOC_PUBLICADO | No |
| EN_CURSO / PENDIENTE_FIRMA | RECHAZADO | Acción explícita de devolver el documento | PARTICIPANTE | DOC_RECHAZADO | No |
| (Cualquier activo) | CANCELADO | Acción forzosa con motivo | GESTOR / ADMIN | DOC_CANCELADO | No |

### 11.2 Paso de Flujo
| Origen | Destino | Condición | Actor | Auditoría | Reversible |
|---|---|---|---|---|---|
| (Inicio) | PENDIENTE | Se crea el paso | SISTEMA | PAS_CREADO | N/A |
| PENDIENTE | DISPONIBLE | Paso anterior finalizado | SISTEMA | PAS_DISPONIBLE | No |
| DISPONIBLE | EN_PROCESO | Participante inicia la tarea | PARTICIPANTE | PAS_INICIADO | Sí (a DISPONIBLE si abandona) |
| EN_PROCESO | DISPONIBLE | Abandono sin guardar | PARTICIPANTE | PAS_ABANDONADO | No |
| EN_PROCESO | COMPLETADO | Tarea enviada exitosamente | PARTICIPANTE | PAS_COMPLETADO | No |
| EN_PROCESO | RECHAZADO | Participante rechaza con motivo | PARTICIPANTE | PAS_RECHAZADO | No |
| DISPONIBLE | OMITIDO | Decisión explícita de saltar (si regla lo permite) | GESTOR | PAS_OMITIDO | No |
| PENDIENTE / DISPONIBLE| CANCELADO | Proceso fue cancelado globalmente | SISTEMA | PAS_CANCELADO | No |
| DISPONIBLE | VENCIDO | Excedió fecha límite | SISTEMA | PAS_VENCIDO | Sí (reactivación controlada) |
| VENCIDO | DISPONIBLE | Reactivación justificada | GESTOR | PAS_REACTIVADO | No |

### 11.3 Firma
| Origen | Destino | Condición | Actor | Auditoría | Reversible |
|---|---|---|---|---|---|
| (Inicio) | PENDIENTE | Requiere firma | SISTEMA | FIR_REQUERIDA | N/A |
| PENDIENTE | CAPTURADA | Trazo o evidencia recolectada | FIRMANTE | FIR_CAPTURADA | No |
| CAPTURADA | CONFIRMADA | Aceptación de términos | FIRMANTE | FIR_CONFIRMADA | No |
| CONFIRMADA | INCORPORADA | Sellado exitoso en PDF | SISTEMA | FIR_INCORPORADA | No |
| CAPTURADA / CONFIRMADA| INVALIDADA | Error de sellado, revalidación fallida o rechazo | SISTEMA | FIR_INVALIDADA | No |
| PENDIENTE | INVALIDADA | Proceso cancelado antes de firma | SISTEMA | FIR_INVALIDADA | No |

### 11.4 Publicación Alfresco
| Origen | Destino | Condición | Actor | Auditoría | Reversible |
|---|---|---|---|---|---|
| (Inicio) | PENDIENTE | PDF listo | SISTEMA | PUB_ENCOLADA | N/A |
| PENDIENTE | EN_PROCESO | Inicio de transmisión HTTP | SISTEMA | PUB_INICIADA | No |
| EN_PROCESO | PUBLICADA | Código 2xx confirmado | SISTEMA | PUB_EXITOSA | No |
| EN_PROCESO | VERIFICANDO | Timeout o desconexión | SISTEMA | PUB_VERIFICANDO | No |
| VERIFICANDO | PUBLICADA | Hash coincide en Alfresco | SISTEMA | PUB_EXITOSA | No |
| VERIFICANDO | ERROR | Hash no existe en Alfresco | SISTEMA | PUB_FALLO | Sí (a REINTENTO) |
| EN_PROCESO | ERROR | Código 4xx/5xx claro | SISTEMA | PUB_FALLO | Sí (a REINTENTO) |
| ERROR | REINTENTO | Lanzamiento de nuevo intento | SISTEMA | PUB_REINTENTO | No |
| REINTENTO | EN_PROCESO | Nueva transmisión | SISTEMA | PUB_INICIADA | No |
| VERIFICANDO | REVISION_MANUAL| Múltiples fallos de consulta | SISTEMA | PUB_CRITICO | Sí (a PUBLICADA / REINTENTO por humano) |

## 12. Idempotencia de Publicación
Para asegurar que Alfresco no reciba versiones duplicadas frente a reintentos o desconexiones, se define una **clave determinista estricta**:
`Idempotency Key = SHA-256(docid + nodid + verini + hasfir + "PUBLICACION_ALFRESCO")`

**Flujo y Persistencia:**
- Persistencia única en BD asociada al intento de publicación.
- Reutilización en reintentos.
- Comentario de versión generado usando la clave.
- **Consulta previa:** Antes de un reintento, FirmaDoc consulta en Alfresco si existe un documento con ese hash/comentario.
- **Verificación posterior:** (Estado `VERIFICANDO`) Reconciliación tras timeout consultando el hash.
- **Comparación por hash:** Prohibición estricta de repetir escritura HTTP POST sin antes haber verificado el estado real en Alfresco.
- Si el resultado es persistentemente incierto, pasa a `REVISION_MANUAL` para evitar corrupción documental.

## 13. Manejo de Plantillas
- Al preparar un proceso, este **congela** la versión exacta de la plantilla (`tplid` y `tplver`).
- Inactivar una plantilla posteriormente **no detiene** los procesos que ya la estaban utilizando.
- Una plantilla inactiva no puede asignarse a procesos nuevos.
- Los campos de la versión congelada permanecen **inmutables** para ese proceso.

## 14. Consentimientos Informados (Reglas)
- Las casillas de selección y declaración son independientes.
- Una autorización no seleccionada (ej. no autoriza transfusión) **no equivale necesariamente a rechazo total** del proceso. El flujo puede continuar marcando la decisión explícita.
- Decisiones institucionales pendientes de definir reglamentariamente pero modelables genéricamente: Representante legal, menor de edad, testigo obligatorio, persona incapaz de firmar.

## 15. Reglas de flujo (versión 1)
- Admite: flujos secuenciales, uno o varios participantes, uno o varios firmantes, orden explícito, un paso activo por vez, rechazo, cancelación, reintento de publicación.
- No admite: firmas paralelas, aprobación por mayoría, ramas dinámicas complejas, delegación, sustitución automática de firmante, firmas opcionales condicionadas por reglas avanzadas.

## 16. Revalidación documental
Antes de diligenciar, aprobar, firmar, generar PDF final o publicar, se debe validar obligatoriamente:
- nodeId, versión actual, hash actual, tamaño, mime type, estado del proceso.

Si la versión o hash del documento en Alfresco cambian:
- Detener el proceso, invalidar pasos pendientes, impedir nuevas firmas, registrar auditoría, exigir reinicio o nueva evaluación, y no reutilizar firmas anteriores.

## 17. Corrección y sustitución
Un documento firmado **no se edita silenciosamente**. Flujo de sustitución:
1. Identificar el documento firmado.
2. Registrar motivo.
3. Generar nueva versión documental.
4. Iniciar nuevo proceso.
5. Conservar documento anterior.
6. Relacionar proceso anterior y sustituto.
7. Mantener auditoría completa.

## 18. Seguridad y privacidad
- Mínimo privilegio, autorización por rol y proceso.
- Autenticación institucional y cifrado en tránsito.
- Protección de archivos temporales (PDFs cifrados post-falla).
- No almacenar firmas como perfiles reutilizables ni registrar secretos en logs.
- Limitación de datos personales en auditoría, retención y trazabilidad.

## 19. Evidencia jurídica
- Una imagen manuscrita por sí sola no demuestra identidad.
- La fuerza probatoria depende del conjunto integral de evidencias (IP, fecha, usuario autenticado, hashes inmutables, log idempotente).
- Jurídica debe validar los procesos institucionales.
- La versión 1 implementa trazabilidad técnica, no certificación digital, y no debe presentarse como tal.

## 20. Decisiones clínicas e institucionales pendientes
Por definirse formalmente mediante validación institucional pero modelables:
- Tratamiento de representantes, menores, incapaces y testigos en consentimientos.
- Política de anonimización en auditoría.
- Política de vencimiento de flujos y reactivación.
- Revisión jurídica oficial de los consentimientos informados y sus cláusulas de no-rechazo.
