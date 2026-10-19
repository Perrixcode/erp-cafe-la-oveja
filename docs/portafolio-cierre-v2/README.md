# Cierre de caja V2: caso de estudio

Parte del proyecto personal ERP Café La Oveja de Esteban Iturra. El proyecto combina desarrollo manual, asistencia de IA, definición de requisitos operativos, diseño del flujo, revisión y validación. No se atribuye una autoría manual específica por módulo sin evidencia adicional.

## Problema y objetivo

Un cierre de caja requiere separar lo declarado, lo comparable y lo recibido. La propuesta V2 organiza el conteo, los respaldos y la revisión para evitar confundir un dato pendiente con cero, un depósito declarado con una recepción, o una firma de interfaz con aprobación administrativa.

## Flujo diseñado

1. Identificar caja, día operativo y momento del conteo.
2. Contar efectivo A por denominación y definir efectivo B retenido para el próximo turno. B es un subconjunto de A; la bolsa corresponde a A menos B.
3. Declarar totales por terminal, con período y alcance explícitos.
4. Adjuntar respaldos privados en el ERP o indicar por qué faltan; las pruebas públicas usan solo imágenes sintéticas.
5. Revisar diferencias de efectivo y terminales por separado. Las diferencias comparables requieren explicación; no se compensan entre medios.
6. Revisar una instantánea y registrar conformidad con una firma dibujada ligada al snapshot del servidor.
7. Declarar depósito por separado, sin dar por recibida o contada la bolsa.

## Estado verificable

La V2 está integrada en el backend Python/SQLite y en la interfaz del ERP. El piloto semanal fue publicado y verificado el 19 de octubre de 2026; la publicación del commit público se verifica por separado. El estado final y las pruebas se registran en [VERIFICACION.md](VERIFICACION.md).

El ERP existente tiene una distribución pública propia, instrucciones de ejecución y pruebas en el README raíz. Este caso de estudio se añade a ese proyecto: no reemplaza su código, documentación, historial ni licencia.

## Qué demuestra el trabajo

- Traducción de necesidades operativas en estados, validaciones y criterios de aceptación.
- Separación entre declaración, comparación, respaldo, custodia y revisión administrativa.
- Control de revisión y conservación de la declaración confirmada frente a cambios posteriores.
- Diseño de una transición desde prototipo local hacia controles de servidor.
- Revisión explícita de privacidad y límites de evidencia.

No se publican métricas de ahorro, precisión, uso o impacto comercial sin mediciones verificables.

## Documentación

- [Arquitectura y decisiones](ARQUITECTURA.md)
- [Ejecución y verificación](VERIFICACION.md)
- [Privacidad, procedencia y publicación](PUBLICACION.md)

## Autoría y licencia

Proyecto propio de Esteban Iturra, con participación manual y asistencia de IA. La responsabilidad descrita abarca requisitos, diseño del flujo, revisión y validación; no se inventa un reparto de autoría por archivo. Se conserva el historial existente sin alterar autores ni fechas.

El README público consultado declara que todavía no se ha elegido una licencia. Esta documentación no concede una nueva licencia ni modifica las condiciones de componentes de terceros.
