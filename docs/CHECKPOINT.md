# Checkpoint verificado · 2026-10-04 UTC

## Entrega vigente: catálogo limpio, simulaciones y campana

Esta sección reemplaza los conteos y el estado de demo de los checkpoints históricos de abajo.

- Instalación principal en `http://127.0.0.1:8765`, carpeta ERP Oveja correcta. `python3 scripts/start_local.py` comprueba el puerto e inicia el proceso separado de la terminal, sin instalar servicios. Solo localhost.
- Quedan **21 productos privados y 0 pedidos operativos**. Los ejemplos se retiraron después de verificar un respaldo completo: `private/backups/before-demo-cleanup-20261004T061453Z-d4025f1b.sqlite3`. El modo `toteat-local` impide repoblarlos. Base, respaldo y catálogo comercial están excluidos del repositorio.
- **Simular pedido** permite elegir el catálogo cargado, entrega inmediata o programada y contacto ficticio. Las pruebas se etiquetan explícitamente, se separan de la operación/indicadores y no afectan stock. Retirar/restaurar conserva el historial.
- Campana accesible con contador, producto, retiro y apertura de pedido. Abrir/leer no confirma marcado. Consulta local cada 15 segundos. Sonido opcional, activado por interacción; una solicitud nueva suena una vez y no vuelve a sonar por consulta o recarga.
- **93 pruebas Python aprobadas**, más pruebas Node del seguimiento de notificaciones. Sin advertencias de recursos en la última ejecución. La revisión para el commit añadió protección del contacto ficticio también al corregir una simulación; rechazo transaccional con pedido e historial intactos comprobado.
- QA de UI terminado en Chrome dedicado con SQLite y catálogo exclusivamente ficticios: crear/doble clic, programada pendiente, solicitud de marcado, abrir sin marcar, confirmar/entregar, retirar/restaurar, separación de stock e indicadores. Campana y foco con Escape; escritorio 1180 y móvil 390/320 sin desbordamiento; cero excepciones JavaScript. AudioContext activado con clic real de navegador y emisión única del oscilador verificados; no se realizó escucha física del altavoz. Recarga no reproduce avisos anteriores.
- UI principal revisada solo en lectura: 21 productos, 0 pedidos operativos y botón visible. Capturas ficticias: [campana escritorio](../qa/bell-desktop-fake.png) y [campana móvil](../qa/bell-mobile-320-fake.png).
- **Toteat no está conectado**. La documentación pública identifica `GET orderstatus?listing=true&det=true` para consultar abiertas; el identificador individual es `ic`. Falta verificar en la cuenta que el detalle incluya el comentario de la comanda y los campos necesarios para clasificar/programar. `scripts/toteat_open_orders_probe.py` está preparado para una lectura manual confirmada por el usuario, con salida sanitizada y sin persistir credenciales ni respuesta. No se ejecutó una consulta autenticada aquí.
- Integración permanente, almacenamiento de credenciales y despliegue permanecen pendientes; no se han configurado. La entrega local no necesita cambios en Toteat ni categorías duplicadas. Estos últimos cambios aún no están publicados en GitHub.

## Checkpoints históricos

## Carpeta y preservación

El proyecto está en la carpeta ERP Oveja seleccionada. Se compararon/copiaron 18 archivos de código, documentación, fixtures y pruebas desde el checkpoint anterior; SHA-256 idéntico antes de aplicar mejoras. Se conservó `.git` del destino. No se copió la base de datos, cachés ni credenciales; el origen no se borró ni modificó.

La primera base demo creada en destino se conserva como `data/erp-demo-pre-catalog.sqlite3`, excluida de Git. La actual usa el catálogo explícito de enteros. No reutilizar la base preliminar como si contuviera la versión final del esquema.

Servidor: <http://127.0.0.1:8765>. El proceso antiguo del origen fue identificado y detenido; el actual usa la carpeta correcta. El diagnóstico Toteat es otro script/proceso independiente.

## Verificación final

- **44 pruebas aprobadas**: negocio, HTTP local, rangos, catálogo/enteros, reserva manual 0..X, recetas decimales/tamaños, avisos de marcado, migración histórica y diagnóstico de transporte simulado.
- Sintaxis Python (`compileall`) y JavaScript (`node --check`) aprobadas.
- HTTP temporal con origen/Host, deduplicación, fecha y path traversal; recursos HTTPError se cierran explícitamente.
- Persistencia tras detener/reiniciar el proceso real: pedido QA, 3 enteros, versiones y seis eventos previos, reserva físico7/reserva2 y receta Mixta se conservaron idénticos. Las interacciones posteriores agregan sus propios eventos.
- Diagnóstico HTTPS sin credenciales: CA predeterminada de Python inexistente; error de cadena verify_code20. Bundle público macOS validó TLSv1.3 a api.toteat.com. Se corrigió la selección de CA sin desactivar certificado/hostname ni modificar el sistema.

## QA visual e interacción real

Navegador dedicado en segundo plano, sin competir con pestañas del usuario. Se revisaron escritorio (1180 y 1440) y móvil (390; borde320). El pequeño desborde del responsable en320 se corrigió.

Comprobado en UI: pago obligatorio; nuevo pedido ficticio; doble clic sin duplicación; transición y motivo; solicitud de marcado distinta al físico; aviso único y confirmación de Esteban; cancelar/volver; cancelación guardada; bloqueo de edición de ítem cancelado; reversión con historial; recetas0.5→0.25 con agregado0.75 y restauración0.5; conteo7/reserva2/disponible5; cinco rangos; personalizado invertido rechazado; previa y copia WhatsApp coherentes con rango y símbolo**. No hay envío externo. Consola sin errores al finalizar la sesión revisada.

Evidencias, exclusivamente datos ficticios:

- [Escritorio](../qa/desktop-dashboard.jpg)
- [Móvil](../qa/mobile-dashboard.jpg)
- [Reserva de vitrina móvil](../qa/mobile-reserva.jpg)

## Estado del alcance

Flujo final: **Pendiente de marcado → Marcado solicitado → Marcado físico → Entregado**. Cancelación y reversión conservan historial. El paso experimental de producción fue retirado, conservando trazas históricas y una migración explícita para lo aún activo.

Catálogo ficticio de enteros; recetas configurables; vistas demo Esteban/jefa; indicadores con denominadores y límites. Boleta en estado pendiente, sin enlace fabricado. WhatsApp alerta a Esteban queda como requisito futuro separado de los avisos in-app.

No hay autenticación real, integración en vivo, importación comercial, cambios de inventario, hooks ni despliegue. El script manual permite que el usuario haga una prueba de lectura; no significa que el ERP esté conectado.

## GitHub y hosting

Identidad confirmada oficialmente: Perrixcode. Destino inicialmente sin commits ni remoto; nombre `erp-cafe-la-oveja` no encontrado durante la comprobación. GitHub CLI autenticó correctamente al permitir red; el primer error de auth del sandbox no demostró un token inválido.

Secret-scan de código/documentación/fixtures: sin credenciales detectadas; clientes y SKU ficticios. Bases, claves, logs, .env y adjuntos excluidos. La publicación fue reautorizada después de corregir el problema TLS. La disponibilidad del nombre y la identidad Perrixcode se reconfirmaron antes de preparar el repositorio. La URL/commit publicados se entregan tras verificar GitHub.

VS Code recibió `--new-window` con la carpeta correcta, sin cerrar cambios del usuario. No se inspeccionó el terminal donde ingresa el token.

Lookup secundario del proyecto de transferencias, solo README/configs de despliegue: archivos de systemd y Caddy reverse_proxy presentes. Eso acredita una configuración prevista, no identifica ni verifica proveedor/servidor activo. Sin SSH, secretos ni despliegue.

Tras la reconexión del equipo, el servidor de la demo se volvió a iniciar sobre la misma SQLite. Los conectores de UI devolvieron `Transport closed`, por lo que no se pudo repetir la captura final de320px ni confirmar visualmente la ventana nueva de VS Code. El QA de interacción/capturas anteriores sí se completó; las pruebas automáticas finales pasaron después de los cambios.

El usuario obtuvo una respuesta JSON tras corregir TLS; esto no confirma todavía éxito semántico ni catálogo validado. El diagnóstico se amplió a esquema acotado con nombres seguros, flags conocidos y tipos sin valores comerciales. Se espera la siguiente ejecución manual del usuario.

Publicación verificada: https://github.com/Perrixcode/erp-cafe-la-oveja (público). Commit inicial de implementación `45b1d01a6010ec28227596ec83f63a90da2828ca`, idéntico en Git local y API oficial GitHub. Sin licencia agregada ni despliegue.

## Continuación: vista previa local del catálogo

Se añadió `scripts/toteat_catalog_preview.py`: consulta manual única, selección explícita de IDs reales de las dos categorías por nombre completo, tabla local de candidatos e informe de excluidos. Sin importación ni persistencia comercial/credenciales. Se confirmó mediante respuesta comunicada por el usuario el sobre `ok=true`, `data` array y los campos de categoría/producto; los valores e IDs reales no se copiaron al repositorio.

58 pruebas aprobadas (14 nuevas de catálogo), incluyendo conflictos de categorías/identidad, duplicados, exclusión de modificadores/porciones, selección explícita, balance de conteos, fallos `ok=false`, cancelación, ausencia de escritura y bloqueo de token reflejado. El usuario debe ejecutar la consulta autenticada y revisar la selección; el agente no la ejecutó.

Corrección del selector: el nombre fuente de tortas es exactamente «TORTAS ENTERAS - ¡Sin opción de escritura!», con etiqueta legible «Tortas enteras». Se obtiene el ID de la respuesta, sin hardcodearlo ni ampliar coincidencias por substring. Prueba ficticia de regresión: admite nombre completo y rechaza etiqueta corta/sufijos. Suite de 59 pruebas aprobada; no se incorporaron IDs internos ni productos reales.

## Catálogo privado incorporado y tipos de entrega · 2026-10-04

La instalación local incorporó manualmente 21 productos revisados (12 tortas y 9 dulces); el repositorio conserva únicamente datos de ejemplo. El archivo comercial y SQLite se encuentran en rutas ignoradas. No se consultó Toteat ni se usaron credenciales durante la incorporación.

Se realizó un respaldo mediante SQLite Backup antes de cargar. Los hashes de todas las tablas preexistentes coincidieron después de importar: 8 pedidos, 9 ítems y 22 eventos de historial intactos, además de recetas, conteos, documentos y metadata. IDs/campos de origen comprobados contra el archivo privado. Segunda ejecución: 0 agregados, 21 sin cambios, una sola entrada de auditoría. Integrity check y foreign key check correctos. El catálogo combinado tiene 33 productos: 21 locales y 12 ejemplos preservados. No se agregó stock ni se inventaron precios.

Flujo explícito del pedido: inmediata crea ítems entregados; programada crea pendientes de marcado con teléfono/fecha/hora y pago simulado. Los registros antiguos sin clasificación conservan su estado y aparecen por confirmar. No se duplican productos ni inventarios; no se interpreta automáticamente texto libre. Los no pagados siguen rechazados hasta implementar la autorización correspondiente.

78 pruebas aprobadas, con casos de importación aditiva, IDs exactos, conflictos que revierten todo el lote, idempotencia, independencia de recetas, stock desconocido/cero, entregas inmediatas/programadas, duplicados y conservación de históricos. Sintaxis Python/JavaScript y diff comprobados.

QA en Chrome headless con perfil temporal dedicado, sin usar ventanas/sesiones del usuario. Instancia de prueba aislada con productos e IDs ficticios: catálogo y procedencia, doble envío (un solo pedido), inmediata entregada, programada pendiente, filtros, cancelar/volver, cancelación/reversión y recarga. Viewports 1180, 390 y 320 px sin desbordamiento; cero excepciones JS. La UI de la instalación principal se revisó solo en lectura: 33 productos, 21 incorporados manualmente. Capturas de QA usan fuente simulada para probar el distintivo y nunca contienen catálogo real.

La conexión automática de comandas, comentarios y stock sigue sin implementar/verificar. Se requiere una muestra de venta de prueba sanitizada para comprobar IDs de venta/ítem, comentario, pago y canal; no se debe adivinar ese mapeo a partir del catálogo. Sin hooks, polling ni despliegue.

Reinicio final verificado: todas las tablas de la instalación principal y la base de QA conservaron sus hashes. La API volvió a responder correctamente; el catálogo principal siguió con 33 productos. La base ficticia conservó inmediata entregada y programada pendiente después de cancelar/revertir. La instancia aislada se cierra al terminar; queda activa únicamente la demo principal en http://127.0.0.1:8765.

Capturas nuevas, exclusivamente ficticias: [catálogo de escritorio](../qa/catalog-desktop-fake.png) y [programada en móvil de 320px](../qa/scheduled-mobile-fake.png).
