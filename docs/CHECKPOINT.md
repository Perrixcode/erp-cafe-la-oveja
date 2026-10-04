# Checkpoint verificado · 2026-10-04 UTC

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
