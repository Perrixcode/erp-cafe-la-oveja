# Proveedores: interfaz y estados de carga

14 de octubre de 2026. Etapa local posterior al cierre funcional A–F.
Base: rama `feat/delivery-fees`, HEAD
`8f5f0c6add611ebe7ea57c27e46705c5371d3684`.
Describe código local y QA ficticio; no acredita despliegue.

## Referencias y criterio visual

Se revisó documentación pública oficial de Defontana y la captura del listado
de proveedores enlazada desde su ayuda. No se ingresó a una cuenta del servicio.

- [Crear un proveedor](https://intercom.help/defontanaerp/es/articles/13680108-como-crear-un-nuevo-proveedor): listado, filtros, acciones y ficha organizada.
- [Nuevo módulo de compras](https://intercom.help/defontanaerp/es/articles/14659534-nuevo-modulo-de-compras): documentos ingresados/pendientes, filtros y estados.
- [Panel de control](https://intercom.help/defontanaerp/es/articles/13177506-panel-de-control): resumen y acceso a información de gestión.

Patrones observados: fondo gris claro, superficies blancas, navegación lateral
estable, encabezados sobrios, bordes finos, filas compactas, filtros arriba y
acciones identificables. Se trasladó esa estructura a Oveja conservando su
nombre y verde, con iconos SVG propios y sin incorporar activos de Defontana.
No se verificó visualmente su animación de carga: el indicador implementado es
propio y no se presenta como una copia exacta.

## Cambios de interfaz

- `static/workspace.css`: tema compartido y estilos de Proveedores, tabla
  compacta, resumen de cuatro indicadores y adaptación móvil. La navegación
  lateral y superior conservan las rutas y permisos existentes.
- `static/app.js`, `static/index.html`, `app.py`: carga de los dos nuevos assets,
  título único por sección e iconos de navegación. Al iniciar sesión se espera
  el tablero antes de mostrar el espacio de trabajo inicial.
- `static/supply-planning.js`: panel de pendientes en filas, próximas entregas,
  conciliación, comparación de precios, existencias y sugerencias en tablas.
- `static/procurement.js`: pedidos en una tabla con pendientes, aceptados,
  rechazos e incidencias; conserva formularios y operaciones existentes.
- `static/supplier-directory.js`: listado buscable y ficha integrada con
  Resumen, Contacto y pago, Compras y precios y Facturas. Mantiene acceso a
  cuenta corriente y edición de la ficha existente.
- `static/payables.js`: lista y dashboard con el estilo común; las
  actualizaciones del dashboard conservan los indicadores hasta recibir datos
  y descartan respuestas anteriores a la consulta vigente.

La etapa no modifica modelos, cálculos de saldos, cantidades, precios,
reposiciones, controles de permisos ni esquema. El único cambio Python de
aplicación agrega las rutas de los nuevos archivos estáticos.

## Carga, errores y navegación

`static/loading-state.js` expone `OvejaLoading.begin()` y `cancelAll()`.
`begin()` devuelve `done`, `fail` y `cancel`; cada región mantiene su propio
estado y una respuesta antigua no elimina el indicador de la siguiente.

- Espera 180 ms antes de aparecer para no parpadear en consultas rápidas.
  Una vez visible permanece al menos 220 ms, salvo cancelación de la vista.
- Carga inicial: indicador al centro del área de trabajo, con texto de la
  sección. La navegación sigue disponible.
- Actualización: indicador pequeño, conservando los datos consultables.
- Error de lectura: mensaje y botón Reintentar; se conserva el contenido
  anterior cuando existe. No se reintentan escrituras automáticamente.
- Cambio de sección o cierre de sesión: cancela los indicadores anteriores.
- Texto accesible, `role=status`, `aria-live`, `aria-busy` y errores `role=alert`;
  `prefers-reduced-motion` elimina el giro. No se hizo una auditoría con lector
  de pantalla ni una certificación de accesibilidad.

Se integra en acceso, tablero inicial, Inicio, Proveedores, asignador y las
vistas principales de reparto y transferencias; también en el guardado de los
formularios de abastecimiento y revisión documental. Esto no inicia ninguna
integración externa. Las consultas GET del helper `api()` tienen un plazo de
30 segundos; las escrituras conservan sus controles y la lectura documental
conserva su plazo existente.

## Alcance A–F conservado

| Área | Disponible localmente | Límite vigente |
| --- | --- | --- |
| A | Foto/PDF con extracción local, revisión y original; CSV SII conservado. | Cargar no crea deuda; OCR no interpreta líneas ni acredita autenticidad. |
| B | Cuenta corriente, pagos parciales, anticipos, NC monetarias y reversas. | Registro administrativo; no transferencias ni conciliación bancaria. |
| C | Propuesta semanal revisable, presupuesto, prioridades y reservas. | Manual; no programación periódica, techo central de caja ni ejecución. |
| D | Pedido, recepción parcial, rechazo, discrepancias, factura vinculada y stock propio. | No envía compras ni deduce ventas, recetas o Toteat. Requiere conteo físico verificado. |
| E | Conciliación y precios históricos por unidad normalizada. | Equivalencias explícitas; neto/bruto separados; sin repartir fletes, impuestos o descuentos automáticamente. |
| F | Reposición explicada según stock, pendientes, consumo y política revisados. | Bloquea antecedentes insuficientes; preparar pedido requiere revisión y confirmación. |
| Panel/directorio | Alertas enlazadas y ficha integrada de contacto, pago y abastecimiento. | No valida cuentas bancarias ni activa avisos automáticos. |

La planilla de **35 proveedores sigue pendiente de cotejo e incorporación**.
No se importó durante esta etapa. Los detalles funcionales, integridad,
permisos e idempotencia están en `PROVEEDORES_PLAN_LOCAL.md`.

## Evidencia y aplicación

Checkpoint de esta tarea: `proveedores-interfaz-20261014T175921Z`.
Incluye `before/`, `work/`, manifiestos SHA-256, parche y comprobador. Antes de
aplicar, se exige el mismo HEAD/rama y coincidencia de los 102 archivos
capturados. `application-check.json` es la evidencia de aplicación y de que
los demás archivos capturados permanecieron sin cambios. No crea commit.

- **239 pruebas Python aprobadas** en 24,876 s (`tests-final.log`): permisos,
  origen, actor, duplicados, idempotencia, versiones, saldos, recepción,
  existencias, precios, reposición, documentos y rutas de los nuevos assets.
- **8 pruebas de carga aprobadas** (`tests-loading.log`): consulta rápida,
  duración mínima, sustitución, cancelación, segundo plano, error/reintento,
  regiones independientes y respuestas antiguas.
- Sintaxis de los 13 JavaScript cambiados/nuevos y los dos Python cambiados
  comprobada en `syntax-final.json`.
- QA en escritorio 1365×900 y móvil 390×844, con cuatro proveedores y cuatro
  facturas ficticias. El servidor bloquea SQLite fuera del temporal y toda
  red distinta de loopback, y muestra el aviso de entorno ficticio.
- Se verificó guardar una nota ficticia y conservar días y saldo; abrir cuenta
  corriente; filtrar dashboard; tablas de pedidos, stock y precios; sugerencia
  bloqueada por entrega atrasada; navegación durante una respuesta tardía;
  carga lenta central; actualización sin borrar datos; error 503 provocado y
  reintento exitoso. No se hicieron pagos ni compras reales.
- Capturas y observaciones: `qa/final-check.json` y archivos `qa/*.jpg`.
  La comprobación final de consola no registró errores; la prueba de recuperación
  provoca un HTTP 503 controlado en el servidor ficticio.

No se desplegó, no se inició la aplicación con su base operativa y no se
aplicaron migraciones operativas. La activación, datos reales, volumen,
paginación y revisión de OCR en el servidor de destino conservan los pendientes
documentados en el cierre funcional. Kardex Toteat sigue fuera del alcance.
