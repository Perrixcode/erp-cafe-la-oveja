# Bolsas de efectivo: entrega aislada para integración

Implementación preparada el 16 de octubre de 2026 en una copia de trabajo. No desplegada ni importada en producción. Mantener el cierre manuscrito actual hasta la integración coordinada.

## Contrato de las etiquetas emitidas

- URL permanente: `https://erp.example.test/?module=cash&bag=<UUIDv4>`.
- Manifiesto: Library `libfile_0c950324dd448191867148c6bf1e6836`, conservado en `erp/data/cash-bags-preprinted-000001-000020.json`.
- Los dos lotes suman BOL-000001 a BOL-000040. Los UUID se toman del manifiesto; el importador no genera ni sustituye sus identificadores.
- PDF original: Library `libfile_ae246ecec64081919eb46ea0621c15cd`, conservado en `erp/data/cash-bags-preprinted-000001-000020.pdf`.
- SHA-256 del PDF: `624b3fb49b1fdd061808741cf83805c1e1cc4390a9790f5294f9a755a4223b64`.
- Huella del manifiesto canonicalizado: `144d218b857def911c95b49b68127d78a4aad565b5c113e19f76f68ff346999c`.
- Cada lote tiene cinco hojas Carta; cuatro etiquetas de 7 × 12 cm por hoja. Imprimir al 100 %, sin ajuste. No se hizo prueba física de impresión ni de lectura con teléfono.
- Reimprimir el lote importado devuelve los bytes originales, incluso después de completar sus datos. Si contiene alguna etiqueta anulada, la reimpresión completa se bloquea para evitar reutilizarla.

Segundo lote definitivo: Library manifiesto `libfile_2c91182764d0819189424e75251abe3c`, PDF `libfile_cfd8fbea86f881919873cf971f27246a`. Archivos `erp/data/cash-bags-preprinted-000021-000040.json` y `.pdf`. Huella canónica JSON `6b17435912cc8a088a66e7949bbc1b5df9ecd814905e7f510e2106b88787e291`; huella PDF `f14608c84709ccdf66404ebbf37c338b3962d25312fa2373062194485d2bad96`. Los dos PDF se preservan sin alterar ni invalidar etiquetas.

## Comportamiento

El enlace abre la pantalla existente del ERP y requiere sesión para obtener los datos. Un GET, escaneo o descarga no cambia las fichas ni su estado. El UUID no contiene dinero, credenciales ni información de la caja.

Caja y Socio pueden completar fecha, caja, turno, responsable, A total contado y B fondo retenido. La confirmación expresa de datos y entrega calcula C=A−B y deja la bolsa pendiente. A incluye el fondo y las propinas físicas presentes. Son datos manuales, sin precarga de Toteat. Una ficha pendiente permite corregir su declaración con versión y auditoría; después de verificarla, la declaración queda bloqueada.

Solo Socio puede crear lotes futuros, anular o verificar una entrega pendiente. Verificar requiere conteo real y confirmación expresa; si difiere de C, exige observación. Se conservan A/B/C declarados, importe real, diferencia, usuario y fecha. Estados: sin completar, pendiente, verificada, con diferencia o anulada. Producción no puede consultar ni modificar las bolsas. No se modificaron permisos globales ni cuentas reales.

El usuario elige la cantidad y ve el siguiente correlativo. No hay número inicial editable ni flujo DOCX. Número, bolsa y UUID conservan una relación inmutable. Los números son correlativos, únicos y persistentes, sin reinicio diario/anual ni reutilización de anulados. Las operaciones usan transacción, clave de idempotencia y control de versión. La lista incluye paginación y resumen de avance por lote. El PDF tiene vista previa antes del enlace de descarga.

## Importación coordinada

`scripts/import_cash_bags.py --db RUTA_EXISTENTE` realiza solo una comprobación de lectura, abre SQLite en modo `ro` y no crea esquema, archivo, fichas ni secuencia. El primer manifiesto es el predeterminado; el segundo se indica con `--manifest erp/data/cash-bags-preprinted-000021-000040.json`. La salida `ready_after_schema` significa que no existe aún el esquema de bolsas; no significa que se haya importado.

El esquema aditivo `erp/cash_bags_schema.sql` se inicializa al arrancar la versión integrada del servidor, nunca al escanear. Mantiene tablas propias; no altera pedidos, pagos ni movimientos financieros.

La futura aplicación debe hacerse únicamente tras la revisión coordinada y el respaldo correspondiente:

```sh
python -B scripts/import_cash_bags.py --db RUTA_EXISTENTE
python -B scripts/import_cash_bags.py --db RUTA_EXISTENTE --apply --actor IDENTIDAD_DEL_RESPONSABLE
python -B scripts/import_cash_bags.py --db RUTA_EXISTENTE --manifest erp/data/cash-bags-preprinted-000021-000040.json
python -B scripts/import_cash_bags.py --db RUTA_EXISTENTE --manifest erp/data/cash-bags-preprinted-000021-000040.json --apply --actor IDENTIDAD_DEL_RESPONSABLE
```

Ningún comando de aplicación fue ejecutado sobre producción. Exige base existente y esquema inicializado. Dentro de una sola transacción valida el lote exacto, colisiones de número o UUID, secuencia aún no usada e integridad del lote previo. Una colisión o manifiesto diferente cancela todo y exige revisión; no hay opción de forzar o reasignar. El primer lote fija la secuencia en 21 y el segundo la fija en 41. El segundo exige que el primero esté importado e íntegro. Repetir cualquiera conserva estados/historial y no reduce una secuencia que haya avanzado. La creación de nuevos lotes en un origen público permanece bloqueada hasta importar AMBOS lotes, por lo que BOL-000021 no se ofrece para generación. Después de ambos, el primer número disponible es BOL-000041.

## Integración con trabajo concurrente

Se entrega `integration.patch` exclusivamente para los cambios pequeños en `app.py`, `static/app.js` y `static/index.html`. Aplicarlo por fragmentos sobre la versión vigente, preservando el trabajo paralelo de Documentos; no reemplazar esos archivos completos con los de esta copia.

Los archivos nuevos se entregan en `new-files/`. `files-manifest.json` enumera rutas y huellas; `cash-bags-text.patch` incluye también los nuevos archivos de texto. El PDF binario original se copia desde `new-files/erp/data/`. Usar una sola vía para los archivos nuevos; no aplicar ambas.

Rollback funcional: deshabilitar ruta y navegación si fuera necesario, conservando tablas, números asignados y manifiesto. No eliminar ni reiniciar la secuencia para reutilizar etiquetas.

## Validación y límites

- 35 pruebas nuevas aprobadas: invariantes, idempotencia, concurrencia, estados/roles, CSRF, autenticación, lectura sin efectos, manifiesto fijo, colisiones, secuencias 21/41, importación concurrente y repetida de ambos lotes, PDF original y paginación.
- Regresión ejecutada: `test_partner_module_guard` y `test_erp`, 24 pruebas aprobadas.
- JavaScript comprobado con `node --check` para ambos archivos.
- Demo en localhost: QR conserva ficha a través del login; Caja entrega 67.300−36.800=30.500; Socio registra 30.400 y diferencia −100 sin cambiar lo declarado. Persistencia comprobada tras reiniciar servidor. Vista móvil de 390 px sin desborde horizontal del documento. Vista previa y descarga del PDF original comprobadas.
- Los 40 QR de los dos PDF originales fueron decodificados con Vision de macOS y comparados con sus manifiestos, cuatro por página y sin UUID repetidos entre lotes. La descarga del navegador del primer lote fue idéntica al PDF original. La conservación de bytes de ambos originales está probada automáticamente.

La conciliación con Toteat, movimientos posteriores al conteo, recepción/custodia en caja fuerte, pagos en efectivo a proveedores, saldos de arrastre y dashboard financiero quedan fuera de este mínimo. El PDF impreso tiene espacio para ajustes manuscritos posteriores; la ficha de esta fase usa C=A−B y no modela esos ajustes. No registrar allí un cierre con ajuste posterior como si el cálculo lo incluyera: su tratamiento requiere la siguiente fase.

Pendientes antes de publicar: combinar los pequeños puntos compartidos con Documentos, hacer comprobación de colisiones en la base real, importar ambos manifiestos y autorizar/desplegar la versión coordinada. No se comprobó la base real para este desarrollo ni se hicieron más intentos SSH o consultas Toteat.
