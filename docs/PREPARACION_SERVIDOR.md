# Despliegue aislado del ERP

Configuración preparada para Ubuntu 24.04, Caddy y systemd; sin Docker.

| Recurso | Ruta |
| --- | --- |
| Código | `/opt/oveja-erp/app` |
| Entorno Python | `/opt/oveja-erp/venv` |
| Configuración privada | `/etc/oveja-erp` |
| SQLite y adjuntos | `/var/lib/oveja-erp` |
| Proyección de transferencias | `/var/lib/oveja-erp-import` |
| Usuario | `oveja-erp` |
| Puerto | `127.0.0.1:8010` |

`deploy/` contiene unidades de servicio/timer, configuración Gunicorn y bloque de sitio Caddy. Conservar los bloques existentes de otros servicios. Validar Caddy antes de recargarlo. No abrir 8010 en el firewall.

Variables web: `OVEJA_DATA_ROOT`, `OVEJA_PUBLIC_ORIGIN` (HTTPS exacto, sin ruta) `OVEJA_TRANSFERS_SNAPSHOT` y `OVEJA_TRANSFERS_SOCKET` (`/run/oveja-erp-followup/service.sock`). Cookies Secure en producción; Host/Origin comprobados; un proceso Gunicorn por las sesiones en memoria. Los reinicios exigen nuevo inicio de sesión, sin cambiar cuentas ni contraseñas.

El lector Linux recibe la credencial por `LoadCredential`; nunca descargarla, imprimirla ni ponerla en Git. Comparar restaurante/local con la instalación antes de activarlo. La configuración de consulta preserva la fecha verificada del turno y el punto inicial de importación.

Ovejita permanece separado. El exportador corre como su usuario, consulta SQLite en `mode=ro` y publica únicamente datos del panel y evidencia de las revisiones. El usuario web del ERP tiene prohibido acceder a `/etc/cafeteria` y `/var/lib/cafeteria`.

Para las fotos nuevas, el hook de archivo se añade al flujo del bot luego de probar una copia aislada. Conserva la imagen por SHA-256 y la selección confirmada; no altera la validación, mensajes, registro de pagos ni credenciales. Una falla de archivo no impide al bot revisar el comprobante. El panel antiguo solo se retira tras verificar que revisiones, seguimiento y cierres estén disponibles en el ERP; conservar código/configuración para reversión.

Respaldos: timer diario de SQLite y boletas. Verificar restauración a carpeta nueva. Sigue pendiente configurar destino externo cifrado y política de retención. El timer `oveja-erp-evidence-backup` conserva fotos y selección exacta en `/var/backups/oveja-erp-evidence`, con objetos por SHA-256 y manifiestos privados; también respalda SQLite de Ovejita bajo `databases/`, incluidas las resoluciones del seguimiento. La proyección se reconstruye desde SQLite y ese archivo; nunca publicar estas copias.

Cambiar dominio: crear DNS, ajustar `OVEJA_PUBLIC_ORIGIN` y bloque Caddy, validar y recargar. Los datos no dependen del dominio.


## Estado de despliegue y reversión

ERP activo en https://erp.loxby.cl con diseño A. El lector local está deshabilitado y localhost solo redirige al HTTPS. Los datos operativos permanecen en `/var/lib/oveja-erp`; nunca volver a habilitar dos escritores sin conciliación.

El código está versionado en `/opt/oveja-erp/releases/` y `app` apunta a la versión activa. Ante un fallo de código, detener el lector, apuntar `app` a la versión anterior verificada y reiniciar solo los servicios ERP. Conservar la base de producción actual: restaurar una copia antigua podría perder movimientos posteriores.

Caddy conserva un respaldo `Caddyfile.before-oveja-erp-*` en `/etc/caddy/`. Validar la configuración antes de recargar. Los servicios webhook, worker y monitor del bot no dependen del ERP.

El archivo original del flujo de Ovejita y una copia SQLite íntegra se conservan en `/var/backups/cafeteria/before-erp-evidence-*`. Para revertir el hook de fotografías, restaurar solo su archivo Python y reiniciar `cafeteria-worker` cuando no esté procesando/enviando. No restaurar la base antigua sobre revisiones nuevas. Las fotos archivadas se preservan.

El reemplazo de seguimiento y exportación está implementado y documentado en [Paridad del panel](PARIDAD_PANEL_BOT.md). La retirada del servicio anterior requiere primero verificar la versión desplegada, datos y reversión. El puente `oveja-erp-followup` conserva las mismas resoluciones para los cierres y monitor, con escritura limitada a seguimiento; no tiene acceso de red ni credenciales.
