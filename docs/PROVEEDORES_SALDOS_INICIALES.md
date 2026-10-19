# Proveedores: preparación de saldos iniciales

Gestión de proveedores → Pagos muestra una copia privada del libro auditado. Proveedores permite definir una condición de compra predeterminada para cada nombre exacto. Cada fila puede usar ese valor o una excepción: crédito, contado/contra entrega o sin definir. No se infiere la condición del estado de pago, del saldo ni de la recepción de mercadería.

Esta etapa no crea pagos, corrige importes, fusiona razones sociales ni aprueba saldos. Los estados y abonos son antecedentes declarados. Los saldos positivos y negativos se muestran separados; un saldo vacío conserva su ausencia y presenta el cálculo como referencia. Los duplicados permanecen en el original. Las respuestas del formulario no crean obligaciones adicionales.

La importación administrativa usa `scripts/import_supplier_opening.py` con `--input`, `--database` y `--actor`. El JSON auditado contiene `file` (id, title, modified_time), `ledger` y `form`, con las filas de origen. Se conserva el archivo completo, su SHA-256 y cada fila sin modificaciones. Nunca incluir fuentes reales en el repositorio. La misma copia se puede importar de nuevo sin duplicar; otra versión o fuente se rechaza para evitar acumular saldos superpuestos sin conciliación.

Las tablas `supplier_opening_batches`, `supplier_opening_records` y `supplier_opening_annotations` son aditivas. Las anotaciones conservan condición, motivo, responsable de sesión, fecha y versión; los reintentos son idempotentes y los cambios concurrentes desactualizados se rechazan. Cambiar la condición no cambia el estado de pago ni resuelve automáticamente una incidencia. Las variantes de nombres se mantienen separadas al definir condiciones; la normalización para detectar candidatos solo ignora mayúsculas, acentos y espacios.

La lectura y las anotaciones están restringidas a socios mediante `/api/suppliers/opening` y `/api/suppliers/opening/annotations`. No existe importación pública HTTP. La vista se actualiza al entrar o pulsar Actualizar vista; el refresco general no reemplaza filtros, páginas, detalles o borradores. Actualizar vista no vuelve a leer Google Sheets.

Validación: `python -m unittest discover -s tests -p 'test_supplier_opening.py'`. Las pruebas cubren importación, trazabilidad, diferencias entre fuentes, folios provisorios repetidos, valores ausentes, condiciones heredadas y excepciones, persistencia, versiones concurrentes y acceso HTTP. Las capturas en `qa/supplier-opening-*-fake.png` contienen datos ficticios.

Pendientes de etapas posteriores: resolver importes y duplicados con evidencia, registrar facturas nuevas, abonos y comprobantes, programar pagos, exportar resúmenes e importar XML/fotos. No hay conexión al SII ni a grupos de mensajería en esta entrega.
