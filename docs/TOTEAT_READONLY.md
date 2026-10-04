# Prueba de lectura Toteat

La UI demo no está conectada ni importa datos comerciales. El adaptador `erp/toteat_readonly.py` conserva su plan inejecutable. La prueba manual, autorizada aparte, está en `scripts/toteat_diagnostic.py`.

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
| `orderstatus` | `det=false`, `oic`: ID externo existente |

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
2. Revisa la tabla de nombres e IDs reales de categorías. Solo se proponen coincidencias completas de «Tortas enteras» y «Dulces enteros», normalizando mayúsculas y espacios. No se usan substrings ni IDs inventados.
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
