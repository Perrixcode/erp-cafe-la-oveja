"""Proyecciones de diagnóstico: evidencia pública sin valores y originales privados separados."""
from collections import Counter
from copy import deepcopy
import re
from scripts.toteat_diagnostic import describe
from scripts.toteat_open_orders_probe import comment_evidence

COMMENT_FIELDS = ('comment', 'comments', 'notes', 'observations', 'instructions')


def rows(payload):
    if not isinstance(payload, dict) or payload.get('ok') is not True:
        raise ValueError('provider_success_not_confirmed')
    data = payload.get('data')
    if not isinstance(data, list) or len(data) > 5000 or any(not isinstance(row, dict) for row in data):
        raise ValueError('invalid_provider_rows')
    return data


def identity(value):
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and 0 < len(value) <= 100:
        return value
    raise ValueError('invalid_identity')


def table_identity(value):
    # Toteat representa mesas virtuales con enteros negativos. El signo distingue
    # su identidad de una mesa física; nunca normalizar con abs().
    if type(value) is int:
        return str(value)
    return identity(value)


def original_comments(node, path):
    return [{'path': path + '.' + field, 'original': deepcopy(node[field])}
            for field in COMMENT_FIELDS if field in node and node[field] is not None]


def safe_comment_summary(comments):
    result = []
    for item in comments:
        value = item['original']
        # Solo keys seleccionadas: el texto original nunca se copia a este resultado.
        evidence = comment_evidence({'comment': value})
        result.append({'path': item['path'], 'type': type(value).__name__,
                       'length': len(value) if isinstance(value, (str, list, dict)) else None,
                       'nonempty': bool(value.strip()) if isinstance(value, str) else bool(value),
                       'labels': sorted({label for e in evidence for label in e['afiche_labels_detected']})})
    return result


def inspect_sales(payload, catalog, current_order_id):
    transactions = rows(payload)
    index = {key: {} for key in ('id', 'idToteat', 'localCode')}
    for product in catalog:
        owner = identity(product['idToteat'])
        for key in index:
            value = product.get(key)
            if type(value) in (str, int) and str(value):
                index[key].setdefault(str(value), set()).add(owner)
    candidates = []; evidence = []; basis_counts = Counter(); product_types = Counter()
    ambiguous = excluded_extras = same_order = 0
    for row in transactions:
        order_id = identity(row.get('orderId'))
        same_order += order_id == current_order_id
        products = row.get('products', [])
        if not isinstance(products, list):
            raise ValueError('invalid_products')
        matched = []
        for n, product in enumerate(products):
            if not isinstance(product, dict):
                raise ValueError('invalid_product')
            value = product.get('id'); product_types[type(value).__name__] += 1
            if type(value) not in (str, int):
                continue
            basis = [key for key, values in index.items() if str(value) in values]
            owners = set().union(*(index[key][str(value)] for key in basis)) if basis else set()
            if not owners:
                continue
            if len(owners) != 1:
                ambiguous += 1; continue
            reference = product.get('lineReference', product.get('lineReference*'))
            if product.get('isExtra') is True or reference not in (None, 0, '0', ''):
                excluded_extras += 1; continue
            basis_counts.update(basis)
            matched.append({'product_id': value, 'catalog_id': next(iter(owners)), 'match_basis': basis,
                            'quantity': product.get('quantity'), 'line_id': product.get('lineId', product.get('lineId*')),
                            'line_reference': reference,
                            'comments': original_comments(product, f'$.products[{n}]')})
        if not matched:
            continue
        order_comments = original_comments(row, '$')
        payments = row.get('paymentForms', [])
        if not isinstance(payments, list):
            raise ValueError('invalid_payment_forms')
        payment_comments = [item for n, payment in enumerate(payments) if isinstance(payment, dict)
                            for item in original_comments(payment, f'$.paymentForms[{n}]')]
        payment_summary = safe_comment_summary(payment_comments)
        candidate = {'order_id': order_id, 'payment_id': identity(row.get('paymentId')),
                     'date_open': row.get('dateOpen'), 'date_closed': row.get('dateClosed'),
                     'order_comments': order_comments, 'products': matched,
                     'payment_comments_evidence': payment_summary,
                     'classification': 'not_inferred'}
        candidates.append(candidate)
        evidence.append({'same_as_open_test_order': order_id == current_order_id,
                         'has_closed_date': bool(row.get('dateClosed')), 'matching_products': len(matched),
                         'order_comments': safe_comment_summary(order_comments),
                         'product_comments': [entry for p in matched for entry in safe_comment_summary(p['comments'])],
                         'payment_comments': payment_summary})
    report = {'transactions_returned': len(transactions), 'same_open_test_order_transactions': same_order,
              'matching_transactions': len(candidates), 'matching_unique_orders': len({c['order_id'] for c in candidates}),
              'ambiguous_product_matches': ambiguous, 'excluded_extra_matches': excluded_extras,
              'match_basis_counts': dict(basis_counts), 'product_id_types': dict(product_types),
              'transaction_schema': describe(transactions[0]) if transactions else None,
              'matching_comment_evidence': evidence, 'imported_operational_orders': 0}
    return report, candidates


def inspect_tables(payload, table_ids):
    data = rows(payload); wanted = {table_identity(value) for value in table_ids}
    selected = []; evidence = []
    for row in data:
        table_id = table_identity(row.get('tableId'))
        if table_id not in wanted:
            continue
        comments = original_comments(row, '$')
        # El nombre de mesa se conserva con su propia semántica, nunca como comentario de orden.
        display = {key: row[key] for key in ('tableName', 'name', 'displayName', 'orderName', 'description')
                   if isinstance(row.get(key), str)}
        extra_text = {key: deepcopy(value) for key, value in row.items()
                      if isinstance(key, str) and re.search(r'comment|note|observ|instruction', key, re.I)
                      and key not in COMMENT_FIELDS and not re.search(r'token|secret|password|auth', key, re.I)}
        selected.append({'table_id': table_id, 'display_fields': display, 'table_comments': comments,
                         'unmapped_table_comment_fields': extra_text, 'classification': 'not_inferred'})
        evidence.append({'schema': describe(row), 'display_fields': {k: {'length': len(v), 'multiline': '\n' in v}
                                                                     for k, v in display.items()},
                         'table_comments': safe_comment_summary(comments),
                         'unmapped_comment_field_count': len(extra_text)})
    return {'tables_returned': len(data), 'target_table_count': len(wanted), 'matching_tables': len(selected),
            'matching_table_evidence': evidence, 'table_name_is_order_comment': False,
            'imported_operational_orders': 0}, selected
