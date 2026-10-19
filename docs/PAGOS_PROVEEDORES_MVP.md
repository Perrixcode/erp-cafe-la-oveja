# Pagos y proveedores · MVP

## Uso cotidiano

En **Compras y proveedores → Facturas**, cada factura muestra proveedor, folio, vencimiento, monto original, monto pagado y saldo pendiente.

- **Pagar** abre directamente el registro del pago o abono. El saldo se propone como monto; puede reducirse para un pago parcial. Se indica la fecha y se confirma que el pago ya fue realizado. El comprobante es opcional y puede agregarse después.
- **Detalle** abre la factura, sus documentos, pagos y el historial de cambios. Desde ahí se editan los datos, se adjuntan archivos y se anulan registros erróneos con motivo.
- **Nueva factura** permite ingreso manual. **Proveedores** permite editar contacto y datos de transferencia.
- Seleccionar facturas permite programar una propuesta y preparar el listado para WhatsApp. La programación no rebaja deuda. WhatsApp abre un mensaje que la persona revisa y envía.
- **Historial de pagos**, dentro de Facturas, reúne los pagos registrados. Los adjuntos se consultan con sesión de socio o socia.

El ERP registra transferencias realizadas; no ejecuta operaciones bancarias. Las facturas pagadas se conservan y se pueden consultar con el filtro correspondiente. Una anulación deja su historial y restituye el saldo cuando corresponde.

## Apertura e independencia de la planilla

La carga inicial revisada del 12 de octubre de 2026 contiene **30 documentos pendientes de 15 proveedores**, por **$6.714.651**. El monto original suma $6.964.651 y conserva $250.000 de abonos anteriores. No se atribuye una fecha inventada a esos abonos.

La planilla es una evidencia de apertura almacenada en la base. No existe sincronización, lectura periódica ni dependencia de Google Sheets. Desde la apertura, todos los ingresos, correcciones y pagos se guardan directamente en el ERP. Repetir exactamente la misma inicialización no duplica datos; otra apertura se bloquea cuando hay facturas operativas.

Las filas sin saldo positivo se conservan en la evidencia original y no se convierten en cuentas por pagar. Los folios o fechas ausentes quedan explícitamente pendientes de completar. Las referencias `SALDO-FILA-…` identifican una fila inicial, no un folio tributario verificado.

## Importación de compras

**Importar compras SII** recibe un archivo CSV descargado por la persona. Presenta una vista previa y permite seleccionar facturas nuevas. Admite facturas afectas y exentas (tipos 33 y 34), detecta duplicados y diferencias, y conserva archivo y valores originales aunque posteriormente se edite la factura.

Un registro de compras no acredita cuánto se ha pagado. Por ello, las facturas importadas requieren **Validar saldo** antes de sumarse a la deuda confirmada o al listado de pagos. Las notas de crédito/débito y otros tipos se muestran para revisión y no se aplican automáticamente. La lectura directa del SII mediante API no está conectada. Revisa las facturas y sus saldos antes de confirmar.

El lector admite los separadores vacíos adicionales al final de las filas del RCV, sin desplazar campos ni descartar columnas adicionales con contenido. Las continuaciones de otros impuestos se asocian únicamente a la factura anterior con la misma identidad, sin número de registro ni monto total y con datos tributarios reconocibles. La vista previa informa las filas asociadas; no se crean facturas ni se vuelve a sumar el impuesto. El CSV completo se conserva como evidencia. Filas incompletas o ambiguas siguen bloqueadas. Se comprobó la lectura de un CSV real de agosto: 230 filas corresponden a 225 documentos y cinco continuaciones tributarias; seis documentos son notas de crédito que requieren revisión.

En **Facturas**, el botón **Exportar a WhatsApp** permanece visible sobre el listado. Se activa al marcar facturas pendientes y muestra la cantidad seleccionada. Genera **un mensaje por proveedor**, con semana, día previsto, total y folios seleccionados, para que la socia pueda reaccionar con ✅ a cada pago. Si hay varias facturas del mismo proveedor, quedan juntas en su mensaje. Incluye una opción para añadir los datos de transferencia y un resumen inicial opcional. Cada proveedor tiene sus propios botones Copiar y Abrir WhatsApp; solo incluye la selección, incluso si cambias los filtros. La semana puede cambiarse sin modificar la programación: las fechas que caen fuera se señalan y no se omiten facturas. No se envía automáticamente ni se interpreta copiar o reaccionar en WhatsApp como un pago en el ERP. Las facturas SII con saldo por validar no pueden incorporarse todavía al listado de pagos.

## Dashboard y exportación

El dashboard funciona dentro del ERP y no necesita Power BI. Incluye saldo pendiente, vencimientos conocidos, programación, compras y pagos por mes, antigüedad de deuda, proveedores con mayor saldo y análisis automático de los datos disponibles. Se actualiza al abrir la sección y periódicamente mientras está activa, sin reemplazar formularios en edición.

Las fechas desconocidas no se inventan: una factura sin fecha de emisión sigue sumando a la deuda, pero no a las compras de un mes específico. Los abonos históricos sin fecha no aparecen como transferencias del mes actual. Las alertas indican los datos que faltan para completar los indicadores. No hay envío automático de alertas a terceros.

Los archivos CSV de facturas y pagos sirven para Excel o Power BI. Incluyen fechas ISO y montos enteros CLP; no exportan datos bancarios. No se requiere configurar Power BI para utilizar el módulo.

## Integridad y respaldo

Pagos, versiones e historial se guardan de forma transaccional. Los reintentos de una misma operación no duplican pagos; los cambios concurrentes exigen actualizar los datos. No se permite pagar sobre el saldo ni reducir una factura por debajo del monto ya pagado.

Los archivos PDF, JPG y PNG, de hasta 8 MB, se guardan en la base con su huella de integridad y acceso restringido a socios. Los respaldos SQLite existentes incluyen pagos, adjuntos, importaciones y evidencia de apertura. El respaldo automático actual permanece en el mismo servidor; esto no equivale a una copia externa.

## Verificación

Suite Python completa: 321 pruebas aprobadas. Casos específicos: 19 pruebas de importación, saldos, pagos, concurrencia, permisos, comprobantes, exportación y recuperación. QA de navegador con información ficticia en escritorio y teléfonos de 390/320 px: ingreso manual, abonos, adjuntos, programación, mensajes preparados, CSV, edición y dashboard.


## Navegación simplificada · 12 de octubre de 2026

El módulo tiene tres pestañas: Facturas, Proveedores y Dashboard. Facturas reúne los saldos, los botones Pagar/Detalle, la selección para WhatsApp y la programación. Las acciones de programación aparecen al seleccionar facturas. Los comprobantes se encuentran en cada detalle o en Historial de pagos; los archivos CSV anteriores, dentro de Importar compras SII. Ese formulario contiene la guía para descargar el detalle desde el Registro de Compras y Ventas del SII.

El menú lateral agrupa los destinos existentes en Ventas y entregas, Inventario y producción, Finanzas y reportes, y Equipo y documentos. Compras y proveedores conserva acceso directo. Productos y formatos ya no duplica la ficha de proveedores. Los enlaces anteriores a Pagos, Comprobantes, Programación semanal y Catálogo → Proveedores siguen llevando al destino correspondiente. La reorganización no cambia saldos ni registros.


## Selección SII y prevención de duplicados · 12 de octubre de 2026

La vista previa comienza sin facturas seleccionadas. Permite marcar individualmente, buscar por proveedor/RUT/folio, filtrar por revisión, seleccionar las nuevas de la página y limpiar la selección. Las selecciones se conservan entre filtros y páginas de 50 filas. El resumen muestra la cantidad y el monto original de las facturas elegidas; no lo presenta como deuda confirmada. Solo esas filas se incorporan. Se puede indicar Crédito, Contado/contra entrega o Sin definir; ninguna condición genera un pago supuesto. Los pagos anteriores se validan después.

La protección se comparte con el ingreso manual. Compara RUT, tipo y folio normalizados contra facturas operativas (pendientes, pagadas o anuladas), los archivos SII incorporados y las identidades conservadas en el historial de cambios. También consulta la evidencia de apertura almacenada, incluidas las filas ya pagadas que no se incorporaron como deuda operativa. No consulta Google Sheets.

Cuando faltan RUT o folios, una coincidencia de nombre/folio o folio/monto, y ciertos saldos iniciales sin folio con el mismo monto, requieren revisión. La vista identifica el documento y la fuente coincidentes y bloquea inicialmente su selección. **Revisar / forzar** muestra la comparación, el estado y los pagos disponibles. Exige motivo y confirmación individual; Cancelar no autoriza ni importa nada. En una coincidencia dudosa permite incorporar una factura distinta, con saldo por validar. En una identidad exacta vincula el archivo a la factura existente y conserva monto, pagos, saldo, estado y correcciones: no crea otra deuda. No fusiona proveedores automáticamente. Una fila repetida dentro del CSV puede revisarse y elegirse una sola vez; errores de datos y tipos no admitidos no tienen excepción.

Confirmar la revisión selecciona la fila; la operación se guarda al confirmar el lote completo. Puedes quitar una excepción antes de importar. El historial conserva usuario, fecha, motivo, coincidencias observadas y documento de origen. Si cambia la coincidencia o sus pagos entre la revisión y la confirmación final, el lote se rechaza y debe usarse **Revisar archivo de nuevo**. Los reintentos conservan el resultado anterior sin repetir facturas ni vínculos. Completar correctamente los RUT ayuda a distinguir facturas legítimas de proveedores diferentes. No se puede garantizar identificar dos documentos si sus antecedentes están incompletos o son erróneos y no hay una coincidencia verificable.

Al confirmar, el servidor repite la revisión de toda la selección dentro de una transacción con bloqueo de escritura. Una vista desactualizada no sobreescribe pagos ni crea duplicados. Repetir el mismo archivo/fila ya incorporado es idempotente; importaciones concurrentes desde archivos diferentes también se controlan. Si una fila seleccionada requiere revisión, no se aplica parcialmente ese lote.

Verificación específica: 29 pruebas de pagos, apertura e importación, incluidos diez casos de selección y duplicados (pagadas, historial de edición, apertura, RUT faltante, dos archivos simultáneos, reintentos y selección desactualizada). QA de navegador con 55 filas ficticias, selección de tres, filtros/paginación y formatos de 1360, 390 y 320 px.

Ampliación de excepciones: 355 pruebas completas aprobadas, incluidas nueve nuevas de autorización, auditoría, concurrencia, documentos pagados/anulados, cancelación y datos no admitidos. QA del flujo Confirmar/Cancelar en 1360/390/320 px con registros ficticios.

## Alta de proveedores desde facturas · 12 de octubre de 2026

En Nueva factura, la opción **+ Nuevo proveedor** permite indicar nombre y RUT opcional sin salir del formulario. Al guardar se crean la ficha y la factura en una sola transacción: si la factura falla, no queda un proveedor suelto. Un RUT existente reutiliza su ficha y conserva nombre, contacto y datos bancarios. Un nombre coincidente sin RUT suficiente requiere seleccionar la ficha existente o completar sus datos, evitando fusionar empresas por nombre. Los reintentos no duplican registros.

Las importaciones SII normales y forzadas crean automáticamente los proveedores nuevos usando nombre y RUT del CSV al confirmar el lote completo. Varias facturas del mismo RUT usan una sola ficha; revisar o cancelar no crea proveedores. Los vínculos exactos conservan el proveedor de la factura existente. Los datos de contacto y transferencia pueden completarse después en Proveedores.

La preparación de mensajes se denomina **Listado de pagos**.

## Revisión persistente de archivos SII · 12 de octubre de 2026

**Archivos SII** abre una bandeja de archivos guardados, con revisiones abiertas y un historial de archivos cerrados/reemplazados. Subir un CSV no incorpora facturas automáticamente. El mismo contenido reutiliza el archivo previo sin reiniciar su avance; no hay límite de 20 archivos visibles.

La revisión guarda en el servidor la selección, condiciones, excepciones confirmadas, descartes con motivo, notas y gestiones, búsqueda, proveedor, filtro y página. El guardado automático se agrupa mientras se escribe y muestra su estado. Guardar y salir espera a que termine. Una falla conserva los cambios locales mientras siga abierto el formulario y ofrece reintentar; recargar tras un conflicto advierte que descartará lo no guardado. La pestaña advierte si se intenta salir con cambios pendientes. No hay sincronización sin conexión: una recarga forzada antes de guardar puede perder cambios locales.

Los filtros permiten trabajar por proveedor, buscar nombre/RUT/folio y ver pendientes, por resolver, seleccionadas, incorporadas/registradas, descartadas o todas. Seleccionar disponibles afecta a todo el filtro, incluidas otras páginas, sin autorizar duplicados. Las coincidencias siguen requiriendo revisión individual, motivo y confirmación. Las notas admiten Consultar al proveedor, Esperando nota de crédito y Otra gestión; no cambian pagos ni saldo. Las acciones en grupo permiten descartar pendientes del filtro o de la selección con motivos Pagada anteriormente, Compra al contado, No corresponde, Documento duplicado u Otro (con nota obligatoria). Descartar es reversible y no registra pagos ni elimina facturas.

Incorporar una selección procesa solo esas filas y deja el archivo abierto para continuar. Las fichas de proveedores se crean/reutilizan automáticamente. Los saldos importados siguen sujetos a validación. La selección restante, notas y descartes permanecen; los reintentos no repiten documentos. Los cuatro contadores separan Pendientes, Por resolver, En el ERP y Descartadas, con montos originales y aviso cuando hay datos inválidos. No representan deuda confirmada. La revisión y el estado de pago son independientes.

**Reemplazar archivo** exige comparar primero y confirmar después. La comparación muestra nuevas, cambiadas, ausentes, iguales y ambiguas, paginadas de 50 en 50. Se vuelven a comprobar archivo, nombre y versión al confirmar. Se conservan descartes, notas y condiciones solo cuando la identidad RUT/tipo/folio, monto y fecha coinciden sin ambigüedad. Se limpian selecciones y excepciones para revisar nuevamente. Las filas cambiadas, nuevas o ambiguas quedan pendientes; las ausentes no borran registros. El archivo anterior queda inmutable para consulta; sus facturas, pagos y evidencias permanecen. Se impide reemplazar con otro archivo ya existente en la bandeja, evitando ciclos; se puede abrir ese archivo directamente. Volver a subir el mismo contenido conserva el avance.

**Cerrar revisión** exige resolver todos los documentos y gestiones pendientes. El cierre no paga ni modifica facturas y puede revertirse con Reabrir revisión. Se guarda actor/fecha. Revisiones cerradas y versiones reemplazadas no aceptan cambios; una versión reemplazada dirige a la siguiente. Dos sesiones no pueden sobrescribir avances entre sí: guardado, confirmación, cierre y reemplazo usan versiones y transacciones. Las excepciones se invalidan si cambian sus coincidencias o pagos. Se auditan diferencias de selección, motivos, notas, condiciones, cierre/reapertura y reemplazos. La nueva tabla `ap_import_reviews` es aditiva; los archivos anteriores sin metadatos se pueden abrir y continuar.


### Eliminar y recuperar un archivo SII

En Archivos SII, **Eliminar archivo** aparece junto a **Retomar revisión** (o Ver revisión para archivos cerrados/anteriores). La confirmación muestra el nombre y las facturas incorporadas; exige marcar la casilla y permite Cancelar. El archivo sale de la lista y queda en **Papelera**, desde donde **Restaurar archivo** recupera su revisión y avance.

Es una eliminación recuperable: se mantienen el CSV original, notas, selecciones, descartes y la evidencia para evitar duplicados. No elimina ni modifica facturas, proveedores, pagos o comprobantes. Las sesiones con el archivo abierto dejan de poder guardar, importar o reemplazarlo mientras esté en papelera. Volver a subir el mismo CSV pide restaurarlo, sin crear otra revisión. El control de versión evita eliminar un avance modificado por otra sesión, y eliminar/restaurar queda auditado.
