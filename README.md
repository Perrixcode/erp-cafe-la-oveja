# ERP Café La Oveja

Prototipo local de **tortas y dulces enteros**: encargos pagados, marcado, coordinación, bases de producción y reserva para vitrina. Python estándar, SQLite y HTML/CSS/JavaScript responsive, sin dependencias de ejecución.

**Los pedidos, pagos y conteos son ficticios. La distribución pública incluye solo un catálogo demo; una instalación local puede incorporar un catálogo privado de forma manual. No sincroniza Toteat, no ajusta inventario externo, no envía WhatsApp ni emite boletas. Los perfiles son una demostración visual, sin autenticación real.**

## Ejecutar la demo

En la instalación con catálogo privado, pulsa **Simular pedido**. Selecciona Programada, un producto y confirma el pago ficticio. En el pedido, **Solicitar marcado** crea el aviso de la campana; **Activar sonido** habilita un tono corto para nuevas solicitudes en esa pestaña. Abrir el aviso no marca físicamente la torta. Inmediata se guarda entregada.

Las simulaciones llevan etiqueta **PRUEBA** y vistas separadas de la operación; no alteran stock ni indicadores operativos. **Retirar prueba** la conserva en **Pruebas retiradas**, desde donde se puede restaurar. Tras la limpieza del catálogo privado no se vuelven a cargar ejemplos iniciales.

Para dejar el servidor local separado de la terminal, sin instalar un servicio:

```sh
python3 scripts/start_local.py
```

La conexión automática desde Toteat sigue pendiente. Este botón permite probar solamente el flujo local.

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

No se interpretan todavía comentarios de Toteat. Un futuro lector deberá conservar el texto original, validar los campos del agendamiento y mandar los casos ambiguos a revisión. Los pedidos no pagados requieren un flujo de autorización explícito todavía pendiente; la demo los rechaza.

## Catálogo, bases y perfiles demo

El catálogo de `erp/catalog.py` es ficticio, con SKU `DEMO-`. El importador local admite un archivo privado previamente revisado, limitado a **Tortas enteras** y **Dulces enteros**, con identidades fuente verificadas. No se importará el resto de la carta ni se usará coincidencia parcial de nombres como autorización. SKU desconocidos, trozos y porciones se rechazan; no se adivina que un artículo ambiguo sea entero.

Los bizcochos tienen formatos de 10 y 20 personas. Hojarasca, mixta y zanahoria solo 20. Cheesecake, pie y kuchen demo tienen formato entero propio, sin imponer 10/20 personas ni inventar receta.

Valores iniciales editables indicados para 20 personas: Amor hojarasca, 10 hojarascas (provisional); Hoja manjar, 14; Mixta, 0,5 bizcocho chocolate + 6 hojarascas. Chocolate de bizcocho tiene 1 base del tamaño correspondiente. Los sabores de base no confirmados muestran «Receta/cantidad pendiente». Rellenos pendientes.

El usuario elige el escenario **Todas las encargadas** para calcular bases. Es una referencia de planificación; no calcula automáticamente lo que falta elaborar porque no hay registro de bases ya hechas. Los decimales se suman exactamente y los tamaños no se mezclan: 3 mixtas → 1,5 bizcochos de 20 personas + 18 hojarascas. No se redondea a 2 automáticamente.

La vista Esteban muestra el acceso completo; la vista Jefa de producción permite consultar y solicitar marcado/configurar bases. Son controles de UI para probar el flujo, **no permisos de seguridad**. Autenticación y autorización en servidor quedan pendientes.

## Stock y reserva para vitrina

`**` significa **enteros que Esteban decide reservar para trozar y mantener vitrina**. Nunca representa inventario de trozos. Puede decidir 0, 1 o X; no hay una regla automática de reservar la última unidad.

```text
Disponible para venta entera = físico entero − reserva vitrina
```

Los encargos no se restan nuevamente. Si el físico es desconocido, el disponible sigue desconocido aunque se declare una reserva. El conteo es **actual e independiente del período de pedidos**; el total solo cubre sabores registrados. Conteos y reservas guardan motivo, responsable, fecha y versión, sin escribir inventario real.

## Indicadores y boletas

Pedidos y unidades se presentan separados. El número de pedidos incluye finalizados/cancelados; unidades excluye canceladas e incluye entregadas. Se desglosa por canal y por producto/formato en el resumen. Solicitud→marcado muestra promedio de confirmaciones registradas sobre pedidos del rango. A tiempo usa ítems actualmente entregados con evento de entrega y compara su timestamp con retiro programado; no es porcentaje de ventas. Sin eventos muestra «Sin datos». Todo son métricas de demostración, sin ingresos ni márgenes inventados.

El modelo conserva una referencia de boleta en estado pendiente. No se muestra un enlace fabricado. Falta verificar si FiscalDocuments devuelve metadatos, PDF u otra referencia, y autorizar cualquier configuración requerida; no se activa aquí.

## Diagnóstico manual de Toteat

La demo continúa aislada. Existe una herramienta **separada**, que debe iniciar y confirmar el usuario:

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

Desarrollo asistido por IA, con revisión y aprendizaje incremental del autor. Prototipo experimental, sin afirmaciones de experiencia/integración de producción. No se incluyen clientes, credenciales, bases de datos ni catálogo comercial real. Sin licencia elegida todavía.

Identidad negro/blanco inspirada en [Café La Oveja](https://cafelaoveja.cl); crema, oliva y caramelo son interpretación del ERP. Tipografía del sistema; sin fotos ni archivos del logo oficial.

Pendientes: lectura y mapeo verificados de comandas/ventas, interpretación validada de comentarios y excepciones de pago; boletas verificadas; alcance de bases faltantes; notificaciones WhatsApp a Esteban (recordadas, aún sin despacho); autenticación/roles reales, respaldos y servidor de producción. Hosting y GitHub son etapas separadas; no se despliega aquí.

Guías: [aprendizaje](LEARNING.md), [operación](docs/OPERACION.md), [Toteat](docs/TOTEAT_READONLY.md).
