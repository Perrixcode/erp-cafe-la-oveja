# Estado de la entrega actual · 4 octubre 2026

Carpeta correcta: `/Users/estebaniturra/Documents/ChatGPT/ERP Oveja`. No se borró el origen ni se incorporaron archivos privados a Git.

## Checkpoint de supervisión

- **Diseño A aplicado** a código local: referencia PDF materializada y revisada sin bloqueo pendiente. Marfil `#F7F4EF`, sidebar `#F2EEE7`, selección `#E4ECE5`, acción `#315C4B`, Noto Sans local. Tabla de agenda en escritorio, tarjetas en móvil, fecha/día/hora, estados con símbolo y texto; prioridades desplegables. Se retiró el slogan genérico del acceso.
- **QA aislado:** 196 pruebas Python aprobadas en macOS, pruebas Node y sintaxis JS aprobadas. Chrome dedicado 1180/320: acceso, roles, marcado, recarga, privacidad al salir, 16 módulos, recepción, foto y venta elegida. Sin desbordes ni errores JS. Se corrigió una referencia DOM obsoleta detectada durante el ajuste visual; el marcado y refresco posterior vuelven a pasar. Capturas en `qa/*-fake.png`, exclusivamente ficticias. La versión anterior del mismo backend pasó 196 pruebas en Ubuntu.
- **Bot:** modal con foto y descarga en la misma ventana listo y probado. Se conserva la opción exacta elegida por caja. Hook de archivo ensayado contra 21 pruebas del flujo Ovejita en copia aislada del servidor: aprobado. Todavía no activado en producción. Históricos sin imagen/detalle original se indican no disponibles; no se inventan asociaciones.
- **Analítica:** estructura preparada y propuesta en `docs/ANALITICA.md`: margen por producto, venta conjunta, horarios, precios, canales/reparto, mermas, recompra, productividad, pronóstico y calidad. Sin pipeline financiero ni KPIs inventados. Personas, documentos y proveedores siguen como módulos por desarrollar; facturas semanales contemplan texto para WhatsApp.
- **GitHub público:** `https://github.com/Perrixcode/erp-cafe-la-oveja`, identidad Perrixcode verificada. Referencias publicadas previas: `b12e82f` y `2a9d47b`. El diseño A forma parte de la entrega que contiene este checkpoint. Proyecto personal con asistencia técnica. Sin licencia del proyecto elegida.
- **DNS:** `erp.loxby.cl` resuelve a `46.224.147.187`. Usuario, carpetas, venv y código del ERP preparados de forma aislada. Puerto previsto `127.0.0.1:8010`. **HTTPS y servicios ERP todavía no activados; la URL pública aún no es utilizable.** Caddy y los servicios de Ovejita siguen sin modificar.
- **Bloqueo explícito:** la revisión automática rechazó pausar el ERP/lector locales y transferir pedidos, clientes, boletas, historial, hashes de cuentas y configuración privada. Se solicitó autorización específica y sigue sin respuesta. No se ejecutó el script de congelación/migración ni se copió ese archivo de datos al servidor. No reintentar la migración para sortear ese rechazo. La posterior aprobación de un reinicio exclusivamente local no autoriza por sí misma la transferencia privada a Hetzner.
- **Instalación local actualizada a las 22:31:55 UTC:** `http://127.0.0.1:8765`, PID 9314, código `1dcf612`. La alternativa limitada al Mac fue aprobada: respaldo verificado y reinicio solo de la web. Lector sin detener y ninguna transferencia al servidor. Tema, Noto Sans, Inicio, Transferencias y Recepción responden 200 con MIME y contenido idénticos al código. Tablas existentes, hashes de cuentas y boletas idénticos al respaldo; integridad y claves foráneas correctas. APIs privadas rechazan acceso anónimo (401; recepción 403). Las sesiones anteriores se invalidaron, no las contraseñas. Evidencia privada `private/local-a-activation.json`. Chrome dedicado sobre localhost real: login anónimo, Noto Sans, 1180/320 sin desbordes ni errores JS; capturas privadas `private/login-live-1180.png` y `private/login-live-320.png`.

## Siguiente paso después de aprobación

1. Congelar escritores locales, obtener y verificar respaldo privado, transferirlo por SSH y comprobar huellas/cuentas/adjuntos en carpeta exclusiva del ERP.
2. Actualizar el código público en el servidor con el commit final; comprobar alcance restaurante/local sin exponer tokens.
3. Activar servicios del ERP, lector y proyección privada; Caddy validado, HTTPS, pruebas de login y denegación sin sesión.
4. Activar hook de fotos con copia/reversión del archivo de Ovejita y verificar integridad de revisiones, selección, historial y cierres. Incluir fotos en respaldo privado del servidor.
5. Retirar el panel viejo solo tras comprobar paridad; preservar el bot y sus credenciales. Falta respaldo externo cifrado y política de retención.

## Registro histórico (los estados anteriores no describen la instalación actual)

# Checkpoint verificado · 2026-10-04 UTC

## Estado vigente: acceso integral, respaldos y estructura modular

- Activado en <http://127.0.0.1:8765>, carpeta correcta, proceso web 83178. Sin despliegue, commit ni push.
- Login profesional **ERP Oveja · Cocina y Café**; frase «Gestión clara. Equipo conectado.». Todo dato del ERP requiere sesión, incluidas las URLs directas a boletas, comentarios, historial, stock y notificaciones. Pantalla limpia al cerrar sesión y permisos comprobados por servidor.
- Dos cuentas de socios preexistentes preservadas sin cambiar nombre, usuario, sal ni hash. Ninguna cuenta real fue creada por el agente. Perfiles adicionales disponibles mediante alta local interactiva `scripts/create_user.py`.
- Socio: todas las operaciones; Producción: consulta, solicitar marcado y recetas; Caja: consulta, boletas y registrar entrega. Ya no existe selector que conceda permisos ni responsable arbitrario de formularios. Los cambios nuevos usan el usuario autenticado, y el historial anterior se conserva.
- Tarjetas con día de semana, fecha y hora; panel de pendientes de próximos 7 días, vencidos, marcado, falta de pago y datos incompletos. Simulaciones separadas y módulos futuros vacíos.
- Navegación solicitada: Pedidos de tortas (funcional); Despacho y reparto; Planificación de turnos; Control de asistencia; Conciliación de transferencias; Inventario y abastecimiento; Producción; Catálogo y proveedores; Analítica y reportes; Costos y rentabilidad; Gestión de clientes; Activos y mantenimiento. Subpestañas sin datos ni integraciones GPS, GeoVictoria o Power BI. Tres últimos módulos sugeridos y aceptados por el usuario; nombres profesionales solicitados. Navegación persiste al recargar y menú móvil compacto.
- Respaldo automático local al arranque y cada 24 h con proceso activo; reintento 30 min ante fallo. SQLite + PDF + hashes de cuentas, manifiesto verificado y restauración solo a carpeta NUEVA. Copia privada local verificada a 20:51:57 UTC / 17:51:57 Chile. Falta respaldo externo cifrado, retención y servicio de producción.
- **185 tests Python aprobados**; pruebas de permisos, cuentas heredadas, sesión/revocación, boletas, recuperación de SQLite/PDF/cuentas, archivos corruptos y separación de prioridades. Sintaxis JS y pruebas de notificaciones aprobadas.
- QA Chrome dedicado con fixtures 1180/320: login obligatorio, permisos por rol, PDF protegido, día de semana, logout sin datos, sesión en recarga, módulos vacíos/subpestañas persistentes, sin overflow ni excepciones JS. Capturas seguras `qa/login-*-fake.png`, `qa/access-agenda-*-fake.png`, `qa/modules-*-fake.png`.
- Activación real: todas las tablas de pedidos/ítems/historial/documentos/correcciones/SOS/vínculos preservadas por comparación de huellas; dos hashes de cuentas intactos. HTTP sin sesión: board/toteat/stock/receipt/notifications = 401. Lector abierto y ventas siguen `receiving`, búsqueda de turno automática activa. Evidencia privada `private/access-activation-evidence.json`. No se tocó el lector ni se cerró/abrió un turno real.
- Pendientes de hosting precisados en `docs/PREPARACION_SERVIDOR.md`: SO/dominio, servidor web de producción/HTTPS, sesiones, servicio, lector macOS y copia externa. No exponer `http.server` a Internet.

## Historial previo: Lectura → Agendadas, boleta y edición de socios

- Carpeta correcta y servidor: `/Users/estebaniturra/Documents/ChatGPT/ERP Oveja`, <http://127.0.0.1:8765>. Sin despliegue ni cambios nuevos publicados.
- Usuario confirmó automático: comanda abierta en Lectura; cierre y saldo comprobados con comentario válido → Agendadas. Desaparece de la bandeja, conserva identidad/evidencia y marcado solo desde el pedido agendado. API de transiciones exige evidencia de agendamiento para fuente Toteat.
- **Evidencia real:** turno abierto el 2/10; consultas 296 y luego 297 transacciones; comentarios de 107 y 93 caracteres recuperados. Solo las dos pruebas del usuario se incorporaron. Una saldada con descuento total; otra pagada por **$100 según API**, no $1. PDF real vinculado y validado: **86.637 bytes**, privado y accesible en el ticket. No se emitió ni envió boleta.
- Comprobación HTTP real después del reinicio: servidor sano, lector conectado, ventas actualizadas hace 37 s y automático activo para el turno 2/10; boleta de 86.637 bytes disponible. Ambas siguen marcadas PRUEBA. La segunda tiene dos ítems para **30/10/2026, 18:00** y aparece en Simulaciones de octubre. La primera conserva la fecha histórica del comentario y la solicitud de marcado que realizó el usuario. Sin modificaciones de sus datos durante QA.
- Dos vistas distintas, actualización local cada 15 s, abiertas cada 30 s y ventas cada 90 s. Aviso/acceso a pruebas del rango evita la falsa apariencia de pérdida en Operación. Fecha y hora visibles en todas las tarjetas. Vista y período persisten en URL.
- Datos de cliente editables solo con sesión de socio: nombre, teléfono, retiro/delivery, dirección, fecha y hora. Motivo/versión/cuenta auditados; comentario original, pago y boleta intactos. Corrección sobrevive sincronización y reinicio; resúmenes y avisos usan fecha corregida. SOS y autorización sin pago ya tienen servidor y UI para socios; ver la sección siguiente.
- **Cuentas locales aún no creadas**: el usuario configura cada socio con `python3 scripts/create_partner.py`, contraseña oculta. Sin credenciales iniciales; scrypt, cookie HttpOnly/SameSite, sesión 8 h, límite de intentos. Los demás perfiles de demostración siguen sin autorización real integral.
- **Pruebas sintéticas:** 174 tests Python aprobados, incluida la regresión de bloqueo de marcado sin evidencia. QA Chrome dedicado 1180/320: dos tickets sin duplicación en Lectura, fecha 30/10 visible, PDF ficticio ver/descargar, login de socio ficticio, edición a delivery/noviembre, doble clic, comentario intacto, recarga y persistencia; cero excepciones JS y sin desbordes. Evidencia: `qa/agenda-1180-fake.png`, `qa/agenda-320-fake.png`, `qa/customer-320-fake.png`, tickets ficticios anteriores.
- **Continuidad automática activa y verificada 20:02:56 UTC:** usuario completó el handoff; helper autorizado, `shift_lookup_automatic=true`, `shiftstatus` real exitoso, 297 transacciones y dos pedidos sin duplicar. Se corrigió una suposición: `shiftstatus.date` avanza con la hora de respuesta y no acredita apertura. Se conserva el día de ventas comprobado 2/10 y se consultan días nuevos individualmente (2, 3 y 4/10 en este ciclo). Un error HTTP transitorio se recuperó con el reintento. No hay permiso pendiente ni se recompiló el binario. El cambio real de turno no se provocó ni se afirma probado. Evidencia: `private/automatic-shift-live-verification.json`.
- Revisión de supervisor: exact match de restaurante/local/orden/pago y un solo evento de agendado por cada prueba; cliente/fecha/comentario idénticos al parseo fuente. PDF real local idéntico a HTTP, MIME `application/pdf`, descarga con `attachment`. Evidencia sanitaria privada: `private/supervisor-final-evidence.json`.
- Se reparó un fallo del worker: una venta sin comentario ya no aborta el ciclo completo; queda en revisión mientras las siguientes válidas se agendan. Test de regresión, cambio de turno y handoff con rechazos/configuración concurrente incluidos. Worker reiniciado manteniendo binario/configuración aprobados.
- Pendientes: comentario en abiertas omitido por los formatos probados; revisión de casos ambiguos/reembolsos/pagos múltiples; usuarios y acceso integral antes de despliegue. No se hacen escrituras a Toteat, stock ni envíos al cliente.

## SOS y excepción sin pago · entrega local

- **Activado 20:16:05 UTC**, con respaldo y comparación de tablas: pedidos, ítems, pagos, boleta e historial conservados; cero errores de claves foráneas y cero pedidos SOS creados en operación. HTTP real 20:16:44: servidor sano, SOS servido, 403 sin sesión, cuentas de socio aún no configuradas. Lector `receiving`, automático activo, 297 transacciones y último éxito 20:16:13, sin error. Evidencia privada: `private/sos-activation-evidence.json`.
- Ingreso Manual SOS protegido por sesión real de socio. Datos de cliente, retiro/delivery, dirección, fecha/hora, productos/cantidades, pago y motivo. Sin cuentas ni contraseñas reales creadas por el agente.
- Sin pago → borrador fuera de producción; socio autoriza con motivo → Agendado sin pago. Pago sigue falso, boleta pendiente y marcado/entrega intactos. Pago manual verificado exige referencia y se etiqueta como declaración del socio.
- Vínculo solo a comanda observada por el lector y productos/cantidades coincidentes; al llegar venta saldada, mismo pedido/ítems y cambios locales preservados. Nota SOS y comentario fuente separados. Sin vínculo, coincidencias pasan a revisión; socio vincula o declara distintas, con auditoría. No se empareja por nombre automáticamente.
- Reintentos y doble clic no duplican pedido ni autorización. Revalidación bajo bloqueo SQLite evita crear demanda duplicada si un SOS llega durante la importación. Pruebas aisladas no frenan ventas operativas.
- API: sin sesión/rol falsificado/CSRF/borrador sin autorización rechazados. Historial de borradores también protegido. No se escribe Toteat, inventario ni se envían mensajes.
- QA ficticio Chrome dedicado 1180/320: ingreso, autorización, pago sigue pendiente, reintento, recarga, logout, conciliación y pago posterior sobre mismo ID, notas conservadas. Cero excepciones JS/sin desborde. Capturas `qa/sos-1180-fake.png`, `qa/sos-320-fake.png`. Las pruebas UI no usaron la base real.
- Pendiente del usuario: crear sus cuentas de socio con `scripts/create_partner.py`; el agente no inventa claves. El acceso global fuera de estas funciones sigue siendo de prototipo local.

Las secciones siguientes son históricas y no reemplazan este estado vigente.

## Avance anterior: lector preparado y planner mínimo

- Handoff autorizado preparado en `scripts/connect_toteat.py`: helper nativo compilado y firmado, self-test sin red/llavero aprobado; usuario ingresa la credencial ocultamente en Terminal. No se observó Terminal ni se capturaron secretos. Guardado y primera lectura reales pendientes del resultado de ese ingreso.
- Lector GET fijo de abiertas cada 30 segundos, sin concurrencia, espera creciente ante fallos y detención ante problemas de acceso. Bandeja privada independiente con IDs exactos, canal/proveedor y comentarios originales. Repetición idempotente; cambios con historial; sin inferir entrega al desaparecer del listado. No hay conversión automática a pedidos operativos ni cobertura confirmada de cerradas. UI distingue claramente **Agendamiento por validar**.
- **Plan de tortas y bases**: tabla de cantidades por sabor/tamaño, bases agrupadas por tipo/unidad/diámetro explícitos y recetas faltantes visibles. Editor admite diámetro y unidad opcionales; recetas anteriores quedan por confirmar sin conversiones inventadas. Fracciones conservadas, marcado contado una vez, entregado/cancelado excluido. La necesidad neta de fabricar permanece desconocida; no descuenta stock.
- **109 pruebas Python aprobadas**, pruebas Node de notificaciones y sintaxis correctas. UI sintética en navegador dedicado: cantidades 10/20, base ficticia de 18 cm explícita, receta faltante, edición a 20 cm y cantidad 0,25, persistencia tras recarga; bandeja sintética conserva canal/comentario. Escritorio 1180 y móvil 390/320 sin desborde, cero excepciones JS. Ninguna escritura de QA en la base operativa.
- Capturas exclusivamente ficticias: [planner escritorio](../qa/planner-desktop-fake.png) y [planner móvil](../qa/planner-mobile-320-fake.png). No se presupone que 10 PP equivalgan a 18 cm en las recetas del usuario.
- Demo activa: <http://127.0.0.1:8765>. Commits previos `4276c14` y `447def3` preservados; este avance posterior aún no está comprometido ni publicado.

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

### Ajuste posterior de sonido

A petición del usuario, el aviso usa una campanita con tres parciales y caída progresiva de 1,98 segundos, conservando activación expresa, silencio y seguimiento de avisos. Verificado en navegador dedicado: AudioContext activo después de clic confiable; emisión de los tres parciales; render offline con energía decreciente hasta la cola y silencio después de dos segundos; pico 0,0685, sin clipping. No se afirma escucha física. Sintaxis JS, pruebas de notificaciones y diff correctos. No se crean pedidos para esta comprobación.

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
