# Cierre acotado de Proveedores: OCR, pago, imágenes y PDF

Cambios locales del 14 de octubre de 2026. Conserva CSV SII y no requiere cambios de esquema.

- `erp/ocr_descriptions.py`: lectura offline de las celdas detectadas de descripción, con modelos latinos oficiales, seis recortes acotados y dos lectores. Nunca modifica códigos ni columnas numéricas. Conserva texto inicial, propuesta, alternativas y confianza; todo requiere cotejo humano. Sin reglas por proveedor, folio o nombre de producto.
- `erp/document_intake.py`: volver a leer crea una comparación identificada e idempotente. No cambia el texto anterior ni la revisión. Aplicar exige versión vigente y confirmación; reemplazar campos exige confirmación adicional. El historial conserva texto anterior y correcciones guardadas y no guardadas. Original binario intacto.
- `static/payables.css`: ancho explícito de casillas, formularios de una columna en pantallas pequeñas y controles sin desborde.
- `static/image-attachment.js`: pegado de JPG/PNG en área enfocada de todos los cargadores compatibles; vista previa, reemplazo y quitar. Máximo 8 MB; PDF mediante selector. No usa lectura de portapapeles en segundo plano ni envía automáticamente. Los importadores CSV no cambian.
- `erp/purchase_pdf.py`: PDF real, descargable y privado para agenda y OC. Incluye proveedor/contacto conocidos, fechas, cantidades/unidades, productos y notas; borrador explícito. Sin saldos, bancos, costos ni datos de otros proveedores. Exportar no envía ni modifica estado. Encabezado de tabla repetido y fuente local con acentos.

## Modelos offline

Fuente oficial: https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml (ModelScope RapidAI/RapidOCR, revisión v3.10.0). Pesos en `ocr-runtime/rapidocr-1.4.4/latin-models/`; el lector verifica SHA-256 antes de cargarlos. No descarga modelos durante una solicitud.

- `latin-v3.onnx`: `e9d7a33667e8aaa702862975186adf2012e3f390cc0f9422865957125f8071cf` (PP-OCRv3 Latin, manifest bajo PP-OCRv4).
- `latin-v5.onnx`: `b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a` (PP-OCRv5 Latin).

Modelo principal v3 con margen vertical de 10%; v5 y los otros recortes son alternativas visibles. Las puntuaciones de modelos distintos no son comparables. El contenedor de lectura mantiene bloqueo de red, timeout de 90 s y límites de documento existentes. Sin ambos modelos sigue disponible lectura original y revisión manual; un archivo inválido no se carga.

## Validación

Pruebas aisladas de relectura, idempotencia, versiones, permisos por HTTP, original conservado, ausencia de efectos financieros/stock y PDF multipágina. Pruebas de MIME/tamaño del componente de pegado. Verificación visual con datos ficticios en los siete flujos compatibles, ancho de modal desde 320 px y foco por teclado. Zoom nativo de 200% no verificado; el control disponible validó anchos de viewport.

La foto de referencia recupera 4/4 descripciones y formatos y conserva códigos, cantidades, precios e importes. No constituye garantía para otras facturas. Una foto sintética inclinada recuperó tres productos/importes, pero fusionó código y descripción: conserva `POR-REVISAR` en el código. El PDF sintético con texto recuperó dos líneas; el formato de 50 unidades quedó vacío para cotejo. OCR nunca registra por sí solo facturas, pagos, pedidos o stock.
