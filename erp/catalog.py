"""Catálogo ficticio explícito. Ningún código procede de Toteat."""
from decimal import Decimal
from erp.domain import DomainError, OUTSTANDING, flatten


def product(sku, flavor, size, category, bases=None):
    return dict(sku='DEMO-'+sku, flavor=flavor, size=size, category=category,
                kind='torta', whole=True, bases=bases, version=0, source='demo', is_demo=True,
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


def validate_product(item, catalog=CATALOG):
    found = next((p for p in catalog if p['sku'] == item['sku']), None)
    if not found or not found['whole']:
        raise DomainError('Selecciona un producto entero del catálogo local. Un SKU desconocido necesita clasificación manual.')
    if (item['flavor'], item['size']) != (found['flavor'], found['size']):
        raise DomainError('El sabor y tamaño deben coincidir con el producto entero seleccionado.')


def production_plan(orders, catalog):
    """Escenario sobre encargadas; no afirma qué está elaborado ni descuenta stock."""
    recipes = {row['sku']: row for row in catalog}
    totals, cakes, missing = {}, {}, {}
    for order, item in flatten(orders):
        if item['status'] not in OUTSTANDING:
            continue
        recipe = recipes.get(item['sku'])
        cake_key=(item['sku'],item['flavor'],item['size'])
        cakes[cake_key]=cakes.get(cake_key,0)+item['quantity']
        if not recipe or recipe['bases'] is None:
            missing[cake_key]=missing.get(cake_key,0)+item['quantity']
            continue
        for base in recipe['bases']:
            diameter,unit=base.get('diameter_cm'),base.get('unit')
            # Sin formato confirmado se conserva el tamaño comercial separado.
            size=base['size'] if not diameter or not unit else ''
            key = (base['name'], size, diameter or '', unit or '')
            totals[key] = totals.get(key, Decimal(0)) + Decimal(base['quantity']) * item['quantity']
    rows=[dict(sku=k[0],flavor=k[1],size=k[2],quantity=v,recipe_defined=k not in missing) for k,v in sorted(cakes.items(),key=lambda p:(p[0][1],p[0][2],p[0][0]))]
    return {'scope':'encargadas','cakes':rows,'cake_quantity':sum(cakes.values()),'net_to_make':None,
            'totals':[{'name':k[0],'size':k[1],'diameter_cm':k[2] or None,'unit':k[3] or None,'format_confirmed':bool(k[2] and k[3]),'quantity':str(v)} for k,v in sorted(totals.items())],
            'missing':[dict(sku=k[0],flavor=k[1],size=k[2],quantity=v) for k,v in sorted(missing.items())]}
