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

Variables web: `OVEJA_DATA_ROOT`, `OVEJA_PUBLIC_ORIGIN` (HTTPS exacto, sin ruta) y `OVEJA_TRANSFERS_SNAPSHOT`. Cookies Secure en producción; Host/Origin comprobados; un proceso Gunicorn por las sesiones en memoria. Los reinicios exigen nuevo inicio de sesión, sin cambiar cuentas ni contraseñas.

El lector Linux recibe la credencial por `LoadCredential`; nunca descargarla, imprimirla ni ponerla en Git. Comparar restaurante/local con la instalación antes de activarlo. La configuración de consulta preserva la fecha verificada del turno y el punto inicial de importación.

Ovejita permanece separado. El exportador corre como su usuario, consulta SQLite en `mode=ro` y publica únicamente datos del panel y evidencia de las revisiones. El usuario web del ERP tiene prohibido acceder a `/etc/cafeteria` y `/var/lib/cafeteria`.

Para las fotos nuevas, el hook de archivo se añade al flujo del bot luego de probar una copia aislada. Conserva la imagen por SHA-256 y la selección confirmada; no altera la validación, mensajes, registro de pagos ni credenciales. Una falla de archivo no impide al bot revisar el comprobante. El panel antiguo solo se retira tras verificar que revisiones, seguimiento y cierres estén disponibles en el ERP; conservar código/configuración para reversión.

Respaldos: timer diario de SQLite y boletas. Verificar restauración a carpeta nueva. Sigue pendiente configurar destino externo cifrado y política de retención. El timer `oveja-erp-evidence-backup` conserva fotos y selección exacta en `/var/backups/cafeteria/erp-evidence`, con objetos por SHA-256 y manifiestos privados. La proyección se reconstruye desde SQLite y ese archivo; nunca publicar estas copias.

Cambiar dominio: crear DNS, ajustar `OVEJA_PUBLIC_ORIGIN` y bloque Caddy, validar y recargar. Los datos no dependen del dominio.
