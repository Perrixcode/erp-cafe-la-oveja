# Saldo provisional y análisis de cierres

En Control de efectivo → Resumen y Movimientos, un socio puede confirmar el efectivo total del negocio al comienzo de una fecha en Chile. El formulario parte vacío; no existe una apertura predeterminada. El motivo y la evidencia son obligatorios. Cada corrección añade una versión inmutable y conserva el registro previo. Es configuración de una proyección, no un ingreso ni un movimiento financiero.

La proyección suma al punto de partida las ventas físicas identificables netas de vuelto y otros ingresos externos clasificados, y resta los egresos externos del libro de custodia existente. Las transferencias internas son neutras. Vincular un pago a un egreso no vuelve a restarlo. Un ingreso existente requiere confirmar si ya está incluido en ventas; esta clasificación también conserva su historial. No se suman conteos A, fondos B, bolsas C ni `collection.cash` como entradas del negocio. El importe neto de movimientos Toteat sin destino validado tampoco se convierte en egreso global.

El bloque global usa el rango de fechas y todas las ubicaciones. Los filtros de caja y turno afectan las declaraciones, no dividen este saldo global. El saldo al inicio de un rango posterior al corte incluye únicamente los flujos previos conocidos y queda señalado como parcial. Sin punto de partida se muestra la variación conocida y el saldo absoluto permanece indeterminado. Sin observaciones, un flujo es desconocido; no se inventa cero. El saldo provisional no acredita disponibilidad para pagar.

Se toma una última lectura utilizable por día, conservando aviso si la última consulta falló. Las identidades de pagos se deduplican; conflictos de importe, caja o fecha se excluyen. La comprobación también abarca pagos observados fuera del filtro o antes del punto de partida. La integración actual no certifica cobertura completa de ventas, devoluciones y egresos, por lo que el estado siempre es provisional o pendiente.

En Análisis se muestran población total, presentada, comparable, revisada y con identidad original segura. Porcentajes y frecuencias incluyen solo observaciones comparables y revisadas con identidad segura; cada métrica muestra su denominador. Efectivo, recepción de bolsa y Mercado Pago son métricas independientes. Faltantes y sobrantes se conservan separados y no se compensan.

Mercado Pago se cuenta por terminal y período, incluyendo declaraciones V2 sin duplicar los reportes usados como referencia. Los períodos superpuestos del mismo terminal se excluyen antes de aplicar filtros. Una comparación tardía puede actualizar la observación analítica; exige nueva revisión humana y no altera el snapshot firmado. Se conserva el responsable original, último editor, correcciones, adjuntos, motivos pendientes y enlaces al cierre.

Las sesiones conocidas sin declaración vinculada no son turnos esperados ni prueban ausencia de trabajo. No se atribuyen a una cajera sin vínculo seguro. Jornadas de lunes a viernes, cierres administrativos al día siguiente y sesiones del viernes abiertas durante el fin de semana no generan una calificación de atraso. La antigüedad se expresa como tiempo calendario, con responsable de la próxima acción.

## Persistencia y validación

La migración es aditiva: `cc_balance_origins` y `cc_income_classifications`, sin precargar importes, alterar el libro existente ni cambiar permisos. Los comandos exigen socio, misma procedencia, confirmación explícita, versión vigente e idempotencia; los GET no escriben. Las pruebas usan SQLite temporal y datos sintéticos: conservación de efectivo, deduplicación, cobertura desconocida, firma preservada, permisos HTTP y contratos de interfaz ejecutados desde terminal. No se realizan pruebas financieras en producción.

Antes de publicar se restaura un respaldo, se ensaya la migración dos veces y se comparan tablas y esquema previos. La reversión cambia solo el código a la release anterior y conserva la base actual y las nuevas evidencias. No restaurar un respaldo antiguo sobre movimientos posteriores.
