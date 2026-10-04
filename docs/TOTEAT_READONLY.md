# Prueba de lectura Toteat

## Lector local opcional

Con autorización para guardar credenciales localmente e iniciar el lector con la sesión del Mac, el usuario ejecuta en su Terminal:

```sh
python3 scripts/connect_toteat.py
```

El componente nativo `native/ToteatReader.swift` introduce los cuatro datos sin eco, guarda un elemento en el llavero de archivo local de inicio de sesión y limita su ACL al ejecutable nativo firmado. No usa iCloud ni permite acceso genérico a Python. El helper hace un GET fijo a `orderstatus?listing=true&det=true`; nunca exporta el token. Su salida interna viaja por una tubería al lector Python, que conserva solo las comandas con productos del catálogo local en `private/toteat-reader.sqlite3`, separado de los pedidos operativos. No se almacenan respuestas completas ni credenciales en SQLite.

El instalador prepara `~/Library/LaunchAgents/cl.oveja.erp.toteat.reader.plist`. El lector consulta cada 30 segundos, sin concurrencia; ante fallos aumenta la espera y ante problemas de acceso se detiene. Esta cadencia es conservadora respecto del límite público documentado de 10 solicitudes por segundo; no acredita cuotas particulares de la cuenta. Sin el Mac despierto y su sesión/llavero disponibles no hay lectura.

La sección **Comandas recibidas** conserva canal, proveedor, identidad y comentarios cuando están presentes en la respuesta; repetir una lectura no duplica la comanda, cambiar de canal no crea una segunda identidad y los cambios guardan historial. La ausencia posterior del listado no se interpreta como entrega o cancelación. La cobertura de cerradas se verificó después, usando la fecha de apertura del turno; ver el estado vigente.

Compilación, firma, self-test sin llavero/red, fixtures de recepción y UI sintética comprobados. La instalación local ya confirmó guardado en el llavero, lector automático activo y una comanda real recibida sin duplicación en lecturas repetidas. No se reutilizan tokens publicados en conversaciones. El agendamiento automático validado se describe a continuación.

## Estado vigente

Verificado el 4 de octubre de 2026 con dos pruebas que el usuario creó y cerró en Toteat. La primera venta apareció entre 296 transacciones del turno abierto el **2 de octubre**; la segunda entre 297. Solo se incorporaron las dos pruebas identificadas, no las ventas históricas del rango. Los comentarios de 107 y 93 caracteres llegaron en `Transaction.comment`. El listado y los formatos detallados de abiertas no entregaron ese texto.

- Comandas del catálogo aprobado: lectura cada 30 s. Ventas del turno conocido: cada 90 s; pantalla local cada 15 s. No es un webhook ni llegada instantánea.
- Cierre + saldo comprobado + nombre/fecha/hora interpretables → Agendadas automáticamente, sin aprobación manual. Una falta o inconsistencia bloqueante queda en Lectura para revisar. Teléfono ausente: aviso en el ticket.
- Descuento total y saldo cero: **Saldado con descuento**, sin inventar pago ni exigir boleta. El texto «Pagado» del comentario nunca reemplaza los importes de la API.
- La segunda prueba registra **$100**, aunque el usuario había anunciado $1. Identidad de orden y pago más importes coincidentes permitieron obtener un PDF oficial de **86.637 bytes**; vínculo privado validado, disponible para ver/descargar en el ticket. No se emitió ni envió ningún documento desde el ERP.
- Una sola identidad por restaurante/local/orden: al agendar desaparece de la bandeja, sin borrar su evidencia. Lectura no ofrece marcado ni un endpoint para marcar sus filas; el servidor exige pedido operativo y evidencia de agendamiento para transiciones Toteat.
- Las dos pruebas permanecen identificadas como tales. La del 30/10 figura en Simulaciones de octubre, con dos ítems. La otra conserva la fecha histórica escrita en el comentario. No se cambiaron fechas por suposición.

La [documentación de ventas](https://developers.toteat.com/paths/sales.yaml) selecciona por fecha de apertura del turno. La consulta exacta del **2/10** (`ini=end=20261002`) devolvió las 297 transacciones. El usuario completó el handoff de `private/oveja-toteat-automation`; firma y SHA coinciden, acceso persistente y consulta real de [`shiftstatus`](https://developers.toteat.com/paths/shiftstatus.yaml) quedaron verificados. No queda permiso de llavero pendiente.

### Corrección del timestamp y continuidad verificada

En respuestas reales `shiftstatus.date` avanzó con la hora de consulta (por ejemplo 19:51:44 y 20:02:48 UTC), mientras las ventas seguían asociadas al 2/10. Por tanto ese campo **no se usa como apertura acreditada**. El lector obtiene el estado open/closed y su ámbito de ese endpoint, y determina días con ventas mediante consultas exactas individuales.

Se conserva el último día que devolvió ventas y se consultan los días nuevos, con una ventana máxima de 14 días recientes más el día verificado si es anterior; también se incluye el día previo al último verificado dentro de esa ventana. La búsqueda nunca retrocede antes de la fecha conocida de arranque. Mantiene el filtro de activación y los dos IDs de prueba aprobados, sin incorporar ventas históricas por consultar un rango. Una ausencia prolongada o un turno iniciado fuera de los días cubiertos puede requerir confirmar su apertura; no se presume cobertura histórica ilimitada.

**Ciclo real verificado a las 20:02:56 UTC:** `state=receiving`, `shift_lookup_automatic=true`, estado abierto, consultas 2/3/4 de octubre, 297 transacciones, dos pruebas agendadas y cero revisiones. Un error HTTP transitorio anterior se recuperó mediante espera/reintento. El binario autorizado no se recompiló ni se cambiaron ACL; todas las consultas siguen siendo GET. No se creó ni cerró un turno real para probar el cambio: esa transición se verificó únicamente con fixtures.

El handoff `scripts/authorize_toteat_automation.py` conserva comprobación de firma/hashes/self-test previa al permiso y validación de acceso persistente desde un segundo proceso. No debe repetirse ahora: ya está activo. El worker recarga su configuración sin instalar otro servicio.

Pendientes: recuperar comentario mientras sigue abierta; verificar un cambio de turno real cuando ocurra; tratar reembolsos, pagos múltiples y cambios fuente ambiguos mediante revisión. No se cancelan automáticamente pedidos por una desaparición o una devolución. Deploy y acceso integral de usuarios siguen separados.

## Resultado real y comentario pendiente — histórico anterior al cierre

Lo que sigue documenta los diagnósticos antes de encontrar la fecha del turno. El estado vigente de arriba lo reemplaza; las frases de alcance y comandos pendientes pertenecen a ese momento.

### Objetivo vigente

Recuperar el texto íntegro del comentario para completar los datos del cliente. El usuario aclaró que **por ahora no necesita ver la boleta ni pasar el pedido a Agendado en el ERP**. La lectura del comentario no se condiciona a validar pago, documento fiscal ni transición de estado. Se conserva la identidad de la comanda y el texto original antes de interpretar campos; lo que falte o sea ambiguo queda por completar.

### Consultas completadas

Comprobación del 4 de octubre de 2026, sobre la misma orden existente y solo en lectura:

| Consulta | Resultado comprobado |
| --- | --- |
| `orderstatus?listing=true&det=true` | Respuesta exitosa; producto reconocido por ID exacto; sin campos de comentario de orden o ítem. |
| `orderstatus?ic=<orden>&det=true` | Respuesta exitosa e identidad coincidente; tampoco entrega el comentario. |
| `orderstatus?ic=<orden>&body_detail_type=DELIVERY_INFORMATION` | Respuesta exitosa e identidad coincidente; `platform=POS`, `delivery_information` vacío y sin comentarios. |
| `sales?ini=20261003&end=20261004` | `ok=true`, **0 transacciones antes de filtrar productos**. |
| `tables` | `ok=true`, **13 mesas devueltas**, ninguna coincide con el ID de mesa virtual de esta comanda. |

Las consultas anteriores de ventas con `ini=end=20261004` y `ini=end=20261003` también devolvieron cero. No se dispone de una transacción cerrada pertinente para comprobar `Transaction.comment`; estos vacíos no acreditan que los comentarios estén ausentes de todas las ventas. No se amplió la búsqueda histórica.

La imagen aportada muestra el texto en **Comentarios de esta orden → Comentarios anteriores** dentro del POS. Las otras capturas muestran **Nombre en Listado de Pedidos → Nombre / Comentario y Origen** y etiquetas breves bajo mesas ocupadas; eso no demuestra que dichas etiquetas sean el comentario íntegro. Se inspeccionaron los píxeles sin modificar opciones de Toteat. Las imágenes y datos personales permanecen en `private/`, fuera del repositorio.

También se revisaron todos los campos de texto del detalle antes del mapeo: `orderReference` y `vendorName` vacíos, sin texto multilínea ni etiquetas del afiche bajo otra clave. No se sustituyó el comentario por un nombre de mesa. La falta del texto no permite inferir entrega inmediata.

### Fallo local resuelto

El paso de autorización del ejecutable consolidado **`private/oveja-toteat-diagnostics`** terminó correctamente y verificó acceso persistente desde otra ejecución. El fallo posterior ocurrió en Python, antes de consultar ventas y mesas: la validación genérica rechazaba un ID entero negativo usado por la mesa virtual.

Se añadió una validación específica de mesas que conserva el signo y evita confundir una mesa virtual con una física. Las reglas para orden/producto/pago no se ampliaron. El diagnóstico ahora guarda checkpoints antes y después de cada etapa. Se completaron las dos consultas con el **mismo binario nativo autorizado, sin recompilar ni solicitar otra autorización**.

**No hay un comando pendiente del usuario; no repetir `authorize_toteat_diagnostics.py` ni los handoffs anteriores.** El lector principal sigue conectado y no fue reemplazado. El acceso a credenciales permanece dentro del componente nativo y no exporta el token a Python, al navegador ni a los informes.

El manifiesto privado congela binario, código, orden y rango autorizado. La corrección Python actualizó solo sus hashes pertinentes y preservó los del binario/fuente nativos. El informe `private/toteat-comments-latest-safe-report.json` confirma `complete=true`, las etapas `order`, `sales`, `tables` y ninguna importación operativa. Los originales pertinentes se guardaron aparte, de forma privada. **127 pruebas Python aprobadas** con `ResourceWarning` como error; las regresiones incluyen IDs negativos y llegada efectiva a ambas consultas.

### Bloqueo y consulta técnica preparada

La [documentación de `orderstatus`](https://developers.toteat.com/paths/orderstatus.yaml) declara `ALL_INFORMATION` equivalente a `det=true`. El [esquema oficial](https://developers.toteat.com/components/schemas.yaml) declara `Transaction.comment` en [`sales`](https://developers.toteat.com/paths/sales.yaml), que devuelve pagos de órdenes cerradas por fecha del turno. Eso todavía no identifica cómo recibir el comentario de esta comanda abierta. [`tables`](https://developers.toteat.com/paths/tables.yaml) no devolvió la mesa virtual correspondiente en la prueba real. No se exige cerrar la orden para resolver el alcance actual.

Consulta preparada para Soporte Toteat, **sin enviar**:

> Necesitamos recuperar por API el texto completo de «Comentarios de esta orden → Comentarios anteriores» de una comanda POS abierta en mesa virtual, para completar los datos del cliente. `orderstatus` con listado detallado, detalle individual y `DELIVERY_INFORMATION` responde correctamente pero omite ese texto. `tables` devuelve mesas, pero no la mesa virtual de la comanda. ¿Qué endpoint, parámetro o configuración soportada entrega ese comentario mientras la orden está abierta?

No se configuraron webhooks ni se cambiaron pedidos, pagos, inventario o categorías en Toteat. El problema del comentario no depende de ejecutar el ERP localmente: moverlo a un servidor no agrega campos a la respuesta del proveedor. El despliegue continúa separado y pendiente.

## Diagnóstico efímero anterior

Esta sección documenta la herramienta manual anterior, independiente del lector automático descrito arriba. El adaptador de demostración `erp/toteat_readonly.py` conserva su plan inejecutable. La prueba manual está en `scripts/toteat_diagnostic.py`; no hace falta repetir el ingreso de credenciales para diagnosticar el lector ya instalado.

```sh
python3 scripts/toteat_diagnostic.py
```

El usuario introduce los identificadores y el token oculto, revisa el endpoint y escribe `CONSULTAR` para realizar él la solicitud. El agente no rellena ni ejecuta con credenciales. Se recomienda empezar por la opción 1: catálogo activo `products`.

## Parámetros observados en configuración proporcionada

Destino fijo HTTPS: `api.toteat.com/mw/or/1.0/`. La autenticación mostrada usa query: `xir`, `xil`, `xiu`, `xapitoken`. Este documento no contiene valores.

| GET | Parámetros permitidos por el diagnóstico |
| --- | --- |
| `products` | `activeProducts=true` |
| `sales` | `ini`, `end`: AAAAMMDD, máximo un día de diferencia en esta prueba |
| `orderstatus` individual | `det=false` o `true`, `ic`: identificador de orden existente |
| `orderstatus` abiertas | `listing=true`, `det=true` |

La documentación pública distingue la consulta individual con `ic` del listado de abiertas con `listing`. El script `scripts/toteat_open_orders_probe.py` prepara una única lectura del segundo modo con esquema y evidencia de comentarios sanitizados. Esta herramienta manual no es una conexión permanente; la verificación real del lector instalado está descrita arriba. Fuente: [documentación oficial](https://developers.toteat.com/paths/orderstatus.yaml).

No se presupone que `end` sea inclusivo, ni se inventan headers ni estructuras de respuesta. La existencia de estas opciones no demuestra que la cuenta tenga permisos activos.

## Límite de seguridad

Una solicitud, sin reintentos, redirecciones, token en argumentos/archivo/SQLite/localStorage/log/URL del navegador ni respuesta persistida. La URL HTTPS autenticada se construye solo en memoria; el proveedor puede registrarla. La salida muestra nombres de esquema seguros, tipos, longitudes de arrays y flags booleanos conocidos. Omite valores comerciales y redacta claves sensibles/dinámicas; limita profundidad6, campos24 por objeto y240 nodos. No guardar credenciales sin nueva autorización. Sin ventas, cambios de stock, hooks ni ampliación de permisos.

TLS verifica certificados y hostname. En este equipo se encontró Python.org sin CA predeterminada (verify_code 20); el bundle público macOS `/etc/ssl/cert.pem` validó TLSv1.3 al host sin credenciales. El script lo utiliza cuando falta el CA de Python en macOS. No se desactiva verificación ni se modifica el sistema. Los mensajes distinguen certificado, DNS, timeout y conexión sin mostrar errores crudos.

## Después de la respuesta

La estructura recibida solo confirma una respuesta JSON; no demuestra que sea un catálogo exitoso (podría ser un JSON de error del proveedor). Falta verificar estado semántico y una muestra sanitizada que no incluya clientes ni secretos antes de mapear/importar. Para catálogo se admiten exclusivamente las categorías **Tortas enteras** y **Dulces enteros**, por IDs que se verifiquen en la jerarquía real. No hay parámetro de filtrado por categoría verificado: si el endpoint devuelve todo, el futuro filtro deberá ser local y explícito. No importar toda la carta ni seleccionar por substrings. Se requiere clasificación explícita de enteros; los ítems ambiguos y SKU de porciones se excluyen. No incorporar precios/catálogo comercial real al repositorio público.

Boletas: FiscalDocuments estaba deshabilitado en la referencia; no se activa ni consulta en esta entrega. Falta confirmar si devuelve PDF, enlaces o solo metadatos. Nunca fabricar una URL. Los documentos de la demo permanecen pendientes.

Notificaciones WhatsApp a Esteban: requisito conservado para la siguiente etapa; definir destino y autorización de envío. Hoy los eventos se consultan con `Store.notifications()` y el historial; no hay despachador externo activo.

## Vista previa del catálogo

```sh
python3 scripts/toteat_catalog_preview.py
```

El usuario ya obtuvo `ok=true` con `data` de 869 elementos. Se confirmó el esquema del primer registro; no se validaron automáticamente todos los elementos ni el catálogo para importar. Esta herramienta recorre cada fila de una nueva respuesta obtenida por el usuario.

1. Ingresa restaurante/local/usuario API y token oculto; escribe `CONSULTAR` para una única lectura `products?activeProducts=true`.
2. Revisa la tabla de nombres e IDs reales de categorías. Solo se proponen coincidencias completas de los nombres fuente confirmados «TORTAS ENTERAS - ¡Sin opción de escritura!» y «DULCES ENTEROS», normalizando mayúsculas y espacios. La etiqueta legible del primero sigue siendo «Tortas enteras». «Sin opción de escritura» pertenece al nombre comercial y no describe permisos de la API. No se usan substrings ni IDs inventados.
3. Elige números de esa tabla, separados por coma. Si un nombre corresponde a varios IDs, se mantienen separados y el usuario elige. Un ID con nombres incompatibles se excluye. Si falta una categoría no se sustituye por otra.
4. Escribe `VER` para mostrar los productos de la selección en tu terminal. Puedes cancelar antes de consultar o antes de mostrarlos.

| Campo observado | Uso de esta vista previa | Pendiente |
| --- | --- | --- |
| `ok` boolean y `data` array | Requiere exactamente true/lista | No acredita importación lista para producción |
| `category`, `categoryId` strings | Agrupar por ID y revisar etiqueta completa | Verificar IDs concretos mediante selección local; no persistirlos aún |
| `id` string | Identidad provisional y deduplicación de esta respuesta | Estabilidad entre consultas/locales pendiente |
| `idToteat` number | Mostrar referencia recibida | No se supone unicidad global ni equivalencia con SKU |
| `localCode`, `name` strings | Mostrar código/nombre y detectar porciones explícitas | Clasificación comercial final requiere revisión |
| `isModifier` boolean | true se excluye; tipo desconocido también | Sin importar registros de modificadores |
| `modifiers` array | Mostrar cantidad de opciones del producto, sin expandirlas | Opciones reales y su mapeo quedan pendientes |
| `price`, `referencePrice`, `description`, `images` | No se muestran ni guardan | Fuera de alcance de esta previsualización |

Los duplicados con el mismo `id` y payload idéntico se cuentan una vez. Un mismo `id` con registros distintos se pone en revisión y no se elige arbitrariamente una versión. El informe distingue exclusiones globales y detalles de excluidos dentro de las categorías seleccionadas; no enumera productos del resto de la carta.

Se excluyen nombres/códigos con trozo, porción, slice o rebanada. Un formato individual queda en revisión. «20 porciones» puede describir rendimiento de un entero dentro de una categoría autorizada: no se convierte en inventario de porciones ni en tamaño/receta confirmado. La preselección no demuestra stock, receta ni disponibilidad.

Todos los datos reales permanecen en memoria y la salida local, sin exportación, archivos, base demo ni GitHub. Los mensajes libres de `msg` no se imprimen. Si una respuesta refleja el token en la proyección, se bloquea su visualización. El servicio no amplía permisos ni mantiene acceso después de cerrar. Las pruebas usan únicamente productos e IDs ficticios.
