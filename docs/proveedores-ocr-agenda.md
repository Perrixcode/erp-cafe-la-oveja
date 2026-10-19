# Facturas por documento y agenda de pedidos

La importación CSV del SII conserva sus rutas y controles. Foto/PDF comparte la misma identidad de factura y no confirma pagos por una condición «contado».

## Revisión de documentos

1. Abrir Facturas → foto/PDF. El archivo original se conserva y se deduplica por SHA-256.
2. Revisar proveedor, tipo, folio, emisión, vencimiento y total. El OCR solo propone valores. Un RUT detectado requiere dígito verificador válido para sugerirse.
3. Cotejar productos: código, descripción, formato, cantidad comprada, precio, descuento e importe. Las cifras con confianza baja quedan vacías. No se infieren cantidades dividiendo importes.
4. Guardar una revisión incompleta sin crear factura, o confirmar cuando cuadren líneas e impuestos. Toda modificación reinicia la confirmación final.
5. Asociar insumos y unidades antes o después de confirmar. Una caja de 5 × 1 kg sigue siendo una caja; la conversión requiere una equivalencia explícita del catálogo.
6. Preparar una OC local desde las líneas revisadas. Esto vincula la factura y alimenta la conciliación; no recibe mercadería, ingresa stock, paga ni envía una compra.

«Volver a leer original» está disponible para borradores pendientes anteriores a esta mejora. Conserva la revisión manual guardada. Los datos manuales guardados prevalecen sobre sugerencias nuevas; revisar y editar los campos contra el original.

Las líneas asociadas alimentan precios históricos por unidad base, separando neto y bruto. Si cambia la identidad financiera de la factura, el precio deja de ser válido para comparar. No se admiten líneas manuales duplicadas sobre una factura que ya tiene detalle por documento. Las asociaciones posteriores a una OC quedan bloqueadas para conservar su equivalencia histórica.

## Agenda

- Día, semana y pendientes. La fecha usa el día local del negocio que ya usa el módulo.
- Los días habituales y la hora límite vienen de la ficha del proveedor; muestran disponibilidad, no obligaciones de compra.
- Crear una intención explícita, con productos libres o asociados, cantidad, entrega esperada y notas. Hay una intención por proveedor y fecha.
- Editar o posponer conserva fecha original, versión, motivo y actor. Conflictos de fecha y versiones obsoletas se rechazan.
- Preparar OC exige catálogo y equivalencias revisadas, y vincula una sola OC. No marca la compra realizada automáticamente.
- Marcar realizada exige fecha, confirmación y referencia externa de lo realizado. Es un registro administrativo; la aplicación no envía pedidos.
- Una intención realizada conserva su historial; el detalle de una intención con OC queda fijado. Posponer su seguimiento no cambia la fecha original de la OC.
- Directorio, compras sugeridas, panel, agenda y OC tienen accesos entre sí. Las sugerencias bloqueadas por stock desconocido no se convierten en cantidades inventadas.

## Implementación y límites

`document_intake.py`, `invoice_ocr.py`, `ocr_layout.py`, `local_invoice_reader.py`, `document_lines.py`, `purchase_agenda.py` y sus interfaces JS. Tablas nuevas exclusivamente aditivas: `ap_document_reviews`, `pc_document_lines`, `pc_purchase_agenda`. Sin reescritura de tablas existentes.

El lector Linux opcional se instala en `/opt/oveja-erp/ocr-runtime/rapidocr-1.4.4` desde `requirements-ocr.txt`, separado del entorno web. Sus modelos se incluyen en el paquete; no se descargan modelos durante la lectura. ONNX desactiva telemetría y el proceso bloquea sockets Python. Fotos se orientan por EXIF; se usan coordenadas y columnas para reconstruir las líneas. Tesseract local aporta una segunda lectura textual de etiquetas. PDF con texto usa `pdftotext -layout`; páginas escaneadas se rasterizan localmente con `pdftoppm`.

Un bloqueo local limita el motor a una lectura simultánea por usuario del servicio para acotar memoria. Si está ocupado, el borrador permite volver a leer.

Límites: 8 MB, 40 millones de píxeles, 200 KB de texto, 90 segundos, hasta 20 páginas digitales o 5 escaneadas. Si la lectura falla se conserva el original y queda disponible la revisión manual. Estos límites no garantizan el reconocimiento de todas las fotos o formatos. Sombras, manuscritos, impresión borrosa y nombres pueden exigir correcciones. El OCR no autoriza acciones por el contenido del documento.

Pruebas: `tests/test_document_intake.py`, `test_invoice_ocr_agenda.py`, `test_procurement_http.py` y regresión completa. Ejecutar en bases temporales y loopback. Nunca correr fixtures contra la base operativa.
