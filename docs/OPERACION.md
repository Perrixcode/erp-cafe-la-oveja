# Contexto de operación proporcionado

Este registro explica el negocio que orienta el prototipo; no ejecuta estas operaciones.

- Todos los pedidos deben estar pagados para confirmarse. En Instagram se recibe comprobante, se ingresa en Toteat y se envía boleta. El comprobante no prueba abono bancario.
- Mercat permite programar hasta cinco días y maneja disponibilidad independiente del inventario básico de Toteat. El prototipo no impone ese límite global a otros canales.
- Venta/comanda same-day descuenta automáticamente en Toteat.
- Pedido programado con stock: venta `-1`, luego ajuste `+1` para restituir disponibilidad.
- Programado sin stock: ajuste `+1` antes de venta `-1`.
- Más tarde, al asignar torta física con comanda: ajuste manual `-1` del inventario básico.
- Un pedido futuro es un compromiso de producción, no necesariamente una reserva física de hoy.
- Los detalles actualmente viven en comentarios; extras/opciones podrían estructurarse después de revisar evidencia real.

En esta versión «marcado físico» es solo un registro de estado con responsable/fecha. No hace el ajuste manual ni afirma que exista stock. Cancelar tampoco anula la venta, devuelve dinero ni revierte inventario. Deben conservarse estos límites cuando se implemente el mapeo real.

Supuestos de diseño para revisar con el usuario: horario de retiro `America/Santiago`; estados por ítem; resumen de encargadas excluye entregadas/canceladas; correcciones de ítems avanzados requieren reversión explícita. No se ha validado ninguna política adicional de producción o de despacho.


## Aclaraciones confirmadas durante la demo

- Módulo de tortas y dulces ENTEROS: también cheesecake, pie y kuchen cuando el catálogo real los clasifique así. Nunca trozos/porciones.
- `**` = cantidad manual de enteros reservados para trozar y mantener vitrinas; 0..X. Físico menos reserva determina venta entera, sin volver a restar encargos.
- Flujo: pendiente de marcado → marcado solicitado por la jefa → confirmado por Esteban → entregado. El estado no indica si las bases están elaboradas.
- Alerta inicial dentro del ERP; WhatsApp solicitado para una etapa posterior, sin envío en esta versión.
- Bizcocho: 10/20 personas. Hojarasca, mixta y zanahoria: solo 20. No imponer esos tamaños a otros dulces enteros.
- Bases iniciales para 20: Amor hojarasca 10 (provisional editable), Hoja manjar 14, Mixta 0,5 bizcocho chocolate + 6 hojarascas. Rellenos y recetas no confirmadas quedan pendientes.
- Plan actual como escenario de todas las encargadas; falta decidir/registrar qué queda realmente por elaborar. No se deduce de «sin marcar».

## Entrega y catálogo local

En un pedido demo nuevo, elegir explícitamente entrega programada o inmediata. Programada exige teléfono ficticio, fecha/hora y pago simulado; inicia pendiente de marcado. Inmediata confirma entrega al guardar la venta simulada. El producto conserva la misma identidad; no se crean categorías o conteos duplicados. Pedidos anteriores sin tipo aparecen por confirmar, conservando estados e historial.

El panel Catálogo local distingue los productos incorporados manualmente de los ejemplos. Permite consultar formato, identidad de origen y disponibilidad desconocida cuando no existe conteo. La incorporación no representa conexión ni sincronización con Toteat.

Los comentarios originales se conservan. En la instalación privada, ventas cerradas y saldadas con datos interpretables pasan automáticamente a Agendadas. Descuento total autorizado en Toteat y saldo cero también agenda, con etiqueta propia. La boleta es opcional. Los ejemplos públicos y QA usan solo datos ficticios; los datos reales recibidos de API permanecen privados. Manual SOS y la excepción sin pago están disponibles solo con sesión de socio, motivo e historial; sin pago se mantiene como estado independiente.

Los socios autenticados pueden corregir cliente, teléfono, retiro/delivery, dirección y fecha/hora con motivo e historial. No se sobrescribe el comentario original ni se modifica Toteat. La fecha aparece en la tarjeta; simulaciones y operación mantienen rangos separados.

## Acceso y navegación vigentes

Todo el ERP requiere inicio de sesión. Socios administran y autorizan excepciones; producción consulta/solicita marcado/actualiza bases; caja consulta/ve boletas/registra entregas de ítems ya marcados. La cuenta autentica los cambios nuevos y no se puede elegir un responsable ajeno desde un formulario. El selector de perfiles demo fue retirado.

Los nuevos módulos son solo estructura vacía y subpestañas; no capturan GPS, no leen GeoVictoria, no calculan remuneraciones ni conectan Power BI. La operación de tortas existente sigue en Pedidos de tortas. El usuario aprobó también las propuestas de costos/rentabilidad, gestión de clientes y activos/mantenimiento.
