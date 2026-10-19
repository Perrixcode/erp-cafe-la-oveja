# Proveedores · etapa C: propuesta semanal revisable

Fecha: 14 de octubre de 2026. Cambio preparado y validado en copia aislada
sobre la etapa B, para la rama
`feat/delivery-fees`, HEAD `8f5f0c6add611ebe7ea57c27e46705c5371d3684`.
Este documento describe el código local y la validación con datos ficticios.
No acredita un despliegue ni una migración de bases operativas.

## Resultado y uso

En **Compras y proveedores → Facturas → Propuesta semanal**, un socio elige
semana, presupuesto total en CLP y si permite pagos parciales. El sistema
calcula una propuesta por vencimiento y permite ajustar importes, dejar un
importe en cero para excluirlo, consultar cada factura y escoger una fecha.
Una confirmación explícita guarda únicamente la programación. No registra
un pago, modifica deuda, genera movimiento de stock ni ejecuta transferencias.

El recibo conserva actor de sesión, fecha, presupuesto, pagos y reservas al
confirmar, selección, importes, motivos de exclusión y nota de revisión. La
consulta muestra las últimas 20 propuestas y permite abrir su revisión
original; cada factura enlazada muestra su saldo y programación actuales.
La revisión guardada no se altera cuando luego cambian las facturas.

La propuesta se genera al solicitarla. No hay tarea periódica en segundo
plano, envío de mensajes ni integración bancaria.

## Presupuesto, período y pagos parciales

La semana se normaliza a **lunes–domingo, ambos incluidos**. Solo se proponen
la semana actual o posteriores, y la fecha de programación debe estar dentro
de esa semana y no ser anterior al día de negocio actual. Se conserva el día
de negocio de `payables.local_day()`: `America/Santiago`.

`Disponible = máximo(0, presupuesto − pagos registrados de la semana − reservas pendientes de la semana)`.

- Pagos: suma los registros vigentes de `ap_payments.paid_on` y las entradas
  de cuenta corriente de tipo `payment`, por `occurred_on`. Una entrada de
  cuenta se cuenta una sola vez, incluso si aplica a varias facturas. Incluye
  anticipos y todos los medios de pago. Las aplicaciones no vuelven a sumarse.
- Reservas: suma `planned_amount` vigente cuando `scheduled_on` está dentro
  de la semana. Es el importe que aún queda programado; registrar/aplicar un
  pago ya reduce ese importe mediante el comportamiento existente de `touch`.
- Ejemplo verificado: $4.000 programados, luego $1.000 pagados esa semana,
  dejan $3.000 reservados. Con presupuesto $5.000 queda $1.000 disponible.
  No se descuentan a la vez el pago y la reserva original completa.
- Las notas de crédito reducen deuda y programación aplicable, pero no son
  salida de dinero y nunca se suman como pagos de la semana.
- Pagos históricos sin fecha no se asignan artificialmente a una semana.
  Reducen deuda, pero quedan fuera del consumo semanal por fecha.
- Una programación parcial existente se conserva y excluye esa factura de
  la propuesta nueva, también si está fuera de la semana. No se completa ni
  reemplaza automáticamente. Su ajuste se realiza en la programación manual.
- Mover una reserva entre semanas cambia el presupuesto del período de la
  reserva, sin mover la fecha del dinero ya pagado. Cancelar una programación
  libera su reserva y permite volver a proponer la factura.
- Anular un pago lo excluye del consumo por su fecha original. Anular una
  aplicación no anula el pago: sigue consumiendo presupuesto y queda crédito
  disponible para revisar. Ni anular pagos/aplicaciones/NC ni resolver una
  pausa restaura una programación anterior. Hay que revisar y programar de
  nuevo el saldo que se quiera comprometer.
- Los pagos no tienen edición de fecha directa. Una corrección se registra
  anulando y recreando con motivo y fecha correcta. Se recalculan presupuesto
  y reserva restante; la programación anterior tampoco se restaura sola.

Se conserva una instantánea al confirmar, no un cierre de caja histórico.
Una anulación posterior modifica el cálculo actual, mientras el recibo previo
mantiene lo que se revisó entonces.

## Elegibilidad y prioridad

Se omiten facturas anuladas o saldadas. Las restantes deben tener saldo
validado, emisión, vencimiento, folio verificable, condición de pago conocida,
RUT del proveedor, ficha de transferencia completa y original PDF/foto
adjunto. La presencia de un adjunto no certifica su contenido ni autenticidad.
Se exigen banco, tipo y número de cuenta, titular y RUT del titular; no se
verifica titularidad bancaria ni se modifica la ficha desde esta propuesta.

Se explican las exclusiones por falta de antecedentes, vencimiento posterior
al domingo, pausa/disputa o créditos disponibles. Si el proveedor tiene
anticipos o NC sin aplicar, primero debe revisar y aplicar esos créditos.
La importación CSV del SII sigue disponible y no reemplaza el original ni la
validación del saldo. No se introdujo OCR ni una nueva importación.

Prioridad estable: vencimiento, emisión, ID de proveedor, ID de factura.
Incluye deuda vencida antes de la semana. Con pagos parciales se usa el saldo
disponible; sin ellos se omite una factura que no cabe y se puede proponer una
posterior más pequeña. Máximo 100 facturas por confirmación.

## Pausa y disputa

El detalle de factura permite **Pausar pago / disputa** con motivo. Se registra
actor y fecha, se borra la programación actual y se impide incluirla en nuevas
propuestas, programación manual y listados de pagos. La factura permanece en
la deuda. Resolver la pausa exige otro motivo y conserva ambas actuaciones.

La lista añade estado **Pagos en pausa**, etiqueta en cada factura y casillas
deshabilitadas. El dashboard informa las pausas y la exportación de facturas
añade `pago_en_pausa` y `motivo_pausa` al final de las columnas existentes.
Una pausa limita coordinación; no impide registrar un pago que ya ocurrió.
No es una autorización bancaria ni un circuito de doble aprobación.

## Integridad, permisos y trazabilidad

`payment_proposals.preview/build` calcula sobre una transacción de lectura.
`mutate` confirma dentro de `payables.execute`, con `BEGIN IMMEDIATE`, actor
de sesión, versiones e idempotencia por solicitud. El token determinista se
recalcula al confirmar y rechaza revisiones obsoletas cuando cambian importes,
documentos elegibles, fichas, créditos, pausas o programaciones. Un token no
es una firma de aprobación: el usuario debe tener sesión de socio.

Se rechazan importes no enteros, booleanos, negativos, selecciones repetidas,
facturas no elegibles, más de 100 facturas, importes superiores al saldo y la
suma de pagos + reservas + nueva propuesta superior al presupuesto.
La operación es atómica; una segunda confirmación concurrente queda obsoleta.
Reintentar la misma solicitud devuelve el resultado guardado sin duplicarla.

Se agregan tablas `ap_invoice_holds` y `ap_payment_proposals`, con índice único
de pausa activa por factura. No se alteran tablas históricas ni se convierten
datos de etapas previas. La creación repetida del esquema fue comprobada en
bases sintéticas. No se ejecutó sobre una base operativa.

Rutas, bajo los permisos y controles de origen existentes de proveedores:

- `POST /api/suppliers/payment-proposal`: vista previa de lectura.
- `POST /api/suppliers/payment-proposals`: confirmar programación revisada.
- `GET /api/suppliers/payment-proposals`: últimas 20 revisiones.
- `GET /api/suppliers/payment-proposals/{id}`: revisión original conservada.
- `POST /api/suppliers/invoices/{id}/payment-hold`: pausar o resolver.

## Verificación y evidencia

Checkpoint: `proveedores-etapaC-20261014T155122Z`, en el directorio local de la
tarea Codex. Contiene copias `before/` y `work/`, manifiestos SHA-256, parche
y capturas `qa/`. `application-check.json` solo se genera tras una aplicación
confirmada; su ausencia no acredita aplicación al repositorio. Se valida que todos los archivos del
checkpoint sigan iguales antes de aplicar; se conservan los cambios previos
del usuario y el estado sin commit de las etapas anteriores.

- Suite completa: **162 pruebas aprobadas** en `tests-final.log`.
- Comprobación adicional solicitada de períodos, anulaciones y cambios de
  fecha: **27 pruebas unitarias de C aprobadas** en
  `tests-period-boundaries.log`, incluyendo seis nuevas. Total de pruebas
  distintas validadas: **168** (138 anteriores + 30 de C). No se repitió la
  suite completa tras añadir solo estos casos de prueba.
- Los tres casos HTTP de C comprueban 401 sin sesión, 403 para cajero/origen
  indebido, actor tomado de sesión, rutas de lectura y escritura, reintentos,
  pausa/resolución y conservación del saldo al programar.
- JavaScript de propuesta y proveedores: comprobación de sintaxis aprobada.
- QA visual en escritorio y móvil 390×844, con proveedor, cuenta y documentos
  ficticios. Presupuesto 5.000, pago previo 1.000, reserva restante 3.000:
  propuesta 1.000; 1.001 bloqueado. Guardar conserva pago $0 y saldo $6.000 en
  la factura propuesta, y agrega programación $1.000. Sin errores de consola.
- Capturas finales: `qa/proposal-budget-final-desktop.jpg`,
  `qa/proposal-budget-final-mobile.jpg`, `qa/proposal-saved-final-desktop.jpg`,
  `qa/proposal-saved-final-mobile.jpg`, `qa/proposal-invoice-final-desktop.jpg`.

Las pruebas usaron copias y SQLite temporal. Un audit hook bloqueó acceso a
SQLite fuera del área temporal y conexiones distintas de loopback. El
servidor visual temporal fue cerrado. No se consultaron datos operativos,
secretos, servicios externos, Toteat ni documentos de proveedores reales.

## Límites que quedan explícitos

El presupuesto es un parámetro revisado por propuesta; no existe un techo
semanal central autorizado ni un bloqueo global de otros flujos. Una
programación manual o un pago registrado puede excederlo; la siguiente
propuesta lo muestra y no agrega más importe sin ampliar el presupuesto.
No hay conciliación bancaria: pago registrado no equivale a pago conciliado.

Se mantienen las brechas documentadas en B: duplicados financieros reales
con distintas solicitudes no se detectan automáticamente; no hay cierres
históricos reconstruibles antes de ediciones; una NC anulada conserva su
folio único; no se ha certificado volumen operativo/paginación de todos los
listados. Las revisiones de C guardan instantáneas completas y pueden crecer
con el número de facturas; falta medir su volumen y definir retención.

No se implementaron pedido–recepción–factura, bodega/Kardex, compras sugeridas,
comparación de precios por unidad, directorio extendido ni importación de la
planilla de proveedores. Esas áreas pertenecen a etapas posteriores. D no se
inició en este cierre.
