# Proveedores: integridad de edición y dashboard

Etapa local verificada el 14 de octubre de 2026. No desplegada.

## Estado inicial preservado

Repositorio: `/ruta/local/proyecto Oveja/private/release`.
Rama: `feat/delivery-fees`.
HEAD: `8f5f0c6add611ebe7ea57c27e46705c5371d3684`.

El árbol de trabajo ya tenía cambios y archivos sin seguimiento, incluidos los
módulos de cuentas por pagar y SII. HEAD no representa por sí solo la versión
funcional encontrada. No se hizo staging, commit, reset, clean, push ni despliegue.

Checkpoint local:
`/ruta/local/proyecto`.

- `before/`: 75 fuentes de aplicación y pruebas conservadas antes de modificar.
- `baseline-manifest.json`: rama, HEAD, estado Git y SHA-256 de cada fuente.
- `work/`: copia aislada usada para el cambio y la verificación.
- `changes.patch` y `result-manifest.json`: diferencias y huellas finales.
- `tests-final.log`: resultado de las pruebas.

El checkpoint contiene una selección explícita de fuentes; no es una copia de los
datos operativos ni del repositorio completo. No incluye credenciales, bases,
adjuntos reales, archivos SII reales ni directorios privados de datos. Los tres
scripts auxiliares copiados son dependencias de importación de `app.py`; no se
ejecutó ningún diagnóstico ni conexión Toteat.

## Cambios

1. `erp/payables.py`, `mutate(action='invoice')`: aplica `DuplicateIndex.find`
   también al editar, con `exclude_id` de la propia factura. Conserva controles
   sobre otras facturas, sus identidades históricas, evidencia de apertura y
   fuentes/vínculos SII. Los rechazos no guardan cambios ni nuevas auditorías.
2. `erp/payable_reports.py`, `report`: agrupa por `supplier_id`, conservando
   nombre y RUT como atributos. El ranking y el número de proveedores no mezclan
   nombres iguales. El orden tiene desempate estable por identidad.
3. `static/payables.js`, `renderDashboard`: muestra la etiqueta con RUT en el
   ranking; el análisis de concentración utiliza la misma identidad visible.
4. `tests/test_payable_integrity_regressions.py`: 12 regresiones nuevas.
5. Este documento registra el alcance y su verificación.

No se cambió el esquema, el cálculo del saldo por factura, la importación CSV,
los permisos ni el registro de pagos. Los campos `supplier_id`, `tax_id` y `label`
del ranking son aditivos; `name`, `amount` y `count` se conservan.

## Verificación ejecutada

Sobre la copia aislada, usando únicamente datos ficticios y SQLite temporales:

```sh
python3 -B run_isolated_tests.py test_payable_integrity_regressions.py
python3 -B run_isolated_tests.py
node --check work/static/payables.js
```

El arnés está en el checkpoint y permite únicamente bases dentro de su carpeta
temporal y conexiones de prueba de loopback. La suite incluye HTTP local de
autenticación/roles, importación y archivos; no se usaron servicios operativos.

- Regresiones antes de corregir: 12 métodos; 9 fallos de aserción (incluidos
  subcasos) y un error por campo ausente, reproduciendo los defectos.
- Regresiones después de corregir: **12/12 aprobadas**.
- Suite de proveedores, pagos, apertura y SII: **109/109 aprobadas** (97 existentes
  más 12 nuevas), sin omisiones, en 10,793 s.
- Sintaxis del frontend: `node --check`, código de salida 0.

Los primeros intentos del arnés detectaron dependencias faltantes de la copia,
restricción del sandbox para abrir HTTP local y normalización pendiente de URI
SQLite temporales. Se corrigió el entorno aislado y se ejecutó la suite completa
con éxito. Esos intentos no se presentan como pruebas aprobadas.

Las regresiones cubren identidades históricas de facturas pendientes/pagadas/
anuladas, cambios de RUT, exclusión de los antecedentes propios, apertura pagada
o sin folio, fuentes y vínculos SII, reintentos sin doble auditoría, proveedores
con igual nombre y RUT distinto, cambios de nombre y saldos sin validar. Las
pruebas existentes mantienen la cobertura del CSV, concurrencia, pagos, adjuntos,
respaldo/restauración y permisos.

## Límites y continuación

- No se validó ni se alteró el despliegue. No se hizo QA visual de navegador.
- Las coincidencias con antecedentes ajenos siguen bloqueando la edición; una
  excepción previa de importación no autoriza eludir automáticamente otra
  coincidencia. Las ambigüedades necesitan conciliación explícita futura.
- No se introdujo validación del dígito verificador del RUT ni identidad bancaria
  para detectar un pago real registrado mediante dos operaciones diferentes.
- B propuesto: modelo de pagos y aplicaciones a facturas, anticipos/saldos a
  favor y notas de crédito vinculadas; migración aditiva con preservación de IDs,
  abonos, originales y auditoría, ensayada primero en bases sintéticas.
- C propuesto: propuestas semanales con presupuesto, vencimientos, exclusiones
  y montos parciales revisables. Proponer/aprobar nunca registra un pago. Mantener
  el flujo actual de selección y CSV mientras se amplía el modelo.

Para revertir esta etapa, comparar exclusivamente los archivos enumerados con
`before/` y revisar cambios posteriores antes de restaurar. No aplicar una copia
masiva ni borrar el trabajo anterior. Los archivos nuevos de prueba/documentación
no existían en el checkpoint inicial.
