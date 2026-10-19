# Comprobantes de pagos del día

En **Proveedores → Facturas y pagos**, **Exportar comprobantes del día** abre una selección por fecha de pago; comienza en el día actual de Chile. Cada fila muestra proveedor, importe y folios vinculados. Los anticipos pueden no tener aplicación a una factura.

La vista previa contiene el resumen de pagos seleccionados y los respaldos, con portada por anexo. La descarga usa exactamente el PDF de esa vista previa. No envía mensajes, registra pagos ni modifica estados, movimientos o aplicaciones.

## Origen y versiones

- Se consultan pagos históricos de `ap_payments` y pagos de cuenta corriente de `ap_account_entries`. Se excluyen anulados y notas de crédito. Un pago conjunto es una fila aunque tenga varias aplicaciones.
- La fecha es `paid_on` u `occurred_on`, no la fecha de carga del adjunto. Los pagos históricos sin fecha no se asignan artificialmente a un día; se informa su existencia.
- Las aplicaciones a facturas son las vigentes al generar el PDF, no una reconstrucción histórica de la asignación al pagar.
- El modelo de adjuntos conserva archivos pero no tiene una marca de sustitución o vigencia. Por defecto se intenta incluir el último por `created_at,id`. Si no es legible, se informa **Último comprobante no legible** y el respaldo queda pendiente; el pago permanece en el resumen.
- Se puede elegir expresamente otra versión en el selector, con ID, nombre y fecha. El PDF identifica esa elección. Nunca se sustituye automáticamente un archivo ilegible por uno anterior.
- El SHA-256 permite incluir una sola copia del mismo respaldo asociado a varios pagos, conservando todos los pagos y sus importes. No se deduplican pagos por compartir una imagen.
- La apertura técnica de un archivo no certifica correspondencia, autenticidad ni ejecución bancaria.

## Formatos y límites

Se conservan todas las páginas de los PDF incorporados, con su tamaño, recorte, rotación y apariencia. Las imágenes JPEG/PNG respetan proporción y orientación EXIF; la transparencia se representa sobre blanco. Los PDF cifrados, corruptos o imágenes no admitidas quedan pendientes, con advertencia. Los originales no se alteran. El PDF exportado elimina acciones activas embebidas.

Límites por consulta: 100 pagos, 40 comprobantes únicos, 32 MB de archivos revisados, 120 páginas originales de respaldo. Cada archivo ya está limitado por el ERP a 8 MB; imágenes de hasta 20 megapíxeles y 12000 píxeles por lado. El consolidado admite 40 MB y 240 páginas incluyendo resúmenes y portadas. Si el conjunto supera el límite se solicita reducir la selección; nunca se truncan silenciosamente páginas.

## Acceso, consistencia y despliegue

Los endpoints GET `/api/suppliers/payment-receipts`, `/preview` y `.pdf` conservan la autorización de socios del módulo. Las respuestas son `no-store`. La selección lleva una huella del estado consultado: cambios de pagos, aplicaciones o adjuntos obligan a actualizar. La base se abre con `PRAGMA query_only=ON` y una transacción de lectura.

No hay migración de esquema. Dependencias fijadas: ReportLab 5.0.1, Pillow 12.3.0 y pypdf 6.21.0. Antes de activar: pruebas aisladas, copia de código restaurada y comparada, respaldo de datos restaurado y verificado, ensayo de cambio de enlace y reversión. La reversión restaura el enlace de código anterior y conserva la base vigente. No se crean registros de prueba en producción.

Pruebas: `test_payment_receipts.py`, `node tests/test_payment_receipts_ui.cjs`, suites existentes de proveedores/pagos/PDF, acceso HTTP y CSP. La interacción se comprueba desde terminal; no se automatiza el navegador del equipo ni se utilizan comprobantes reales como fixtures.
