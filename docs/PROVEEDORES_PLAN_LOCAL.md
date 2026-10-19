# Proveedores: cierre del plan local A–F, panel y directorio

14 de octubre de 2026. Código sobre `feat/delivery-fees`, HEAD
`8f5f0c6add611ebe7ea57c27e46705c5371d3684`. Este informe describe el código
local y pruebas con datos ficticios; no acredita despliegue ni uso operativo.
Las notas de etapas 1, B y C conservan sus conclusiones históricas. Este cierre
reemplaza sus afirmaciones de que A, D, E, F y el directorio seguían pendientes.

## Funciones disponibles

| Área | Resultado en código local | Límites y revisión necesaria |
| --- | --- | --- |
| A · Foto/PDF | Carga PDF/JPG/PNG, extracción local, sugerencias conservadoras, cotejo explícito, original adjunto, reutilización por SHA-256 y enlace a factura existente. | No registra deuda al cargar. No interpreta líneas ni certifica autenticidad. Conserva revisión manual si falla OCR. CSV SII permanece disponible. |
| B · Cuenta corriente | Pagos parciales, múltiples facturas por pago, anticipos, NC monetarias, aplicaciones, reversas, saldos e historial. | Registro administrativo; no conciliación bancaria ni transferencias. Los duplicados de transferencias reales con distintas solicitudes requieren revisión. |
| C · Propuesta semanal | Semana y presupuesto revisables, pagos ya registrados, reservas vigentes, prioridades, exclusiones, pausas y revisión guardada. | Programación manual confirmada; no tarea automática, techo central de caja ni ejecución de pagos. |
| D · Compras y bodega | Pedido local, recepción parcial, rechazo, incidencias, factura vinculada, cancelación pendiente, reversa y kardex propio de insumos. | No envía pedidos. Recibir no crea deuda. Stock aceptado se registra una sola vez; ventas/recetas/Toteat no se deducen. |
| E · Precios | Líneas de factura revisadas contra pedido, cantidades normalizadas, conciliación pedido–recepción–factura, histórico y último precio válido por proveedor. | Comparación separada por base neta/bruta. Equivalencias explícitas por insumo; no reparte impuestos, descuentos o fletes automáticamente. |
| F · Compras sugeridas | Stock físico verificado, pedidos pendientes, consumo diario revisado, plazo, mínimo y objetivo; explicación y redondeo al formato de compra. | Consumo explícito, no inferido. Bloquea datos insuficientes o plazos dudosos. Preparar pedido abre revisión; no lo guarda automáticamente. |
| Panel | Vencimientos, saldos por validar, pausas, documentos por revisar, pedidos atrasados, incidencias, conciliaciones, stock sin conteo, sugerencias y fichas pendientes. | Estado local actual; cada alerta enlaza con su área. Dashboard financiero anterior conservado. |
| Directorio | Lista buscable y filtro de revisión; nombre abre ficha integrada lateral o móvil. Contacto/vendedor, teléfono, canal, enlace, banco/cuenta/titular/RUT, productos, horarios, plazos, mínimos y sustitutos. | Datos existentes reutilizados. No inventa RUT ni cuentas. Días de pedido y entrega independientes, sin avisos automáticos. |

La planilla «Proveedores + Día de pedidos» de 35 proveedores sigue pendiente
de cotejo e incorporación de datos. Se implementó previsualización de filas
normalizadas sin escritura automática. Este cierre no importó esa planilla ni
revisó cuentas bancarias reales.

## Flujos e integridad

`erp/procurement.py` concentra `execute`, `mutate`, `overview`, recepción y
reversas. Cantidades enteras en millonésimas y precisión declarada por unidad;
las equivalencias por insumo se conservan en cada línea. No se adivina cuántos
kg contiene una caja. Guía/referencia duplicada se bloquea entre pedidos del
mismo proveedor; una corrección conserva la recepción reemplazada. Dividir
una guía entre pedidos exige distinguir explícitamente las referencias.

`erp/supply_planning.py` implementa `position`, `mutate` y `overview`,
con conciliación, comparación de precios, sugerencias y movimientos. Antes de salidas, mermas, ajustes o traslados exige
un conteo físico revisado. Un conteo de cero es válido; un conteo sin diferencia
también queda en historial. El conteo establece saldo físico, no suma otra
vez las recepciones. Traslados son dos movimientos atómicos. Reversas no borran
historial y se bloquean si invalidan un conteo posterior o crean stock negativo.

Precios de facturas anuladas, desvinculadas o con datos financieros cambiados
se excluyen hasta revisar de nuevo. Registrar un pago no invalida por sí solo
el precio. La suma de líneas no puede superar el total de factura. La diferencia
entre cantidad recibida y facturada queda visible; registrar precio no mueve
stock ni modifica el total financiero.

`erp/supplier_directory.py` conserva dos versiones (proveedor y ficha), motivo,
actor y valores anteriores/nuevos. `erp/document_intake.py` conserva original,
texto y revisión. Un archivo no se vincula a dos facturas distintas; ante un
conflicto, creación y adjunto revierten en la misma transacción. Duplicidad de
factura continúa usando la validación existente de proveedor/tipo/folio.

Todas las rutas nuevas usan `/api/suppliers/`, sesión de socio y controles
existentes de origen. El actor proviene de la sesión. Escrituras con versión,
UUID de solicitud y `BEGIN IMMEDIATE`; reintentos idénticos recuperan resultado,
UUID reutilizado con otro contenido/actor se rechaza. Las vistas son de solo
lectura. Las nuevas tablas son aditivas; su creación idempotente solo se probó
en SQLite temporal, nunca en datos operativos.

## Lectura local de documentos

En macOS, `erp/local_document_ocr.swift` usa PDFKit y Vision del sistema,
respetando orientación de imágenes. Requiere Swift disponible y acceso a los
servicios locales de Vision; un sandbox que los bloquee impide reconocer fotos.
En otros sistemas usa `pdftotext` para PDF o `tesseract` para imágenes si están
instalados. No instala herramientas ni envía documentos a servicios externos.

Límites: archivo 8 MB, PDF nativo hasta 20 páginas, imagen hasta 40 megapíxeles,
lectura con plazo de 90 segundos y texto hasta 200.000 bytes UTF-8. El lector
alternativo de PDF procesa las primeras 20 páginas y lo advierte. Archivos
ilegibles, protegidos o herramientas ausentes conservan revisión manual.
Montos ambiguos, formatos monetarios dudosos y fechas inválidas no se rellenan.
Siempre se cotejan proveedor, tipo, folio, emisión, vencimiento y total.

## Evidencia y aplicación

Checkpoint: `proveedores-etapaD-20261014T162745Z`, dentro de la carpeta de esta
tarea Codex. Incluye `before/`, copia validada `work/`, manifiestos SHA-256,
parche, pruebas y capturas en `qa/`. Solo `application-check.json` confirma que
el parche llegó al repositorio. La aplicación exige mismo HEAD y rama, compara
los 87 archivos originales y comprueba que los archivos aplicados coincidan
exactamente con los probados. No hace commit, stage, reset ni despliegue.

El arnés de pruebas bloquea SQLite fuera del directorio temporal y red distinta
de localhost. El servidor visual muestra «ENTORNO FICTICIO DE PRUEBA - Sin datos
operativos». No se cargaron proveedores, facturas, cuentas o documentos reales.
No se ejecutaron compras, pagos ni migraciones operativas.

## Verificación final

- Suite completa: **239 pruebas aprobadas**, `tests-final.log`.
  Incluye permisos HTTP, origen, actor de sesión, duplicados, reintentos,
  versiones, atomicidad, reversas, conteos, conciliación, precios, reposición,
  directorio y carga/revisión de originales; conserva pruebas previas de CSV,
  cuenta corriente y propuesta semanal.
- Sintaxis de los seis archivos JavaScript y seis módulos Python modificados
  o nuevos: `syntax-final.json`. Los tests ejecutan los restantes módulos.
- OCR nativo final: PDF ficticio folio 801 / $12.000 e imagen PNG ficticia
  folio 802 / $15.000, ambos con emisión 12/10/2026, en
  `qa/ocr-native-final.json`. No se usó un sustituto del OCR para esta evidencia.
- QA visual: escritorio 1365×900 y móvil 390×844. Imagen cargada y cotejada
  desde formulario, factura creada con original, directorio buscable/ficha,
  recepción, conteo sin diferencia, conciliación a $40/kg, propuesta de
  18 sacos y preparación/confirmación explícita de pedido local. Sin errores
  de consola. Detalle y capturas en `qa/final-check.json`.
- Se registró previamente una recepción parcial 3 aceptados/1 rechazado,
  seguida de 7 aceptados: final 10 recibidos, 0 pendientes y rechazo histórico
  conservado. La captura de esa prueba móvil se conserva separada de las
  capturas finales con aviso visible de entorno ficticio.

## Antes de activar en operación

La activación operativa requiere otra autorización: respaldo, ensayo de esquema
y recuperación con una copia autorizada, revisión de permisos/configuración del
servidor, carga cotejada del directorio, catálogo y equivalencias reales, conteos
físicos iniciales y políticas de reposición revisadas. También falta medir
volumen/paginación y retención de adjuntos/auditoría con datos representativos.
El OCR se valida en el sistema donde se vaya a ejecutar; no se presume que el
servidor desplegado tenga las herramientas del Mac. Kardex Toteat sigue fuera
del alcance. La etapa visual posterior y el indicador compartido de carga se documentan
en `PROVEEDORES_INTERFAZ.md`, separados de este cierre funcional.
