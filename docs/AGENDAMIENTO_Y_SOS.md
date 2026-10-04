# Agendamiento, boleta y contingencia SOS

## Flujo vigente

Comanda abierta → Lectura de Toteat. Cuando la venta esté cerrada y saldada, y el comentario permita obtener nombre, fecha y hora, pasa automáticamente a Agendadas. No hay autorización manual rutinaria. Lectura conserva solo lo pendiente o que requiere revisión; nunca solicita marcado. El ticket agendado conserva todos los productos, comentario original, pago, boleta disponible e historial.

Descuento total autorizado en Toteat por los dueños y saldo cero también agenda. Se muestra «Saldado con descuento» y no «pagado». La boleta es un adjunto opcional: un documento ausente no bloquea ese caso. La llegada repetida no duplica pedidos ni historial. Un comentario «Pagado» por sí solo no confirma dinero. Inmediata y programada mantienen el mismo producto/inventario; comentario ausente no significa inmediata.

Marcado y entrega conservan sus pasos: pendiente → solicitado → marcado físico → entregado. Agendar no marca ni entrega. La fecha aparece en las tarjetas, incluidos resumen semanal y mensual. Las pruebas quedan separadas de la operación con un acceso visible cuando existen en el rango.

## Cambios solicitados por el cliente

Solo una sesión de socio autenticada habilita cambiar nombre, teléfono, fecha/hora, retiro/delivery y dirección. Se exige dirección en delivery y motivo en todo cambio. Se conserva comentario fuente, pago, boleta, ítems y estado de marcado; las siguientes sincronizaciones no borran la corrección local. Fecha, responsable autenticado y antes/después quedan auditados. No se envían esos cambios a Toteat ni al cliente.

Cuentas locales creadas desde `scripts/create_partner.py`, sin credenciales predeterminadas ni alta HTTP. Contraseña oculta con scrypt, sesiones de ocho horas, revocación al salir/reiniciar y límite de intentos. Los selectores visuales no conceden permiso. Falta que los socios creen sus cuentas en la instalación; el resto de operaciones del prototipo todavía no tiene autorización integral para un despliegue.

## Boleta correspondiente

Se verificó una boleta real: misma orden, pago e importes coincidentes con ventas. El enlace fuente HTTPS permitido solo sirve para descargar el PDF en privado; el ticket expone una ruta local, con comprobación de firma PDF y hash. Descargar para enviar no envía automáticamente. Datos privados, PDFs y bases no se publican en Git.

## SOS y agendado sin pago: implementados para socios

Después de iniciar sesión, **Contingencias SOS → Nuevo pedido SOS** permite ingresar cliente, teléfono, catálogo/cantidades, fecha/hora, retiro/delivery y dirección, canal, nota y motivo. Es un ingreso local: no crea venta en Toteat.

- **Sin pago:** se guarda como pendiente de autorización, fuera de Agendadas y de la demanda de producción. Desde el ticket, un socio pulsa **Autorizar sin pago** y registra el motivo. Pasa a **Agendado sin pago**; pago sigue falso, no se crea boleta, marcado ni entrega. Otra petición idéntica no repite el evento.
- **Pago verificado por socio:** exige confirmación explícita y referencia de verificación. Agenda con etiqueta de registro manual; no se presenta como pago confirmado por Toteat. La boleta permanece pendiente hasta obtener el archivo real.
- Cuenta autenticada, hora del servidor, motivo y estados anterior/posterior quedan en auditoría. Un rol o nombre enviado por el navegador no concede permisos. Cajeras sin sesión de socio reciben 403 en las rutas SOS y de excepción; también al consultar un borrador por su ID/historial.
- Cada envío lleva un ID de solicitud; repetirlo no duplica el pedido. El mismo ID con datos distintos se rechaza. Se revisan coincidencias exactas antes de crear otro ingreso con un ID nuevo. La versión protege autorizaciones y conciliaciones frente a cambios simultáneos.

## Vínculo posterior con Toteat

El socio puede seleccionar una **comanda ya recibida**, con identidad restaurante/local/orden validada y productos/cantidades coincidentes. No se aceptan identidades inventadas desde el formulario. Al autorizar un SOS vinculado, esa comanda deja de aparecer en Lectura; cualquier conflicto posterior vuelve a quedar visible para revisar.

Cuando llega su venta cerrada y saldada, la relación exacta permite aplicar el pago y boleta al **mismo pedido**, sin sumar otra demanda. Se conservan origen Manual SOS, IDs de ítems, marcado, cambios de cliente, dirección y nota manual. El comentario fuente se conserva por separado. Un descuento saldado mantiene su estado propio, sin inventar dinero recibido.

Sin vínculo fiable, productos iguales junto con coincidencia de fecha, nombre o teléfono generan una revisión; no se fusionan ni importan automáticamente. En **Contingencias SOS**, un socio verifica la comanda y elige vincular al SOS o declarar que son pedidos distintos, siempre con motivo. La siguiente lectura aplica la resolución. Un vínculo ya usado, cantidades distintas o mezcla entre pruebas y operación se rechaza para revisión. Los casos no identificables por esos datos requieren verificación humana; no hay emparejamiento universal por similitud.

Los borradores no se pueden marcar. Una vez autorizados, conservan el flujo normal de marcado y entrega, independiente del pago. No hay escrituras en Toteat ni envío de mensajes.
