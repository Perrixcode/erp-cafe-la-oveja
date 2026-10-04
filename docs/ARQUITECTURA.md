# Arquitectura y desarrollo

Proyecto personal de Esteban Iturra, desarrollado con asistencia técnica y revisión incremental.

## Tecnologías

- Python 3.11+ para dominio, permisos, validaciones, lector y tareas de respaldo. SQLite almacena pedidos, versiones e historial mediante transacciones.
- HTML semántico, CSS adaptable y JavaScript nativo. Sin React, Vue ni framework de interfaz. Módulos separados para recepción, Inicio, transferencias y avisos.
- El transporte local usa `http.server` solo en loopback. Producción usa el mismo router mediante WSGI y Gunicorn, con un proceso y cuatro hebras: las sesiones en memoria se comparten dentro de ese proceso y expiran al reiniciarlo.
- Caddy termina HTTPS y redirige a un puerto interno exclusivo. systemd administra web, lectura Toteat, proyección del bot y respaldos. El ERP dispone de usuario, entorno virtual, configuración y almacenamiento propios.
- Toteat se consulta mediante GET acotados. macOS usa un helper Swift y Keychain; Linux recibe una credencial privada por systemd. No se escriben ventas, pagos ni stock en Toteat.

## Cómo se construyó

1. Convertir el proceso del local en estados y reglas: recibir comanda, comprobar cierre/saldo, interpretar comentario, agendar, solicitar marcado, confirmar marcado y entregar.
2. Modelar pedido, productos, identidad fuente y eventos. La identidad restaurante/local/comanda evita duplicación. Versiones y transacciones detectan cambios simultáneos.
3. Separar casos que requieren decisión: datos incompletos, venta inmediata, agendamiento autorizado sin pago, productos modificados y documentos financieros anulados.
4. Construir una interfaz que muestre fecha con día, estado textual, origen, boleta y trazabilidad; verificar en escritorio y móvil con datos ficticios.
5. Añadir acceso por cuenta, permisos comprobados en servidor y respaldos verificados.
6. Integrar información de Ovejita mediante una proyección de lectura. El web del ERP no accede a su SQLite ni a sus credenciales.

## Mapa del código

| Responsabilidad | Archivos |
| --- | --- |
| Rutas HTTP y autorización | `app.py`, `erp/partner_auth.py` |
| Transporte de producción | `erp/wsgi.py`, `deploy/` |
| Pedidos y reglas | `erp/store.py`, `erp/domain.py`, `erp/schema.sql` |
| Recepción y decisiones | `erp/reception.py`, `erp/sos.py` |
| Parser y saldo Toteat | `erp/toteat_scheduling.py` |
| Lectores y evidencias | `scripts/toteat_worker.py`, `scripts/toteat_sales_worker.py`, `scripts/toteat_cancellations.py` |
| Transferencias y fotografías | `erp/transfers.py`, `scripts/export_bot_transfers.py`, `integrations/ovejita_archive.py` |
| Pantallas | `static/app.js`, `static/home.js`, `static/reception.js`, `static/transfers.js` |
| Respaldo y atención | `erp/backups.py`, `erp/attention.py` |

## Alcance y límites

Funcionales: Inicio, pedidos de tortas, recepción y revisión, marcado, boletas, cuentas, prioridades y lectura de transferencias del local. Las fotos nuevas del bot se archivan privadamente y se muestran en un modal con descarga, vinculadas mediante hash y revisión a la selección exacta de la cajera. El histórico que no tenía foto ni productos no se reconstruye por aproximación.

Las pestañas de personas, documentos, proveedores, reparto, turnos, asistencia, inventario, análisis y demás áreas son una estructura inicial. No simulan GPS, GeoVictoria, liquidaciones ni operaciones financieras. La futura preparación semanal de facturas contempla **texto para copiar a WhatsApp**, sin envío automático.

Limitaciones de fuente: el comentario no siempre está disponible en comandas abiertas; desaparecidas no equivalen a anuladas. Cancelación solo por evidencia explícita documentada; parcial/nota de crédito requiere revisión. El sondeo de anulaciones revisa hasta tres identidades conocidas por ciclo, por lo que no promete detección instantánea ni cobertura histórica completa.

El proyecto no acredita abonos bancarios por reconocer una imagen. No archiva claves ni datos privados en Git. No se ha elegido licencia.
