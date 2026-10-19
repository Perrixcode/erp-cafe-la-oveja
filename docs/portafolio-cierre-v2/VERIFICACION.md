# Ejecución y evidencia

La distribución pública contiene el ERP completo actualizado, conservando el historial del repositorio. Solo usa fixtures sintéticos. Instalar las dependencias del servidor antes de ejecutar las pruebas; las dependencias OCR son opcionales para su módulo.

```sh
python3 -m pip install -r requirements-server.txt
PYTHONPATH=tests:. python3 -B -m unittest test_cashier_v2 test_cashier_v2_http test_cash_bags_import -q
ERP_TEST_PYTHON=python3 node tests/test_cashier_v2_ui.cjs
node tests/test_cash_review_ui.cjs
```

Las pruebas usan directorios temporales, puertos loopback, cuentas e imágenes sintéticas. El recorrido interfaz/API incluye cajera, resumen, firma, depósito, recuperación, reintento, vista administrativa, revisión humana e invalidación por comparación tardía. El DOM mínimo no acredita diseño visual ni hardware real.

## Resultado comprobado · 19 de octubre de 2026

El piloto ERP se publicó y comprobó por HTTPS a las 03:10 UTC, versión `oveja-cashier-v2-final-20261019T030846Z`. Se verificaron salud, autenticación requerida, rechazo anónimo, hashes de los archivos servidos y política de fotos. Se restauraron y verificaron respaldos, se ensayó migración aditiva y reversión compatible, y se conservaron históricos, cuentas y permisos. No se registraron operaciones financieras de prueba en producción: las siete tablas V2 estaban vacías en la comprobación.

La regresión del runtime del servidor completó 28 suites y 316 pruebas. El ajuste final de cabeceras completó una prueba HTTP aislada adicional. La distribución pública saneada pasó 71 pruebas de backend y etiquetas, el recorrido interfaz/API con cajera y administrador, análisis sintáctico de 198 fuentes Python y 39 archivos JavaScript, y comprobación del diff.

La fuente pública preparada incluye una prueba adicional del recorrido administrativo sobre el mismo runtime. Las comprobaciones de interfaz usaron un DOM mínimo; no se inspeccionó un navegador, cámara física, firma con dedo ni impresora. Estas verificaciones físicas quedan para la prueba semanal.

La publicación GitHub requiere comprobar el commit remoto; este documento no afirma un push mientras siga en revisión interna.

Las etiquetas incluidas en `erp/data` se regeneraron con UUID ficticios, dominio `.test` y marca «PRUEBA LOCAL · NO OPERATIVA». Su importador y huellas corresponden exclusivamente a esos ejemplos; no deben usarse en producción.

No se agregan badges de CI ni métricas comerciales no verificadas. Se conserva la licencia del proyecto pendiente de elección y la licencia propia de la fuente tipográfica.
