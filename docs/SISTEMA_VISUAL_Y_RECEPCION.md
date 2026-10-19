# Sistema visual común y recepción opcional

Base: release proveedores-af-ui-20261014T184320Z. Octubre 2026.

## Interfaz

La hoja `static/design-system.css` comparte colores, tipografía, tamaños, foco,
formularios, tablas, avisos, diálogos, navegación y estados vacíos entre los 17
módulos existentes. `app.js` conserva sus destinos y secciones, con un rail de
áreas y menú contextual en escritorio y navegación plegable en móvil.
La referencia es la maqueta aprobada Propuesta visual ERP Oveja.png. No se
copian sus empresas ni importes ficticios. El cargador aparece solo durante
consultas y conserva su controlador accesible y cancelación al navegar.

Inicio, pedidos de tortas, tareas, compras/proveedores, transferencias y las
secciones implementadas de reparto mantienen sus funciones. Personas,
documentos, turnos, asistencia, inventario, producción, catálogo, reportes,
costos, clientes y mantenimiento conservan sus estados de preparación dentro
del menú: no se crean funciones vacías adicionales. Las herramientas existentes
de stock, producción, catálogo y analítica de tortas siguen dentro de Pedidos.

Proveedores permite ficha lateral Datos / Cuenta corriente / Compras. Conserva
todos los campos y accesos previos. En pantallas estrechas la ficha pasa debajo
de la lista; el resto de los módulos conserva el ancho que necesita su flujo.

## Recepción y existencias

Guardar una recepción no crea movimientos de stock. Aceptado y rechazado
siguen conciliándose con pedido y factura; los precios revisados permanecen en
el flujo Precios y conciliación y no dependen del ingreso a bodega.

`POST /api/suppliers/procurement/receipts/{id}/warehouse-entries` es una acción
explícita para socios, protegida por el mismo control de sesión y origen.
Solicita versión de recepción y pedido, bodega, fecha, motivo, confirmación,
líneas, cantidades en unidad de pedido y request_id. La transacción inmediata
serializa concurrencia; el identificador y firma evitan repetición y las
versiones y pendientes impiden exceder lo aceptado.

Dos tablas aditivas (`pc_receipt_entries`, `pc_receipt_entry_lines`) enlazan
ingresos parciales con documentos y líneas de stock. No se reconstruyen tablas
antiguas. Los ingresos nuevos se almacenan como ajustes vinculados y se muestran
como Ingreso desde recepción. Las entradas históricas de tipo receipt siguen
reconociéndose como ingresadas. No se generan pendientes falsos ni movimientos
al actualizar el esquema.

Los estados Sin ingresar, Ingreso parcial e Ingresado se calculan por las
cantidades aceptadas y efectivamente ingresadas; una recepción solo rechazada
se muestra sin cantidades aceptadas. La anulación revierte únicamente los
documentos ingresados y conserva la evidencia. Conteos posteriores o un saldo
insuficiente bloquean el reverso completo. La vía genérica de reverso de stock
no puede alterar entradas vinculadas. Una recepción anterior a un conteo puede
registrarse informativamente; su ingreso queda bloqueado para evitar duplicar
lo ya contado.

Si existen cantidades aceptadas pendientes de bodega, las compras sugeridas
piden revisar su destino antes de proponer una compra adicional. No se calculan
ventas ni consumos desde Toteat, ni se envían pedidos o pagos.

## Directorio desde Drive

La carga operativa se ejecuta con un script de mantenimiento privado, fuera del
servidor HTTP, a partir de A1:K36 de la pestaña Proveedores + Dia de pedidos.
Conserva IDs, nombres y campos bancarios existentes; no inventa RUT. Los campos
vacíos no borran datos y las discrepancias se registran para revisión. Los
sustitutos se guardan como texto, sin fusionar identidades. Las equivalencias
de nombres requieren confirmación. La carga es transaccional y repetible sin
duplicados usando un identificador persistido en pc_requests y auditoría.

Las verificaciones y capturas usan bases ficticias aisladas. La publicación
requiere respaldo restaurado y comprobado, ensayo del esquema sobre copia,
pruebas aprobadas y comparación de facturas, pagos y saldos antes/después.
