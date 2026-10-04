# ERP Café La Oveja

ERP modular para Oveja Cocina y Café. El primer flujo operativo gestiona **tortas y dulces enteros**, recepción desde Toteat, agendamiento, marcado y entrega. Incluye Inicio y consulta de transferencias del bot Ovejita. Python, SQLite y HTML/CSS/JavaScript nativo; Gunicorn y Caddy para producción.

**La distribución pública incluye solo datos ficticios. La instalación privada puede leer Toteat: comandas abiertas → Lectura de Toteat; ventas cerradas y saldadas con comentario válido → Agendadas. No escribe pedidos, pagos ni stock en Toteat. La boleta PDF se adjunta cuando llega; no es requisito para agendar. Todo acceso a datos requiere una cuenta autenticada. Los perfiles Socio, Producción y Caja se validan en el servidor; los cambios nuevos registran el usuario real. La demo local usa localhost; la instalación en servidor usa HTTPS y servicios aislados.**

## Interfaz

Dirección visual A: marfil, superficies blancas y verde como acción principal. El tema se centraliza en `static/theme.css`; la agenda usa tabla en escritorio y tarjetas en móvil, con fecha y estados textuales visibles. Noto Sans se sirve localmente, sin petición a Google Fonts. La fuente conserva su licencia propia en `static/fonts/OFL.txt`; esa licencia no se aplica al código del proyecto.

## Ejecutar la demo

En la instalación con catálogo privado, pulsa **Simular pedido**. Selecciona Programada, un producto y confirma el pago ficticio. En el pedido, **Solicitar marcado** crea el aviso de la campana; **Activar sonido** habilita una campanita con resonancia de unos dos segundos para nuevas solicitudes en esa pestaña. Abrir el aviso no marca físicamente la torta. Inmediata se guarda entregada.

Las simulaciones llevan etiqueta **PRUEBA** y vistas separadas de la operación; no alteran stock ni indicadores operativos. **Retirar prueba** la conserva en **Pruebas retiradas**, desde donde se puede restaurar. Tras la limpieza del catálogo privado no se vuelven a cargar ejemplos iniciales.

Para dejar el servidor local separado de la terminal, sin instalar un servicio:

```sh
python3 scripts/start_local.py
```

El lector consulta abiertas cada 30 segundos y ventas del turno verificado cada 90 segundos. Lectura muestra solo las pendientes; al agendar se conserva una única identidad con comentario original, pago y boleta. No se interpreta la desaparición de una comanda como pago. Los casos sin nombre, fecha u hora válidos requieren revisión; el teléfono ausente se señala. Ver [estado y límites de la conexión](docs/TOTEAT_READONLY.md#estado-vigente).

Las tarjetas muestran **fecha y hora** en cualquier período. Las pruebas del rango tienen un acceso directo desde Operación a Simulaciones y siguen excluidas de métricas reales. La vista y el período se conservan en la URL al recargar.

Python 3.11+ y zona `America/Santiago` disponible:

```sh
python3 app.py
```

Abrir <http://127.0.0.1:8765>. Detener con `Ctrl+C`. Para experimentar sin alterar la base actual:

```sh
python3 app.py --port 8766 --db data/otra-demo.sqlite3
python3 app.py --empty --db data/vacia-demo.sqlite3
```

Los ejemplos se cargan una sola vez y conservan su fecha. Selecciona esa fecha o pulsa «Ver día de los ejemplos» cuando corresponda. SQLite guarda pedidos, versiones, historial, conteos y recetas; está excluido de Git. El servidor escucha exclusivamente en `127.0.0.1`; un teléfono externo no puede acceder todavía. No exponer este servidor de desarrollo a Internet.

## Acceso, módulos y recuperación

Crear nuevas cuentas personales desde Terminal, sin contraseñas en argumentos:

```sh
python3 scripts/create_user.py --role partner
python3 scripts/create_user.py --role production
python3 scripts/create_user.py --role cashier
```

No se incluyen cuentas iniciales ni claves predeterminadas. `create_partner.py` continúa disponible para socios. Cerrar sesión limpia los datos de la pantalla y revoca el token; reiniciar el servidor invalida las sesiones. La API devuelve 401 sin sesión y 403 cuando el perfil no permite la operación. La página de acceso, sus recursos y un estado mínimo de salud son públicos, sin información comercial.

| Perfil | Operaciones habilitadas |
| --- | --- |
| Socio | Consulta, boletas, marcado, entrega, correcciones, inventario local, recetas, simulaciones, edición de cliente, toma manual y revisión de recepción |
| Producción | Consulta de pedidos/plan, solicitud de marcado y edición de bases |
| Caja | Consulta de pedidos, acceso a boletas y registro de entrega de ítems ya marcados |

**Inicio**, **Pedidos de tortas** y **Transferencias del local** tienen funciones operativas. **Gestión de personas**, **Documentos y procedimientos**, **Gestión de proveedores**, Despacho y reparto, Planificación de turnos, Control de asistencia, Inventario y abastecimiento, Producción, Catálogo y proveedores, Analítica y reportes, Costos y rentabilidad, Gestión de clientes y Activos y mantenimiento contienen estructura para desarrollo posterior. Proveedores contempla preparación semanal de facturas como mensaje para copiar a WhatsApp. Sin GPS, GeoVictoria, Power BI ni envío de mensajes habilitado.

El panel de atención de tortas muestra próximos 7 días, pedidos de fechas anteriores pendientes de entrega, pendientes de marcado, agendados sin pago y contacto/dirección incompletos. Un pedido puede participar en varias prioridades. No afirma que un pedido vencido siga físicamente en el local ni que falte fabricar una torta. Operación, simulaciones y retiradas permanecen separadas. Las tarjetas incluyen día de semana, fecha y hora.

El servidor local crea un respaldo al arrancar y cada 24 h mientras siga activo, con reintento cada 30 min si falla. Incluye SQLite, historial, cuentas (hashes de contraseña), bandeja de recepción y PDF adjuntos; queda privado en `private/backups/`. La copia usa SQLite online backup, verificación de integridad y SHA-256 de archivos. No incluye credenciales de Toteat, llavero, binarios ni configuración de conexión. **Es una copia en el mismo equipo: falta una copia externa cifrada al preparar el hosting.** No hay borrado automático de respaldos.

```sh
python3 scripts/backup_local.py
python3 scripts/backup_local.py --verify private/backups/NOMBRE_DEL_RESPALDO
python3 scripts/backup_local.py --restore private/backups/NOMBRE_DEL_RESPALDO --destination private/recuperacion-nueva
```

La recuperación exige una carpeta nueva y nunca reemplaza una instalación ni activa el lector. La verificación compara SQLite y todos los PDF referenciados antes de dar una copia por terminada. Los respaldos contienen datos privados y hashes de cuentas; no deben publicarse. [Preparación del servidor](docs/PREPARACION_SERVIDOR.md).

## Flujo de marcado

| Estado | Significado |
| --- | --- |
| Pendiente de marcado | Todavía no se solicitó ni confirmó el marcado. No afirma que falte elaborar. |
| Marcado solicitado | La jefa pidió el marcado a Esteban; hay alerta abierta dentro del ERP. |
| Marcado físico | Esteban confirmó que el producto físico está marcado. |
| Entregado | Se registró la entrega y se conserva el historial. |
| Cancelado | Deja de contar en encargadas sin borrar el registro ni anular pagos/ventas. |

«Solicitar marcado» opera sobre **todas las unidades de un ítem**, sin afectar otros ítems del encargo. La demo no divide cantidades parcialmente. «Ya está marcado» resuelve el aviso, sin tocar stock de Toteat. La campana muestra avisos de todas las fechas, con producto, cantidad, retiro y solicitante. Consulta el ERP local cada 15 segundos; no consulta Toteat. El sonido requiere activación expresa y no repite solicitudes vistas al consultar o recargar.

No existe un paso obligatorio de solicitar producción. Las antiguas entradas de ese experimento se mantienen como históricas. Los ítems que seguían en ese estado se devuelven a pendiente mediante una migración idempotente con historial; no se reinterpretan como solicitudes de marcado.

Las correcciones conservan identidad y versiones. Para modificar un ítem avanzado hay que revertirlo a pendiente con motivo. La fuente + ID y el ID de ítem evitan duplicados; una versión desactualizada rechaza cambios concurrentes. El pago simulado es obligatorio, pero un comprobante por sí solo no acredita abono bancario real.

## Rangos y resúmenes

El período usa **fecha programada de retiro/entrega**, en `America/Santiago`, e incluye ambos extremos. Listado, resumen, plan de bases, indicadores y texto para copiar usan el mismo rango. La búsqueda y filtros de estado afectan la lista visible; el resumen y WhatsApp mantienen todo el período.

- Diario: fecha seleccionada.
- Semanal: lunes a domingo.
- Cada dos semanas: bloques continuos de 14 días anclados al lunes 5 de enero de 2026; no una quincena calendario.
- Mensual: primero a último día del mes.
- Personalizado: desde/hasta obligatorios, con inicio ≤ fin. La navegación por flechas queda desactivada en este modo.

«Por marcar» suma pendiente y marcado solicitado. «Encargadas» suma pendiente, marcado solicitado y marcado físico; excluye entregado y cancelado. Cantidades en **productos enteros**, nunca porciones.

## Entrega inmediata y programada

El tipo pertenece al pedido: se mantiene el mismo producto y no se duplica inventario ni categorías.

- **Programada:** requiere nombre ficticio, teléfono ficticio, fecha/hora y pago simulado confirmado; los ítems comienzan pendientes de marcado.
- **Inmediata:** la venta confirma entrega; todos los ítems nacen entregados, con un evento de creación que registra esa decisión. No generan solicitudes de marcado ni bases pendientes.
- **Históricos sin tipo:** muestran «Tipo de entrega por confirmar». La migración añade una tabla separada; no deduce el tipo por fecha, comentario o estado, ni modifica pedidos/historial existentes.

El filtro de entrega cambia las tarjetas visibles; los indicadores, resumen y texto para copiar siguen abarcando todo el período. Las entregas inmediatas no participan en la métrica de puntualidad de las programadas. Corregir el tipo no cambia estados automáticamente; una reclasificación a inmediata requiere ítems ya entregados. Las correcciones y reversiones conservan auditoría.

En Lectura Toteat, el comentario queda abierto por defecto y el cierre manual se respeta durante las actualizaciones de la sesión. Los socios pueden **Pasar a venta inmediata** solo tras recibir la venta cerrada y saldada con comentario vacío o incompleto, con confirmación y motivo: queda en **Historial de entregadas** como **Entregada inmediata**, sin demanda programada. El saldo con descuento se distingue del dinero recibido. La clasificación no modifica Toteat o inventario; un cierre posterior conserva la misma identidad.

Una anulación explícita de Toteat se resalta para revisión: el socio confirma **Anular en ERP** con motivo. Solo entonces cambia los compromisos pendientes; conserva entregas previas, pago e historial. Las pestañas **Anuladas** y **Entrega inmediata** muestran los registros resueltos sin duplicarlos en Lectura.

El lector de ventas cerradas interpreta el comentario siguiendo el afiche de cajeras y conserva el texto original. No infiere entrega inmediata por falta de comentario. Una venta inmediata sigue siendo entrega confirmada en el flujo manual; su clasificación automática por API aún requiere evidencia explícita.

El automático exige cierre y saldo cero con pago registrado, o cierre totalmente saldado con descuento autorizado en Toteat según la regla de los dueños. Un descuento no se etiqueta como dinero recibido. Boleta, marcado y entrega son estados independientes. Manual SOS y agendamiento sin pago están disponibles solo para cuentas de socios autenticadas. [Reglas vigentes](docs/AGENDAMIENTO_Y_SOS.md).

## Edición de cliente y entrega por socios

Un socio autenticado puede corregir nombre, teléfono, retiro/delivery, dirección, fecha y hora desde el ticket. Delivery exige dirección. La modificación conserva productos, pago, boleta y comentario fuente; registra cuenta de socio, motivo y valores anteriores/nuevos. La versión evita sobrescribir un cambio simultáneo. Una sincronización repetida de Toteat no borra estas correcciones. Los resúmenes y avisos usan la nueva fecha. Pedidos entregados o cancelados completos no se editan por esta vía.

Crear cada cuenta desde Terminal, en este equipo (no se incluyen cuentas ni claves iniciales):

```sh
python3 scripts/create_partner.py
```

La contraseña se escribe oculta, mínimo 12 caracteres. Solo se guarda un hash scrypt con sal aleatoria en `private/partner-auth.sqlite3`. La pantalla **ERP Oveja · Cocina y Café** inicia una sesión HttpOnly/SameSite de ocho horas; cerrar sesión o reiniciar el servidor la invalida. Cinco intentos fallidos bloquean temporalmente el acceso. No hay alta de socios vía HTTP. La protección cubre todas las consultas y operaciones del ERP, incluidas boletas, comentarios, historial y URLs directas. Sin cuentas no existe acceso abierto. Las cuentas de socios anteriores conservan sus hashes de contraseña al incorporar perfiles. Los registros históricos mantienen su responsable original.

## Contingencias SOS

**Iniciar sesión como socio → Toma de pedido manual.** Un ingreso sin pago se guarda fuera de producción hasta que un socio autorice **Forzar agendamiento** con motivo. La autorización no confirma dinero, boleta, marcado ni entrega. Un pago verificado manualmente exige referencia y se etiqueta como declaración del socio.

Una comanda recibida puede vincularse con identidad y cantidades verificadas; su llegada posterior conserva el mismo pedido y los datos locales. Las coincidencias ambiguas se revisan antes de agregar demanda. Auditoría, idempotencia, control de versión y protección ante llegadas simultáneas evitan duplicaciones por reintento. [Flujo, conciliación y límites](docs/AGENDAMIENTO_Y_SOS.md).

## Catálogo, bases y perfiles demo

El catálogo de `erp/catalog.py` es ficticio, con SKU `DEMO-`. El importador local admite un archivo privado previamente revisado, limitado a **Tortas enteras** y **Dulces enteros**, con identidades fuente verificadas. No se importará el resto de la carta ni se usará coincidencia parcial de nombres como autorización. SKU desconocidos, trozos y porciones se rechazan; no se adivina que un artículo ambiguo sea entero.

Los bizcochos tienen formatos de 10 y 20 personas. Hojarasca, mixta y zanahoria solo 20. Cheesecake, pie y kuchen demo tienen formato entero propio, sin imponer 10/20 personas ni inventar receta.

Valores iniciales editables indicados para 20 personas: Amor hojarasca, 10 hojarascas (provisional); Hoja manjar, 14; Mixta, 0,5 bizcocho chocolate + 6 hojarascas. Chocolate de bizcocho tiene 1 base del tamaño correspondiente. Los sabores de base no confirmados muestran «Receta/cantidad pendiente». Rellenos pendientes.

El usuario elige **Todas las encargadas** en **Plan de tortas y bases**. Primero ve cantidades por sabor y tamaño, incluidas las tortas con receta por definir; después, las bases calculables. Es demanda bruta de los encargos pendientes de entrega: incluye los marcados una sola vez, excluye entregados/cancelados y no afirma cuánto falta fabricar. Stock y bases ya elaboradas no se descuentan.

Cada componente de receta admite una unidad explícita (bizcocho entero, disco, hojarasca o unidad) y diámetro en centímetros, ambos opcionales. No se convierten personas en centímetros ni bizcochos en discos. Solo se agrupan formatos confirmados compatibles; los antiguos o incompletos conservan por separado su tamaño comercial y muestran «por confirmar». Las recetas desconocidas no aportan bases inventadas. Se conservan fracciones exactas, sin redondeo automático.

Se retiraron el selector de perfil y el responsable escrito a mano. Los permisos corresponden a la cuenta autenticada: socios administran, producción consulta/solicita marcado/edita bases y caja consulta/ve boletas/registra entregas. La confirmación de marcado, cancelaciones, reversiones, cambios de cliente y SOS quedan en socios. Consultar un borrador SOS también requiere socio.

## Stock y reserva para vitrina

`**` significa **enteros que Esteban decide reservar para trozar y mantener vitrina**. Nunca representa inventario de trozos. Puede decidir 0, 1 o X; no hay una regla automática de reservar la última unidad.

```text
Disponible para venta entera = físico entero − reserva vitrina
```

Los encargos no se restan nuevamente. Si el físico es desconocido, el disponible sigue desconocido aunque se declare una reserva. El conteo es **actual e independiente del período de pedidos**; el total solo cubre sabores registrados. Conteos y reservas guardan motivo, responsable, fecha y versión, sin escribir inventario real.

## Indicadores y boletas

Pedidos y unidades se presentan separados. El número de pedidos incluye finalizados/cancelados; unidades excluye canceladas e incluye entregadas. Se desglosa por canal y por producto/formato en el resumen. Solicitud→marcado muestra promedio de confirmaciones registradas sobre pedidos del rango. A tiempo usa ítems actualmente entregados con evento de entrega y compara su timestamp con retiro programado; no es porcentaje de ventas. Sin eventos muestra «Sin datos». Todo son métricas de demostración, sin ingresos ni márgenes inventados.

El ticket permite ver y descargar la boleta PDF cuando el detalle de la orden entrega un enlace vinculado al mismo pago e importes de la venta. El PDF validado se guarda privado y se sirve desde localhost; nunca se publica el enlace original ni se envía al cliente automáticamente. La falta de boleta no bloquea un cierre saldado con descuento.

## Diagnóstico manual de Toteat

Herramienta histórica separada para instalaciones nuevas; la conexión privada ya instalada no necesita repetir el ingreso de credenciales:

```sh
python3 scripts/toteat_diagnostic.py
```

Primera opción: `1`, catálogo `products` activos. Solicita IDs y token mediante entrada oculta; el usuario escribe `CONSULTAR` para ejecutar una sola petición GET. La credencial y URL autenticada viven solo en memoria. No hay archivos, logs, importación a SQLite, reintentos ni redirecciones. Se imprime esquema acotado con nombres seguros de campos, tipos, tamaños de arrays y flags booleanos conocidos, sin valores comerciales ni nombres de campos dinámicos. Un flag de éxito no acredita que el catálogo ya esté validado. El proveedor recibe autenticación por query HTTPS según su configuración; puede registrar esa URL en su infraestructura.

El diagnóstico conserva TLS y validación de hostname. En macOS, si falta el bundle CA de Python.org, usa el bundle público del sistema `/etc/ssl/cert.pem`. Errores clasificados como TLS/DNS/timeout no muestran URL, token ni respuesta. Nunca pegar credenciales en chat o en el repositorio. Ver [guía de lectura](docs/TOTEAT_READONLY.md).

## Vista previa local del catálogo real

El siguiente paso, separado de la demo, es:

```sh
python3 scripts/toteat_catalog_preview.py
```

El usuario ingresa los identificadores, el token oculto y `CONSULTAR`. Recibe candidatos de **Tortas enteras** y **Dulces enteros** con los IDs y nombres realmente presentes en la respuesta; selecciona los números y confirma `VER`. La terminal muestra productos de esas categorías con `id`, `idToteat`, código local, nombre y cantidad de opciones, junto con un informe de excluidos. Es un filtro local: no se inventa un parámetro de categoría para Toteat. La coincidencia fuente usa el nombre completo confirmado `TORTAS ENTERAS - ¡Sin opción de escritura!` y `DULCES ENTEROS`; «Sin opción de escritura» es texto comercial, no un permiso API. Los IDs se obtienen de cada respuesta.

Solo lee una vez, requiere `ok=true` y valida todas las filas. Excluye otras categorías, modificadores, trozos, formatos ambiguos y conflictos de identidad; colapsa duplicados exactos. La selección de categorías no se guarda. No importa a SQLite ni modifica Toteat. Los valores comerciales aparecen solo en la terminal del usuario; no se guardan archivos, precios, imágenes, descripciones ni credenciales. [Mapeo confirmado y límites](docs/TOTEAT_READONLY.md#vista-previa-del-catálogo).

## Incorporación manual de un catálogo privado

El archivo debe seguir el esquema ilustrado en `fixtures/catalog_import_demo.json`, cuyos productos e identificadores son inventados. No se deben copiar valores reales a ese fixture ni a código, documentación, capturas públicas o commits.

```sh
python3 scripts/import_catalog.py private/catalog-confirmado.json
python3 scripts/import_catalog.py private/catalog-confirmado.json --apply --actor "Operador local"
```

La primera llamada valida únicamente el archivo. `--apply` ejecuta una transacción aditiva en `data/erp-demo.sqlite3`; respalda antes esa base. Tanto `private/` como los archivos SQLite están excluidos de Git. El script no tiene red, no solicita credenciales y no invoca el diagnóstico.

Conserva `id`, `idToteat`, `localCode`, nombre, categoría y su ID exactos, más procedencia y clasificación explícita. La clave `LOCAL-…` es interna al ERP, no un SKU inventado para Toteat. Los productos demo permanecen para mantener sus referencias históricas. Se rechazan identidades duplicadas, categorías incompatibles, colisiones de producto/formato, porciones y campos fuera de alcance.

Repetir un lote idéntico, incluso reordenado, no duplica productos ni auditoría. Un dato diferente para una identidad ya incorporada cancela el lote completo: este importador no sobrescribe ni elimina registros. Las ediciones locales de recetas tienen historial separado y sobreviven a la repetición del catálogo.

La incorporación no crea stock, reservas, precios, pedidos ni producción. Sin conteo, disponibilidad **desconocida**; un cero solo aparece cuando se registra explícitamente. Dulces enteros sin tamaño confirmado no reciben un número de personas inventado. Las bases desconocidas quedan pendientes; las cantidades provisionales se indican como tales.

## Arquitectura y verificación

```text
UI → HTTP local → reglas/validación → SQLite + historial transaccional
Diagnóstico manual → GET HTTPS Toteat → solo forma de respuesta (sin importar)
```

| Archivo | Responsabilidad |
| --- | --- |
| `app.py` | API local, estáticos y protección de Host/origen |
| `erp/store.py`, `erp/schema.sql` | Persistencia, versiones, auditoría y migración demo |
| `erp/domain.py` | Rangos, estados, resúmenes y texto para copiar |
| `erp/catalog.py`, `erp/catalog_import.py` | Catálogo demo, importación local validada y bases con aritmética decimal |
| `scripts/import_catalog.py` | Validación y aplicación manual desde un archivo privado, sin red |
| `static/` | Interfaz adaptable a escritorio y móvil |
| `scripts/toteat_diagnostic.py` | Diagnóstico manual efímero; no lo invoca la UI |
| `tests/` | Negocio, HTTP y transporte simulado de diagnóstico |

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py erp scripts tests
node --check static/app.js
```

Node solo comprueba sintaxis; no se necesita para ejecutar. Las pruebas HTTP usan localhost y bases temporales. Evidencia visual y resultados en [CHECKPOINT](docs/CHECKPOINT.md).

## Portafolio y siguientes pasos

Proyecto personal de Esteban Iturra, desarrollado con asistencia técnica, revisión y aprendizaje incremental. No se incluyen clientes, credenciales, bases de datos ni catálogo comercial real. Sin licencia elegida todavía.

Identidad negro/blanco inspirada en [Café La Oveja](https://cafelaoveja.cl); crema, oliva y caramelo son interpretación del ERP. Tipografía del sistema; sin fotos ni archivos del logo oficial.

Estado actual: recepción con agendamiento automático o autorización de socio; datos incompletos en revisión; venta inmediata separada; alertas de anulación con historial; boletas; acceso integral; Inicio; lectura de transferencias; fotos nuevas del bot con vista previa y descarga. [Arquitectura y tecnologías](docs/ARQUITECTURA.md), [despliegue](docs/PREPARACION_SERVIDOR.md).

Guías: [aprendizaje](LEARNING.md), [operación](docs/OPERACION.md), [Toteat](docs/TOTEAT_READONLY.md).
