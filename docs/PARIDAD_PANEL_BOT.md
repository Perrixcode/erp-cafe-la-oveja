# Migración del panel de Ovejita al ERP

El ERP conserva su diseño A. El bot, sus credenciales, sus respuestas y su base siguen separados. La migración incluye las funciones operativas del panel anterior:

| Función | Ubicación en el ERP | Comprobación |
| --- | --- | --- |
| Revisiones, comanda elegida y monto esperado | Conciliación de transferencias → Transferencias del local | Identidad de revisión/venta, selección original y productos; sin asociar por aproximación |
| Foto del comprobante | Detalle de revisión | Vista previa y descarga privada, misma ventana, SHA-256 |
| Resultado, verificaciones y observaciones | Detalle de revisión | Evaluación original conservada |
| Revisar / reabrir pendiente | Detalle → Seguimiento | Socio autenticado, motivo 5–1000 caracteres, versión y operación idempotente |
| Historial de seguimiento | Detalle de revisión | Misma tabla `resoluciones` del bot; actor, fecha, acción y motivo |
| Filtros | Transferencias del local | Fechas en Chile, cajera y estado; hasta 366 días |
| Contadores y distribución | Transferencias del local | Revisiones, compatibles, pendientes, ventas únicas, monto esperado, motivos/cajeras/días |
| Exportar | Descargar CSV | Todo el filtro, no solo la página; UTF-8 con BOM, neutralización de fórmulas, auditoría de descarga |
| Cierres y estado de envío | Cierres diarios | Informe original íntegro, cajas, medios, exclusiones y advertencias |
| Salud y alertas | Estado del bot | Procesador atrasado después de 120 s; otros componentes después de 7200 s; envíos inciertos |
| Acceso privado | Login ERP | Solo socios acceden a transferencias; sesión y origen verificados por servidor |

## Seguimiento compartido

`oveja-erp-followup` es un servicio pequeño accesible exclusivamente por socket Unix, sin puerto de red y sin credenciales. El usuario del ERP no puede leer la base del bot. El puente admite solo consultar el historial o agregar una resolución `revisado` / `pendiente`; no modifica importes, evaluaciones, pagos, pedidos ni envíos.

Se conserva `resoluciones` porque `cierres.py` y `monitor.py` ya usan su última acción. Separar el seguimiento en otra base produciría contadores contradictorios. Una tabla adicional, `erp_followup_requests`, vincula cada operación a su resolución para que un reintento no duplique el evento. La versión y huella de origen impiden guardar sobre una revisión modificada. El usuario del evento procede de la sesión ERP.

Las consultas combinan la copia privada con el historial actual del puente; no necesitan esperar el próximo minuto de proyección. Si el puente no está disponible, la información conservada sigue visible, el cambio no se confirma y la exportación conectada se bloquea hasta recuperarse. La UI permite reintentar la misma operación después de una falla de red.

Cada exportación registra en `transfer_exports` usuario, fecha, filtros, cantidad de filas y SHA-256. No se envía el CSV ni se cambia el resultado de ningún comprobante. Los resúmenes de proveedores para WhatsApp pertenecen a otro flujo futuro.

## Verificación y reversión

Pruebas aisladas: permisos/origen/identidad del socio, revisión y reapertura en la consulta original del monitor, datos y salida del bot intactos, doble petición concurrente, versiones antiguas, reinicio del puente, indisponibilidad, exportación de más de una página, filtros, fórmulas y auditoría. QA Chrome dedicado en 1180 y 320 px con datos ficticios y descarga real del CSV.

Antes de retirar el panel anterior: respaldo SQLite en línea verificado, comparación de revisiones/historial/cierres, pruebas de funciones y HTTPS. La retirada consiste en detener/deshabilitar `cafeteria-panel` y redirigir `/panel*` al ERP. Se conservan código, configuración, credenciales y la base. Ante regresión: recuperar el bloque de Caddy respaldado, validar/recargar y reactivar `cafeteria-panel`. El panel original ve las mismas resoluciones hechas desde el ERP. Nunca restaurar una base antigua sobre nuevos movimientos para revertir una interfaz.
