"""Importación manual y aditiva. No importa clientes ni realiza llamadas de red."""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from erp.domain import DomainError

MAX_IMPORT_BYTES = 256 * 1024
SOURCES = {'toteat-manual', 'manual-demo'}
GROUP_TYPES = {'Tortas enteras': {'bizcocho', 'hojarasca', 'mixta', 'zanahoria'},
               'Dulces enteros': {'pie', 'kuchen', 'cheesecake'}}
FIELDS = {'id', 'idToteat', 'localCode', 'name', 'categoryId', 'category',
          'catalog_group', 'product_type', 'size_people', 'modifier_options',
          'bases', 'recipe_status', 'recipe_note'}


def exact_text(value, label, maximum=80):
    # Rechazar, nunca normalizar silenciosamente un identificador fuente.
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or '**' in value):
        raise DomainError(f'Catálogo: {label} no válido.')
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DomainError('Catálogo: clave JSON duplicada.')
        result[key] = value
    return result


def load_catalog(path):
    with open(path, 'rb') as stream:
        raw = stream.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise DomainError('Catálogo: archivo demasiado grande.')
    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (ValueError, UnicodeDecodeError):
        raise DomainError('Catálogo: JSON no válido.') from None


def validate_catalog(payload):
    if not isinstance(payload, dict) or set(payload) != {'schema_version', 'source', 'source_reference', 'products'}:
        raise DomainError('Catálogo: estructura no válida; no se admiten campos adicionales.')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1 or not isinstance(payload['source'], str) or payload['source'] not in SOURCES:
        raise DomainError('Catálogo: versión u origen no admitidos.')
    source = payload['source']
    reference = exact_text(payload['source_reference'], 'referencia del origen', 300)
    rows = payload['products']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise DomainError('Catálogo: se requieren entre 1 y 200 productos clasificados.')
    products, identities, toteat_ids, codes, pairs, categories = [], set(), set(), set(), set(), {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise DomainError('Catálogo: campos del producto no válidos. No incluyas precios, stock ni credenciales.')
        for key in ('id', 'localCode', 'name', 'categoryId', 'category'):
            exact_text(row[key], key, 160 if key == 'category' else 80)
        if type(row['idToteat']) is not int or not 0 < row['idToteat'] <= 9007199254740991:
            raise DomainError('Catálogo: idToteat debe ser un entero exacto compatible con el navegador.')
        if row['id'] in identities or row['idToteat'] in toteat_ids or row['localCode'] in codes:
            raise DomainError('Catálogo: identidad duplicada dentro del lote.')
        identities.add(row['id']); toteat_ids.add(row['idToteat']); codes.add(row['localCode'])
        if re.search(r'\b(trozo|porci[oó]n|slice|rebanada|individual)\b', row['name'], flags=re.IGNORECASE):
            raise DomainError('Catálogo: producto individual o porción fuera de alcance.')
        group, kind, people = row['catalog_group'], row['product_type'], row['size_people']
        if not isinstance(group, str) or not isinstance(kind, str) or kind not in GROUP_TYPES.get(group, set()):
            raise DomainError('Catálogo: grupo o clasificación fuera de alcance.')
        if group == 'Dulces enteros':
            if people is not None:
                raise DomainError('Catálogo: no se asume tamaño de personas para dulces enteros.')
        elif type(people) is not int or people not in ({10, 20} if kind == 'bizcocho' else {20}):
            raise DomainError('Catálogo: tamaño de torta no confirmado o incompatible.')
        if type(row['modifier_options']) is not int or row['modifier_options'] != 0:
            raise DomainError('Catálogo: este importador requiere productos sin opciones de modificador.')
        size = f'{people} personas' if people is not None else 'Entero · tamaño no confirmado'
        pair = (row['name'], size)
        if pair in pairs:
            raise DomainError('Catálogo: dos identidades comparten producto/formato; revisar antes de importar.')
        pairs.add(pair)
        category = (row['category'], group)
        if row['categoryId'] in categories and categories[row['categoryId']] != category:
            raise DomainError('Catálogo: identidad de categoría en conflicto.')
        categories[row['categoryId']] = category
        bases = row['bases']
        if bases is None:
            if row['recipe_status'] != 'pending':
                raise DomainError('Catálogo: una receta desconocida debe quedar pendiente.')
        else:
            if not isinstance(row['recipe_status'], str) or row['recipe_status'] not in {'confirmed', 'provisional'} or not isinstance(bases, list) or not 1 <= len(bases) <= 10:
                raise DomainError('Catálogo: receta no válida.')
            for base in bases:
                if not isinstance(base, dict) or set(base) != {'name', 'quantity'}:
                    raise DomainError('Catálogo: base no válida.')
                exact_text(base['name'], 'nombre de base')
                try:
                    if not isinstance(base['quantity'], str): raise InvalidOperation
                    quantity = Decimal(base['quantity'])
                    if not quantity.is_finite() or not 0 < quantity <= 999 or quantity.as_tuple().exponent < -3:
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    raise DomainError('Catálogo: cantidad de base no válida.') from None
        exact_text(row['recipe_note'], 'nota de receta', 300)
        # Esta clave pertenece solo al ERP. No sustituye ni fabrica un SKU Toteat.
        sku = 'LOCAL-' + hashlib.sha256((source + '\0' + row['id']).encode()).hexdigest()[:32]
        products.append(dict(sku=sku, flavor=row['name'], size=size, category=kind,
                             catalog_group=group, whole=True, kind='torta', version=0,
                             bases=None if bases is None else [dict(b, size=size) for b in bases],
                             recipe_status=row['recipe_status'], recipe_note=row['recipe_note'],
                             source=source, source_reference=reference, is_demo=source == 'manual-demo',
                             source_product={key: row[key] for key in ('id', 'idToteat', 'localCode', 'name', 'categoryId', 'category', 'modifier_options')},
                             size_people=people))
    return products
