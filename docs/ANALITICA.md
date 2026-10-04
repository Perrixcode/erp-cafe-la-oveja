# Analítica para decisiones comerciales

Estas secciones son diseño funcional pendiente, no indicadores conectados ni predicciones ya disponibles.

| Área | Cruce necesario | Decisión |
| --- | --- | --- |
| Rentabilidad por producto | Ventas netas, costo vigente de receta, merma, empaque y comisiones | Clasificar por aporte económico y volumen; priorizar oferta |
| Venta conjunta | Líneas de la misma transacción y disponibilidad | Identificar combinaciones con soporte, confianza y lift; probar sugerencias |
| Demanda por día y horario | Unidades vendidas, calendario y stock disponible | Ajustar producción y turnos |
| Precios y promociones | Precio efectivo, descuentos, unidades, costos y grupo comparable | Medir margen e incremento atribuible, no solo facturación |
| Canales y reparto | Origen, comisiones, costo de reparto y devoluciones | Comparar aporte de local, Instagram y web |
| Inventario y mermas | Compras, existencias, vencimientos, recetas y desperdicio | Reducir inmovilización y pérdida |
| Clientes y recompra | Compras de clientes identificados con tratamiento autorizado | Cohortes, frecuencia, recencia y recompra; mostrar cobertura |
| Productividad operativa | Ventas/carga por intervalo y horas efectivas | Dimensionar dotación; controlar estacionalidad y mezcla de productos |
| Proyección de demanda | Histórico limpio, calendario, eventos y disponibilidad | Estimar unidades con intervalos; comparar contra una base simple |
| Calidad de datos | Cobertura de costos, identidades, anulaciones y diferencias | Saber qué conclusiones se pueden sostener |

## Definiciones y controles previos

- Distinguir venta bruta, venta neta de descuentos/devoluciones, ingreso sin impuestos y dinero recibido. No sumarlos como si fueran equivalentes.
- El margen de contribución requiere costos variables completos; no equivale a utilidad neta. Versionar costos por fecha, unidades de receta y rendimiento real.
- Separar pruebas, ventas inmediatas, pedidos futuros, anulaciones y entregas. Contar una identidad de venta solo una vez, aunque haya varias revisiones o pagos.
- Usar fecha operativa del negocio y zona Chile de manera consistente. Mostrar rango, actualización, cobertura y tamaño de muestra.
- No atribuir causalidad a una correlación: medir promociones contra un período/grupo comparable o experimento autorizado.
- La venta observada puede estar limitada por stock. No llamar demanda cero a una hora sin disponibilidad verificada.
- Un análisis de canasta con muy pocas ventas puede ser inestable: informar soporte y validar fuera de la muestra.
- Empezar con reportes descriptivos y comparaciones. Aplicar pronósticos solo después de reunir histórico y evaluar error fuera de muestra.

Prioridad inicial: rentabilidad por producto → demanda por horario → venta conjunta. Requiere cargar y validar costos antes de presentar márgenes.
