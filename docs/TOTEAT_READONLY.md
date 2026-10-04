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
