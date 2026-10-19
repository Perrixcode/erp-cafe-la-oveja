# Proveedores · etapa B: cuenta corriente local

Fecha: 2026-10-14. Código de partida: `private/release`, rama `feat/delivery-fees`, HEAD `8f5f0c6add611ebe7ea57c27e46705c5371d3684`, con trabajo local previo y etapa 1 conservados. Este documento describe código local y pruebas ficticias; no acredita un despliegue.

## Comportamiento disponible

- Un pago puede aplicarse total o parcialmente a varias facturas del mismo proveedor. El importe no aplicado queda como anticipo/crédito disponible y puede distribuirse después.
- Una nota de crédito monetaria tiene folio, fecha, factura de origen y aplicaciones. Puede dejar crédito disponible si la factura original ya fue pagada. No registra devoluciones físicas ni modifica stock.
- Cada aplicación puede revertirse con motivo. Un pago o NC puede anularse con motivo, restituyendo sus aplicaciones de forma atómica y manteniendo documentos/auditoría. La anulación del registro no revierte una transferencia bancaria ni un documento tributario.
- La cartola muestra cargos, abonos, aplicaciones y anulaciones en orden cronológico, con filtros por fecha/tipo y exportación CSV consistente. Los códigos CC identifican registros nuevos; PH identifica pagos históricos. Las anulaciones nuevas conservan su autor además del motivo. Las aplicaciones son informativas: no descuentan nuevamente el saldo neto.
- Directorio, detalle de factura, filtros, historial, dashboard y exportaciones muestran los saldos actualizados. Los pagos históricos siguen disponibles en el detalle de factura.

En la interfaz: Compras y proveedores → Proveedores → Cuenta corriente. Desde allí se registran pagos/anticipos y NC, se distribuyen créditos, se adjuntan PDF/JPG/PNG, se revierten aplicaciones y se anulan registros. Desde cada factura también se abre la cuenta corriente.

## Modelo y compatibilidad

`erp/schema.sql` agrega exclusivamente tres tablas e índices con `IF NOT EXISTS`:

| Tabla | Contenido |
| --- | --- |
| `ap_account_entries` | Pagos/anticipos y notas de crédito monetarias; actor, fecha, versión, estado y motivo de anulación. |
| `ap_account_applications` | Importes aplicados a facturas; actor, fecha y anulaciones conservadas. |
| `ap_account_files` | Documentos adjuntos vinculados al registro, contenido, SHA-256 y metadatos. |

No se copian ni transforman los pagos anteriores de `ap_payments`. Se leen junto con los nuevos registros; sus IDs, importes, fechas conocidas/desconocidas, comprobantes y auditoría permanecen intactos. Tampoco se modifican los archivos, revisión, procedencia o vínculos existentes del CSV SII. El alta de tablas se realiza al inicializar `Store`, como el resto del esquema. Se ensayó repetidamente solo en SQLite temporal; no se inició la aplicación contra una base operativa.

Fórmulas por proveedor:

```
saldo factura = total − pagos históricos vigentes − aplicaciones de pagos − aplicaciones de NC
crédito disponible = importe vigente del pago/NC − aplicaciones vigentes
saldo neto = suma de saldos de facturas validadas − créditos disponibles
```

Un saldo neto negativo representa saldo a favor. Los saldos pendientes por factura siguen alimentando vencimientos y programación: un anticipo sin aplicar no se asigna automáticamente a una factura. El neto global del dashboard es consolidado; no autoriza compensar saldos entre proveedores.

Los importes se validan como enteros CLP, sin flotantes. Se bloquea aplicar sobre saldos disponibles o pendientes, cruzar proveedores, usar facturas anuladas o pendientes de validación, y emitir NC acumuladas por encima del total de su factura de origen. La corrección/anulación de una factura respeta pagos y NC vigentes.

`paid` conserva el significado de dinero pagado; `credited` distingue las NC aplicadas. El estado `settled` identifica facturas saldadas con NC. Los reportes cuentan cada pago por su importe original una vez, incluidos anticipos; las NC no se suman a pagos. El CSV de facturas agrega al final `notas_credito_aplicadas_clp`. El CSV de pagos conserva sus columnas y agrega los registros nuevos con ID `cuenta-<id>`, dejando factura/folio vacíos porque pueden abarcar varios documentos. Los consumidores que exijan IDs numéricos deberán adaptar ese campo antes de usar los registros nuevos.

## Evidencia técnica

| Archivo / función | Responsabilidad |
| --- | --- |
| `erp/supplier_account.py`: `mutate`, `validate_applications`, `apply_items` | Altas, distribución, restricciones, versiones y anulaciones. |
| `erp/supplier_account.py`: `entries`, `statement`, `attach`, `handle` | Cartola, disponibilidad, documentos y HTTP. |
| `erp/payables.py`: `paid`, `credited`, `settled`, `touch`, `mutate`, `view`, `message` | Integración con pagos históricos, facturas, programación y listados existentes. |
| `erp/payable_duplicates.py`: `DuplicateIndex` | Identidades históricas de etapa 1 conservadas; estado incorpora NC. |
| `erp/sii_purchases.py`: `classify` | Saldo de coincidencias SII incorpora NC sin cambiar el flujo de importación. |
| `erp/payable_reports.py`: `report`, `export_csv` | Reportes y CSV sin duplicar pagos por aplicaciones. Ranking mantiene `supplier_id`. |
| `static/supplier-account.js` | Cartola y formularios; control de sesión, reintento idempotente, versiones y adjuntos. |
| `static/payables.js`, `static/index.html`, `app.py` | Accesos desde Proveedores/facturas, indicadores, etiquetas y carga del archivo JS. |

Rutas nuevas, bajo el control de socio ya existente en `/api/suppliers/`:

```
GET  /api/suppliers/accounts/{supplier_id}
GET  /api/suppliers/accounts/{supplier_id}/export.csv
GET  /api/suppliers/account-files/{file_id}
POST /api/suppliers/account-payments
POST /api/suppliers/account-credit-notes
POST /api/suppliers/account-entries/{entry_id}/apply
POST /api/suppliers/account-entries/{entry_id}/void
POST /api/suppliers/account-applications/{application_id}/void
POST /api/suppliers/account-entries/{entry_id}/files
```

Las mutaciones financieras reutilizan `payables.execute`: transacción `BEGIN IMMEDIATE`, `request_id` con firma de contenido/actor, control de versiones y rollback conjunto. Reintentar la misma solicitud devuelve el resultado guardado; reutilizar su ID para otro contenido se rechaza. El actor proviene de la sesión, no del JSON. Los adjuntos se deduplican por SHA-256 dentro del registro, se verifican al descargar y mantienen el límite existente de 8 MB.

No se modificaron autenticación, roles, política de origen ni controles de datos bancarios. Se probaron 401 sin sesión, 403 para cajero y origen externo, actor autenticado pese a JSON adulterado, comprobantes protegidos y rechazo de contenido corrupto.

## Validación ejecutada

Checkpoint aislado: `/ruta/local/proyecto`.

- Suite de Proveedores: **138 pruebas aprobadas en 17.054 s**: 109 anteriores (incluidas las 12 de etapa 1) y 29 nuevas. Evidencia: `tests-final.log`.
- `tests/test_supplier_account.py`: 25 casos de pagos múltiples/parciales, anticipos, NC, aplicaciones/anulaciones, límites CLP, sobreaplicación, versiones, concurrencia, idempotencia, migración repetida, comprobantes, SII y compatibilidad de programación/reportes. Incluye día de negocio chileno para eventos UTC.
- `tests/test_supplier_account_http.py`: 4 casos de permisos/origen, actor, rutas de pago/aplicación/NC/anulación, lectura de cartola y comprobantes/integridad.
- `node --check` aprobado para `static/payables.js` y `static/supplier-account.js`.
- QA visual en Chrome contra `127.0.0.1` y una base temporal desechable: pago de $5.000 distribuido en $2.000 y $3.000; aplicación de anticipo de $2.000 conservando neto de $9.000; NC de $1.500 bajando neto a $7.500; reversión de aplicación conservando neto; anulación de NC recuperando neto de $9.000; vínculo a detalle de factura y dashboard con $7.000 de pagos contados una vez.
- Capturas ficticias: `qa/account-desktop.jpg`, `qa/account-mobile.jpg` (390×844), `qa/account-void-desktop.jpg`, `qa/dashboard-desktop.jpg`. Revisión final adicional de filtros e identificadores en escritorio y móvil: pagos del 2 al 6 de octubre muestran dos movimientos por $7.000 y saldo final de $7.500 incluyendo la NC; enlace CSV conserva los mismos filtros, foco de teclado visible. Evidencia: `qa/account-filter-desktop.jpg` y `qa/account-filter-mobile.jpg`. Sin errores de consola capturados. Navegador de QA cerrado y servidor temporal detenido.

`run_isolated_tests.py` y `serve_visual_qa.py` bloquean bases fuera del directorio temporal y conexiones que no sean loopback. No se leyó una base real, no se hicieron operaciones bancarias, no se diagnosticó Toteat y no se ejecutó un despliegue. El parche y sus hashes quedan en el checkpoint; la aplicación comprueba que todas las fuentes del checkpoint siguen sin cambios concurrentes antes de escribir.

## Límites de esta entrega

- Es el núcleo local de B. A (extracción desde foto/PDF), C (propuesta semanal), D (pedido/recepción/bodega), E y F no se implementan aquí. El CSV SII sigue existente; las NC se registran manualmente y no se importan automáticamente desde SII.
- La cartola usa los valores vigentes de las facturas. No reconstruye un cierre contable histórico antes de cada corrección; esas correcciones permanecen en el historial. Los antecedentes sin fecha se muestran como tales.
- La identidad proveedor/folio de una NC queda reservada incluso tras anularla, coherente con conservar evidencia; esta entrega no incorpora edición/restauración de NC. Pagos duplicados enviados como operaciones diferentes no se detectan automáticamente por referencia; se exige revisión del usuario. La idempotencia cubre reintentos de una misma solicitud.
- Los pagos históricos mantienen su flujo de una factura. Las aplicaciones múltiples se usan para los registros nuevos. No hay devoluciones de dinero al proveedor/desde el proveedor ni conciliación bancaria automática.
- No hay movimientos de stock por NC. Al revertir una aplicación se recupera saldo, sin reconstruir automáticamente la programación anterior de pagos.
- No se validó volumen de producción ni consumidores externos de los CSV. Antes de uso real falta revisar funcionalmente, respaldar la base y ejecutar el proceso de despliegue/migración autorizado. Después de registrar operaciones nuevas, volver al código anterior ocultaría sus efectos sobre los saldos: no es un rollback de datos válido.


## Brechas delimitadas antes de C

| Brecha | Límite y tratamiento actual |
| --- | --- |
| Duplicados financieros entre solicitudes diferentes | La idempotencia evita repetir una solicitud; no demuestra que dos pagos declarados sean operaciones bancarias distintas. Revisar fecha, importe, medio, referencia y comprobante con los códigos CC/PH antes de registrar. El duplicado puede anularse con motivo y trazabilidad. Sigue pendiente un flujo de advertencia/revisión de similitudes, incluyendo pagos históricos. |
| Cartola vigente frente a cierre histórico | Los filtros y el CSV mantienen el saldo completo y explican movimientos ocultos. Usan valores vigentes de documentos y no reconstruyen cierres previos a correcciones. Un cierre histórico requerirá versionar estados contables por fecha y definir política de períodos; no se presenta como disponible. |
| NC anuladas con folio reservado | Se conserva la identidad para evitar recreaciones silenciosas. Falta un flujo explícito y auditable de corrección/restauración; no se debe cambiar el folio real para sortear esta restricción. |
| Volumen | Las pruebas funcionales no acreditan escala productiva. La vista lee el conjunto de facturas/aplicaciones; no tiene paginación del lado del servidor. Antes de homologación productiva se requiere prueba de carga representativa y definir umbrales/paginación. |

B queda como núcleo funcional local validado con estas brechas explícitas; no equivale a conciliación bancaria, cierre contable histórico ni homologación productiva. C puede consumir saldos actuales y créditos, manteniendo revisión humana y exclusiones conservadoras.

## Requisito posterior: directorio operativo

El coordinador reporta revisión de la fuente Google Sheets `1JRftwRdeb7GfnI9AEJrC5a-LtYAgYxKQXHfSiLtV5cM`, gid `906827505`, pestaña `Proveedores + Dia de pedidos`, 35 proveedores en filas 2–36. Contiene productos, pedidos, hora límite, entregas, contacto, canal, condición/plazo de pago y sustitutos. No se importa en B/C. Mínimos no informados y observaciones vacías; algunos horarios pedido→entrega requieren aclaración. La zona técnica America/Los_Angeles no determina la zona operativa.

Se reutilizarán nombre, RUT, contacto, teléfono/email y condiciones de pago existentes. Catálogo abastecido, días/horas límite, ventanas/plazos de entrega, mínimos y sustitutos requieren un modelo estructurado y reglas de conciliación de identidad. Servirá de base para recordatorios y compras sugeridas; queda para una etapa posterior, sin inventar datos.
