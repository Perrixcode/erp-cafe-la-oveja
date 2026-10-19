# Conciliación, analítica y liquidación provisional de repartos

Las tres secciones están en **Despacho y reparto** y requieren una cuenta de socio. Seleccionar un mes con registro auditado.

- **Conciliación de repartos:** cuadro de Toteat, chat y planillas por día, semana o mes; comparación por repartidor, originales privados PDF/XLSX, filas trazables a mensajes y líneas, y casos pendientes.
- **Analítica de repartos:** cantidades e importes por día, semana, mes y repartidor; promedio por ficha base; variaciones mensuales de promedio, importe y cantidad cuando existe mes anterior auditado; días de mayor volumen e importe; pendientes, posibles duplicados/reasignaciones e intentos por revisar con denominadores visibles.
- **Liquidación de repartidores:** base documental, retenidos conocidos, importes desconocidos, pendientes y PDF privado con vista previa y descarga. Marca PROVISIONAL en cada página y emisión en Chile.

## Alcance de las comparaciones

Toteat usa el producto exacto `TOTEATDVYCOST`, `products[].payed` y la fecha operativa del turno. El chat usa la fecha de envío del export, sin zona declarada. Los conteos de fichas y registros de tarifa son unidades diferentes. No hay vínculo confirmado por pedido comercial.

Toteat no atribuye repartidores: las vistas individuales dicen **No atribuible**. El agregado de planillas es parcial y puede incluir reclamaciones superpuestas. Su diferencia se calcula contra la base del chat de los repartidores cubiertos; nunca contra el total global de Toteat. Cada planilla se compara además con su respectiva base individual. No se atribuyen importes disputados por conservar una primera mención.

Un día sin evidencia se muestra como **Sin dato**. El mes y la semana de Toteat solo muestran total comparable cuando se conoce cada día. Los anticipos declarados no se descuentan automáticamente. Los mensajes contextuales sin ficha quedan fuera de importes y denominadores. Las diferencias no acreditan ejecución, fraude, sobrepago ni derecho al cobro.

## Importación y conservación

`scripts/import_delivery_audit.py` consume un paquete privado auditado, verifica período, manifest, tamaños, hashes, originales, totales y referencias contra el lote de chat existente. La transacción es atómica e idempotente. Otro paquete para el mismo período requiere revisión. Las tablas aditivas `delivery_audit_batches` y `delivery_audit_documents` conservan procedencia y documentos originales. No modifica la base de chat, pedidos, asignaciones, stock ni pagos.

El repositorio solo incluye fixtures y capturas ficticias. No incorporar paquetes reales, bases, nombres de clientes, mensajes ni credenciales. La generación PDF usa ReportLab y Noto Sans local; no depende de fuentes externas.

## Verificación

Pruebas de integridad, rechazos atómicos, idempotencia, ámbitos y agregaciones, ausencia frente a cero, permisos HTTP 401/403, PDF multipágina y originales privados. QA de escritorio y móvil con filtros, evidencia escapada, vista previa PDF, períodos vacíos y limpieza al cerrar sesión. Los datos reales se verifican en una base privada aislada antes de importar en producción.

## Resumen exportable y conceptos por repartidor

Analítica y Liquidación incluyen **Resumen por repartidor y conceptos**, con PDF resumido (vista previa y descarga) y CSV UTF-8 para Excel. Se respeta el mes y el repartidor seleccionado. El CSV contiene una fila por repartidor, las seis columnas resumidas e importes numéricos; no agrega una fila total que pueda duplicarse al sumar en Excel. Ausencias documentales conservan «Sin dato» y el total queda «Pendiente».

Un socio puede registrar un **Adicional** o **Descuento** con concepto e importe entero positivo en CLP para un repartidor identificado en la base del mes. El cálculo es **monto bruto de la planilla + adicionales − descuentos = total a pagar provisional**. No se descuentan automáticamente anticipos declarados en planillas ni se atribuyen importes pendientes del chat. El resultado no ejecuta ni acredita pagos; saldos negativos requieren revisión.

Cada concepto conserva responsable autenticado, fecha chilena y solicitud idempotente. Para corregirlo se anula con motivo y se registra el reemplazo: se conservan el original y quién/cuándo lo anuló. La versión evita anulaciones sobre cambios concurrentes. Los conceptos anulados permanecen visibles en el historial y dejan de afectar totales/exportaciones. Las tablas de chat, Toteat y planillas permanecen intactas.

La liquidación detallada, el resumen PDF y el CSV usan el mismo cálculo de conceptos. El resumen evita listar las fichas individuales; su detalle sigue disponible en el ERP. Todas las rutas de consulta, descarga, registro y anulación son exclusivas de socios.

## Monto principal según planilla

Por instrucción del usuario, el resumen y la liquidación calculan el total a pagar sobre el monto bruto declarado en la planilla de cada repartidor, más adicionales y menos descuentos manuales vigentes. La base del chat se conserva como referencia para conciliar; no sustituye una planilla faltante. Los anticipos declarados no se descuentan automáticamente.

Pantalla, PDF y CSV resumen presentan: Repartidor, Base chat (referencia), Monto declarado en planilla, Adicionales, Descuentos y Total a pagar. Sin planilla, el total individual queda pendiente; el agregado se etiqueta como subtotal con planilla, excluyendo también los conceptos de las filas sin planilla. Un importe de planilla de cero es un valor válido; los saldos negativos se conservan. Las evidencias originales y los conceptos no se modifican.

## Selección de varios repartidores

El filtro Repartidores permite marcar uno o varios nombres y aplicar esa combinación; mantiene Todos y Limpiar. Resumen, conceptos, totales, métricas, conciliación y liquidación respetan la selección. Los PDF y CSV incluyen únicamente los elegidos. Las APIs de resumen y liquidación admiten parámetros `driver` repetidos, conservan la consulta individual anterior, deduplican nombres y rechazan selección vacía o mezclar Todos con nombres. No se distribuye Toteat global entre los repartidores seleccionados.
