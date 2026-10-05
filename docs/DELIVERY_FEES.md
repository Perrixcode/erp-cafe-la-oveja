# Montos diarios de reparto

Apartado privado para socios: Despacho y reparto → Montos diarios. Histórico desde septiembre de 2026, por día operativo del turno Toteat. Suma exclusivamente `products[].payed` del producto `TOTEATDVYCOST` (Costo Delivery), sin confundir la tarifa con el total del pedido ni multiplicarla de nuevo por cantidad.

La primera consulta habilita la actualización diaria del servidor. Abrir el apartado o Actualizar importes solicita una lectura; las peticiones concurrentes se combinan. La pantalla consulta el avance periódicamente sin volver a encolar cada vez. El trabajador reutiliza las respuestas de ventas y añade como máximo una fecha histórica por ciclo de 90 segundos. Comparte el bloqueo por HTTP 429 y respeta Retry-After. Funciona aunque el navegador esté cerrado.

Persistencia por ámbito y pago: repetir la respuesta no duplica importes. Cambios conservan historial. Un error mantiene los valores anteriores y se muestra; un día pendiente no aparece como cero. Un concepto desaparecido conserva su valor con advertencia hasta revisarlo. Los importes de venta se presentan como pagos Toteat asociados, nunca como recaudación del repartidor.

No determina por sí mismo la fecha física de entrega ni asigna repartidores. La liquidación individual, app móvil, GPS y sincronización de estados quedan fuera de esta publicación. No ejecuta pagos ni escrituras a Toteat/Mercat.

Importación inicial: `scripts/import_delivery_history.py` valida proyecciones ya consultadas en una base privada aislada; conserva la fecha original de consulta. Nunca incluir la proyección, CSV, bases o importes del negocio en Git. La publicación debe respaldar la base antes de importar las mismas respuestas con `delivery_finance.apply_day`, habilitar `request_refresh` y reiniciar el lector existente. No necesita nuevas credenciales ni servicios.
