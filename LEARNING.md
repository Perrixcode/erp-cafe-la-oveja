# Aprender construyendo el ERP

## Negocio → concepto → archivos

Dos encargos del mismo producto/formato deben sumar una sola vez; cancelados y entregados ya no son encargadas. Los enteros reservados para vitrina se muestran aparte: no son pedidos ni porciones vendibles.

La función pura `summarize()` usa una clave `(sabor, tamaño)` y acumula cantidades. SQL persiste identidad e historial; la UI usa el mismo resumen.

Recorrido: `fixtures/orders_demo.json` → `erp/schema.sql` → `erp/domain.py` (`flatten`, `summarize`) → `tests/test_expanded_mvp.py` → `static/app.js`.

## Ejercicio separado

Cuando la base esté guardada en Git, usa una rama de aprendizaje:

```sh
git switch -c aprendizaje/resumen-enteros
```

Crea `experimentos/resumen.py`, sin tocar producción, con `agrupar(items)`:

```python
items = [
    {"sabor": "Chocolate", "tamano": "20 personas", "cantidad": 2, "estado": "pendiente"},
    {"sabor": "Chocolate", "tamano": "20 personas", "cantidad": 1, "estado": "marcado"},
    {"sabor": "Chocolate", "tamano": "10 personas", "cantidad": 3, "estado": "pendiente"},
]
```

Resultado: 3 enteros de 20 personas y 3 de 10, en grupos separados. Agrega un cancelado y un entregado y comprueba que no se sumen. `**` queda reservado a enteros retenidos para trozar/vitrina; no se incluye en nombres ni conteo de trozos.

Escribe pruebas para sumar dos filas iguales, separar tamaños y excluir finalizados. Después comprueba que `disponible(7, reserva=2)` sea 5 y con físico desconocido siga desconocido; no restes los pedidos otra vez.

```sh
python3 -m unittest discover -s tests -v
```

El ejercicio no bloquea el desarrollo del ERP. La rama de aprendizaje no se crea automáticamente.

## Siguiente concepto

Usa `Decimal` para que 3 mixtas × 0,5 bizcocho den 1,5, sin redondear ni mezclar bizcochos de 10 y 20 personas. Después estudia transacciones y la versión que impide sobrescribir un pedido cambiado en otra ventana.
