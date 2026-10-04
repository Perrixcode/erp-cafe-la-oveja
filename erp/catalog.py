"""Catálogo ficticio explícito. Ningún código procede de Toteat."""
from decimal import Decimal
from erp.domain import DomainError, OUTSTANDING, flatten


def product(sku, flavor, size, category, bases=None):
    return dict(sku='DEMO-'+sku, flavor=flavor, size=size, category=category,
                kind='torta', whole=True, bases=bases, version=0,
                catalog_group='Dulces enteros' if category in {'cheesecake','pie','kuchen'} else 'Tortas enteras')


CATALOG = [
    product('FRA-10','Frambuesa','10 personas','bizcocho'),
    product('FRA-20','Frambuesa','20 personas','bizcocho'),
    product('CHO-10','Chocolate','10 personas','bizcocho', [{'name':'Bizcocho chocolate','size':'10 personas','quantity':'1'}]),
    product('CHO-20','Chocolate','20 personas','bizcocho', [{'name':'Bizcocho chocolate','size':'20 personas','quantity':'1'}]),
    product('MAN-20','Manjar nuez','20 personas','bizcocho'),
    product('AMOR-20','Amor hojarasca','20 personas','hojarasca', [{'name':'Hojarasca','size':'20 personas','quantity':'10'}]),
    product('HOJA-20','Hoja manjar','20 personas','hojarasca', [{'name':'Hojarasca','size':'20 personas','quantity':'14'}]),
    product('MIX-20','Mixta','20 personas','mixta', [{'name':'Bizcocho chocolate','size':'20 personas','quantity':'0.5'},{'name':'Hojarasca','size':'20 personas','quantity':'6'}]),
    product('ZAN-20','Zanahoria','20 personas','zanahoria'),
    product('CHEESE-ENTERO','Cheesecake demo','Entero · formato demo','cheesecake'),
    product('PIE-ENTERO','Pie demo','Entero · formato demo','pie'),
    product('KUCHEN-ENTERO','Kuchen demo','Entero · formato demo','kuchen'),
]


def validate_product(item):
    found = next((p for p in CATALOG if p['sku'] == item['sku']), None)
    if not found or not found['whole']:
        raise DomainError('Selecciona un producto entero del catálogo demo. Un SKU desconocido necesita clasificación manual.')
    if (item['flavor'], item['size']) != (found['flavor'], found['size']):
        raise DomainError('El sabor y tamaño deben coincidir con el producto entero seleccionado.')


def production_plan(orders, catalog):
    """Escenario sobre encargadas; no afirma qué está elaborado ni descuenta stock."""
    recipes = {row['sku']: row for row in catalog}
    totals, missing = {}, []
    for order, item in flatten(orders):
        if item['status'] not in OUTSTANDING:
            continue
        recipe = recipes.get(item['sku'])
        if not recipe or recipe['bases'] is None:
            missing.append(dict(sku=item['sku'],flavor=item['flavor'],size=item['size'],quantity=item['quantity']))
            continue
        for base in recipe['bases']:
            key = (base['name'], base['size'])
            totals[key] = totals.get(key, Decimal(0)) + Decimal(base['quantity']) * item['quantity']
    return {'scope':'encargadas', 'totals':[{'name':k[0],'size':k[1],'quantity':str(v)} for k,v in sorted(totals.items())], 'missing':missing}
