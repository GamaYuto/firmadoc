# Lista de revision arquitectonica

## Inventario

- Confirmar rama, commit y estado de Git.
- Listar modelos, esquemas, rutas, servicios, CRUD/repositorios, migraciones y pruebas.
- Comparar la estructura encontrada con la entrega reportada.

## Capas

- Rutas: validacion HTTP y traduccion de errores, sin logica de negocio extensa.
- Servicios: reglas de negocio y coordinacion transaccional.
- CRUD/repositorios: persistencia, sin decisiones de flujo.
- Modelos: restricciones coherentes con la base.
- Esquemas: entradas y salidas separadas; campos derivados no aceptados desde cliente.

## Alcance

- Detectar dependencias o funciones agregadas sin solicitud.
- Detectar refactorizaciones laterales.
- Confirmar que no se alteraron tecnologias aprobadas.
- Confirmar que README no describa funciones inexistentes.

## Verificacion

- Cada afirmacion del reporte debe enlazarse con archivo, prueba o salida concreta.
- Un resumen del agente no constituye evidencia.
