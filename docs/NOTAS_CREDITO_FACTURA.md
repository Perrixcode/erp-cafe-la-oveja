# Notas de crédito recibidas por factura

En la lista y el detalle de una factura vigente con saldo validado, un socio puede usar **Agregar nota de crédito**. El proveedor y la factura quedan vinculados al documento elegido. Se ingresan folio, fecha, monto y motivo; se puede adjuntar el documento PDF, JPG o PNG de hasta 8 MB. Sin archivo, la ficha indica que el respaldo está pendiente y permite adjuntarlo después desde la cuenta corriente.

La interfaz muestra el importe original, pagos, créditos aplicados, saldo actual y una vista previa de cuánto se aplicará y cuánto quedará disponible. La nota reduce el saldo pendiente hasta agotarlo. Si la factura ya está pagada, toda la nota queda como saldo a favor del proveedor. Si existe un sobrepago anterior, se conserva como otro crédito: no se duplica ni se reescribe. Las notas no generan un pago, no modifican stock y no se emiten al SII.

El libro de cuenta corriente ya existente conserva nota, aplicaciones, adjunto y auditoría. Este flujo los guarda en una única transacción. Un adjunto inválido revierte toda la operación. El reintento conserva la solicitud original cuando la respuesta es incierta; no modifica la aplicación ni crea otra nota. Una respuesta de validación definitiva permite corregir el formulario.

Se mantienen los límites existentes: suma de notas vigentes de origen no superior al importe original, fechas no futuras ni anteriores a la factura, proveedor coincidente y versión vigente. El folio numérico se normaliza y se deduplica por proveedor y tipo. No se inventan reglas para exceder el original ni reasignar créditos ya utilizados. Las aplicaciones posteriores y anulaciones siguen el flujo existente de cuenta corriente.

El detalle de factura presenta notas vigentes y anuladas, monto original, crédito aplicado, saldo, disponible y documentos. La cartola CSV/PDF conserva el folio y la factura de origen, y abona el crédito una sola vez; las aplicaciones distribuyen el crédito sin otro abono. La identidad del usuario procede de la sesión autorizada.

No requiere migración de esquema. Las pruebas usan bases temporales: notas parciales y totales, factura pagada, sobrepago previo, aplicaciones limitadas, concurrencia/idempotencia, documentos inválidos, límites, permisos y contratos de interfaz. El despliegue exige restaurar y verificar un respaldo, comprobar la release activa y conservar una reversión solo de código con la base actual intacta.
