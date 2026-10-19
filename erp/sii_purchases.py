"""Importación local de CSV de compras. Nunca se autentica ni escribe en el SII."""
import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime
from erp import payables as ap
from erp.domain import DomainError
from erp.store import now
from erp.payable_duplicates import DuplicateIndex, folio as identity_folio
from erp import sii_review

ALIASES = {
    'tax_id': ('rut proveedor', 'rut emisor', 'rut contraparte'),
    'name': ('razon social', 'razon social emisor', 'razon social proveedor'),
    'folio': ('folio', 'numero de factura', 'nro documento'),
    'document_type': ('tipo doc', 'tipo documento', 'tipo de documento', 'tipo dte'),
    'issued_on': ('fecha docto', 'fecha documento', 'fecha emision'),
    'amount': ('monto total', 'total'),
}
TYPES = {'33': 'invoice', '34': 'exempt'}
DOCUMENTS = {'33': ('invoice', 'Factura electrónica'),
             '34': ('invoice', 'Factura exenta electrónica'),
             '61': ('credit_note', 'Nota de crédito electrónica'),
             '56': ('debit_note', 'Nota de débito electrónica')}


def document_identity(row):
    # Retain the printed folio separately from the comparable numeric identity.
    return row['tax_id'], row.get('sii_type', row['document_type']), identity_folio(row['folio'])


def document_totals(rows):
    result = {kind: {'count': 0, 'amount': 0} for kind in ('invoice', 'credit_note', 'debit_note', 'other')}
    for row in rows:
        if row['status'] == 'error':
            continue
        total = result[row.get('document_kind', 'other')]
        total['count'] += 1
        total['amount'] += row['amount']
    return result


def normalized(value):
    value = ''.join(c for c in unicodedata.normalize('NFD', value.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', value).split())


def attach_tax_detail(rows, row, header, names, values, indexes):
    """Recognize an adjacent RCV tax-only continuation without creating debt."""
    if not rows or rows[-1]['status'] not in ('new', 'unsupported'):
        return False
    fields = dict(zip(names, (v.strip() for v in values)))
    tax_fields = ('codigo otro impuesto', 'valor otro impuesto', 'tasa otro impuesto')
    if fields.get('nro') != '' or row['amount'] != '':
        return False
    if not re.fullmatch(r'\d+', fields.get(tax_fields[0], '')) or not re.fullmatch(r'\d+', fields.get(tax_fields[1], '')):
        return False
    if not re.fullmatch(r'\d+(?:[.,]\d+)?', fields.get(tax_fields[2], '')):
        return False
    allowed = {'nro', 'tipo compra', 'fecha recepcion', 'fecha acuse', *tax_fields,
               *(names[index] for index in indexes.values())}
    if any(value for name, value in fields.items() if name not in allowed):
        return False
    parent = rows[-1]
    for key, index in indexes.items():
        if key != 'amount' and values[index].strip() != parent['original'][header[index]].strip():
            return False
    parent.setdefault('tax_details', []).append({'row': row['row'], 'original': row['original']})
    return True


def parse(content):
    if not 0 < len(content) <= ap.LIMIT:
        raise DomainError('El CSV debe pesar hasta 8 MB.', 413)
    try:
        decoded = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            decoded = content.decode('cp1252')
        except UnicodeDecodeError:
            raise DomainError('La codificación del CSV no es compatible. Descarga el archivo nuevamente.') from None
    if '\x00' in decoded:
        raise DomainError('Se requiere el archivo CSV del detalle de compras, no un Excel ni un PDF.')
    first = decoded.splitlines()[0] if decoded.splitlines() else ''
    delimiter = ';' if first.count(';') > first.count(',') else ','
    try:
        reader = csv.reader(io.StringIO(decoded), delimiter=delimiter, strict=True)
        header = next(reader)
        names = [normalized(v) for v in header]
        indexes = {}
        for key, aliases in ALIASES.items():
            matches = [i for i, name in enumerate(names) if name in aliases]
            if len(matches) != 1:
                raise DomainError('No se reconoce una columna única de '+aliases[0]+'. Usa el CSV del detalle de compras con encabezados.')
            indexes[key] = matches[0]
        rows = []
        record_count = 0
        for number, values in enumerate(reader, 2):
            if not any(v.strip() for v in values):
                continue
            record_count += 1
            if record_count > 3000:
                raise DomainError('Importa hasta 3.000 filas por archivo.')
            # RCV exports can end each data row with a separator absent from the
            # header. Ignore only empty surplus cells; never shift columns or
            # discard a populated cell. The complete CSV remains in the batch.
            if len(values) > len(header) and all(not v.strip() for v in values[len(header):]):
                values = values[:len(header)]
            row = {'row': number, 'original': dict(zip(header, values)), 'status': 'new', 'issue': ''}
            if len(values) != len(header):
                row.update(status='error', issue=f'La fila tiene {len(values)} columnas y el encabezado {len(header)}. Revisa el archivo; no se ajustaron sus datos.')
            else:
                data = {key: values[index].strip() for key, index in indexes.items()}
                row.update(data)
                if attach_tax_detail(rows, row, header, names, values, indexes):
                    continue
                try:
                    row['tax_id'] = ap.tax_id(data['tax_id'])
                    if not row['tax_id']:
                        raise DomainError('Falta el RUT del proveedor.')
                    row['name'] = ap.text(data['name'], 'Razón social', True, 160)
                    if not re.fullmatch(r'\d{1,18}', data['folio']):
                        raise DomainError('Folio no válido.')
                    row['folio'] = data['folio']
                    if not re.fullmatch(r'\d{1,12}', data['amount']):
                        raise DomainError('Monto total no válido: se esperan pesos enteros, sin separadores.')
                    row['amount'] = ap.integer(int(data['amount']), 'Monto total')
                    try:
                        row['issued_on'] = datetime.strptime(data['issued_on'], '%d/%m/%Y').date().isoformat()
                    except ValueError:
                        try:
                            row['issued_on'] = datetime.strptime(data['issued_on'], '%d-%m-%Y').date().isoformat()
                        except ValueError:
                            row['issued_on'] = ap.day(data['issued_on'], 'Emisión')
                    row['sii_type'] = data['document_type']
                    row['document_kind'], row['document_label'] = DOCUMENTS.get(data['document_type'], ('other', 'Otro documento'))
                    row['identity'] = list(document_identity(row))
                    # Validate optional monetary cells without inferring tax
                    # arithmetic: RCV can contain special taxes and retentions.
                    for label, value in row['original'].items():
                        key = normalized(label)
                        if value.strip() and (key.startswith('monto ') or key in ('iva activo fijo', 'iva uso comun', 'impto sin derecho a credito', 'iva no retenido', 'tabacos puros', 'tabacos cigarrillos', 'tabacos elaborados', 'valor otro impuesto')):
                            if not re.fullmatch(r'\d{1,12}', value.strip()):
                                raise DomainError(label+': se esperan pesos enteros no negativos, sin separadores.')
                    if data['document_type'] not in TYPES:
                        row.update(status='unsupported', issue=(row['document_label']+': requiere documento original y referencia revisada. No se incorpora como deuda ni se aplica automáticamente a una factura.' if row['document_kind'] in ('credit_note', 'debit_note') else 'Tipo '+data['document_type']+': requiere revisión; no se incorpora automáticamente.'))
                    else:
                        row['document_type'] = TYPES[data['document_type']]
                except DomainError as error:
                    row.update(status='error', issue=str(error))
            rows.append(row)
    except (csv.Error, StopIteration, UnicodeError):
        raise DomainError('No se pudo leer el CSV. Conserva los encabezados y descarga nuevamente el detalle de compras.') from None
    if not rows:
        raise DomainError('El CSV no contiene documentos.')
    # Repeated identities require a reviewed exception; at most one can be used.
    groups = {}
    for row in rows:
        if row['status'] in ('new', 'unsupported'):
            groups.setdefault(document_identity(row), []).append(row)
    for group in groups.values():
        if len(group) > 1:
            for row in group:
                row.update(status='conflict' if row['status']=='new' else row['status'], file_duplicates=[r['row'] for r in group], issue=row['issue']+' Documento repetido dentro del mismo archivo; elige solo una de sus filas al revisar.')
    return rows


def classify(db, rows):
    unidentified = list(db.execute("SELECT id,name FROM ap_suppliers WHERE tax_id=''"))
    index = DuplicateIndex(db)
    for row in rows:
        if row['status'] in ('error', 'unsupported') or (row['status'] != 'new' and not row.get('file_duplicates')):
            continue
        matches = index.find(row)
        exact = next((match for match in matches if match['exact'] and not match['historical_only']), None)
        if exact:
            current = db.execute('SELECT * FROM ap_invoices WHERE id=?', (exact['invoice_id'],)).fetchone()
            same = current['amount'] == row['amount'] and current['issued_on'] == row['issued_on'] and not current['voided_at']
            row.update(status='duplicate' if same else 'conflict', invoice_id=current['id'],
                       matches=matches, issue=f"{exact['state']}: ya existe en el ERP; se conserva su saldo, pagos e historial." if same else 'Existe con diferencias, correcciones o anulación. Revisa el documento sin reemplazarlo.')
        elif matches:
            row.update(status='conflict', possible_duplicate=True, matches=matches,
                       issue='Posible duplicado en los registros o la carga inicial, incluidas facturas pagadas. Falta confirmar RUT o folio. Revisa la coincidencia antes de agregar otra factura; no se fusionará automáticamente.')
            if all(match['historical_only'] for match in matches):
                row.update(historical_only=True, issue='El folio figura solo en el historial de una factura corregida. Revisa el folio actual y confirma explícitamente si este es otro documento; no se vinculará automáticamente.')
        else:
            # Never merge an existing supplier without RUT based on its name alone.
            collisions = [dict(r) for r in unidentified if normalized(r['name']) == normalized(row['name'])]
            if collisions:
                row.update(status='conflict', supplier_matches=collisions, issue='Existe un proveedor con este nombre y sin RUT. Completa su RUT o revisa la coincidencia antes de incorporar otro proveedor.')
        if row['status'] in ('duplicate', 'conflict'):
            row['review_action'] = 'link' if exact else 'create'
            row['can_override'] = True
            existing = []
            for match in matches:
                if match['invoice_id']:
                    current = dict(db.execute('SELECT * FROM ap_invoices WHERE id=?', (match['invoice_id'],)).fetchone())
                    current['paid'] = ap.paid(db, current['id'])
                    current['credited'] = ap.credited(db, current['id'])
                    match.update(current_amount=current['amount'], paid=current['paid'],
                                 balance=0 if current['voided_at'] else current['amount']-current['paid']-current['credited'])
                    existing.append(current)
            proof = dict(row=row['row'], tax_id=row['tax_id'], document_type=row['document_type'],
                         folio=row['folio'], amount=row['amount'], issued_on=row['issued_on'],
                         action=row['review_action'], matches=matches, existing=existing,
                         suppliers=row.get('supplier_matches', []), file_duplicates=row.get('file_duplicates', []))
            row['review_token'] = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return rows


def preview(store, batch):
    with store.connect() as db:
        db.execute('BEGIN')
        source = sii_review.source(db, batch)
        rows = classify(db, parse(source['content']))
        linked = {r[0] for r in db.execute('SELECT source_row FROM ap_invoice_sources WHERE batch_id=? UNION SELECT source_row FROM ap_import_resolutions WHERE batch_id=?', (batch, batch))}
        for row in rows:
            if row['row'] in linked:
                row.update(already_imported=True, can_override=False,
                           issue='Esta fila ya fue incorporada o vinculada. Se conserva la factura existente y sus pagos.')
        review = sii_review.decorate(db, batch, rows)
        return dict(id=batch, name=source['name'], rows=rows, review=review,
                    document_totals=document_totals(rows),
                    tax_detail_count=sum(len(r.get('tax_details', [])) for r in rows),
                    counts={status: sum(r['status'] == status for r in rows) for status in ('new', 'duplicate', 'conflict', 'unsupported', 'error')})


def upload(store, content, name, actor):
    rows = parse(content)
    key = hashlib.sha256(content).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO ap_import_batches VALUES(?,?,?,?,?)', (key, ap.text(name, 'Archivo', True, 160), content, actor, now()))
        sii_review.source(db, key)
        if not sii_review.state(db, key)['version']:
            sii_review.write(db, key, rows, {}, actor, 1)
    return preview(store, key)


def confirm(store, batch, data, actor):
    rows = data.get('rows')
    if not isinstance(rows, list) or not rows or len(rows) > 3000 or any(type(r) is not int for r in rows) or len(set(rows)) != len(rows):
        raise DomainError('Selecciona filas nuevas y válidas de la vista previa.')
    if data.get('confirmed') is not True:
        raise DomainError('Confirma que corresponde al registro de compras de la empresa.')
    conditions = data.get('conditions', {})
    selected_keys = {str(number) for number in rows}
    if not isinstance(conditions, dict) or any(key not in selected_keys for key in conditions):
        raise DomainError('Las condiciones deben corresponder solo a las filas seleccionadas.')
    for value in conditions.values():
        ap.choice(value, ap.CONDITIONS, 'Condición de compra')
    overrides = data.get('overrides', {})
    if not isinstance(overrides, dict) or any(key not in selected_keys for key in overrides):
        raise DomainError('Las excepciones deben corresponder a filas seleccionadas.')
    for value in overrides.values():
        if not isinstance(value, dict) or value.get('confirmed') is not True:
            raise DomainError('Confirma cada excepción de duplicado.')
        ap.text(value.get('reason'), 'Motivo de la excepción', True)
        ap.text(value.get('token'), 'Revisión de la coincidencia', True, 64)
        ap.choice(value.get('action'), ('create', 'link'), 'Acción de la excepción')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        source = sii_review.source(db, batch)
        review = sii_review.state(db, batch)
        sii_review.active(review)
        if 'review_version' in data:
            ap.integer(data['review_version'], 'Versión de la revisión', minimum=0)
            if data['review_version'] != review['version'] and not set(rows) <= sii_review.linked_rows(db, batch):
                raise DomainError('El avance cambió. Recarga el archivo antes de confirmar.', 409)
        discarded = set(json.loads(review['draft_json']).get('discarded', []))
        candidates = {r['row']: r for r in classify(db, parse(source['content']))}
        selected = []
        already = 0
        identities = set()
        for number in rows:
            row = candidates.get(number)
            linked = db.execute('SELECT invoice_id FROM ap_invoice_sources WHERE batch_id=? AND source_row=? UNION SELECT invoice_id FROM ap_import_resolutions WHERE batch_id=? AND source_row=?', (batch, number, batch, number)).fetchone()
            if linked:
                already += 1
                continue
            if number in discarded:
                raise DomainError('Restaura la fila descartada antes de importarla.', 409)
            override = overrides.get(str(number))
            if not row:
                raise DomainError('La vista previa cambió o incluye filas no importables. Actualiza la importación.', 409)
            if override:
                if not row.get('can_override') or override['action'] != row['review_action'] or override['token'] != row['review_token']:
                    raise DomainError('Cambió la coincidencia o sus pagos. Vuelve a revisar y confirmar la excepción.', 409)
            elif row['status'] != 'new':
                raise DomainError('Revisa y confirma la excepción de cada coincidencia antes de importar.', 409)
            identity = document_identity(row)
            if identity in identities:
                raise DomainError('Selecciona solo una fila por factura. El mismo documento aparece más de una vez.', 409)
            identities.add(identity)
            selected.append(row)
        imported = linked_count = 0
        for row in selected:
            override = overrides.get(str(row['row']))
            if override and override['action'] == 'link':
                invoice = ap.record(db, 'ap_invoices', row['invoice_id'])
                supplier = invoice['supplier_id']
                linked_count += 1
            else:
                existing = db.execute('SELECT id FROM ap_suppliers WHERE tax_id=?', (row['tax_id'],)).fetchone()
                if existing:
                    supplier = existing['id']
                else:
                    supplier = ap.mutate(db, 'supplier', {'name': row['name'], 'tax_id': row['tax_id']}, actor, None)['id']
                condition = conditions.get(str(row['row']), 'unknown')
                # Rechecked above under the same write lock, including exceptions.
                invoice = ap.mutate(db, 'invoice', dict(supplier_id=supplier, document_type=row['document_type'], folio=row['folio'], issued_on=row['issued_on'], amount=row['amount'], condition=condition), actor, None, check_duplicates=False)
                db.execute('UPDATE ap_invoices SET payment_review=1 WHERE id=?', (invoice['id'],))
                db.execute('INSERT INTO ap_invoice_sources VALUES(?,?,?,?,?,?,?,?,?)', (invoice['id'], batch, row['row'], json.dumps(row['original'], ensure_ascii=False),row['tax_id'],row['document_type'],row['folio'],row['amount'],row['issued_on']))
                ap.event(db, 'sii_imported', actor, after={'batch': batch, 'row': row['row'], 'condition': condition, 'payment_review': True}, supplier=supplier, invoice=invoice['id'])
                imported += 1
            if override:
                evidence = dict(candidate={key: row[key] for key in ('tax_id', 'document_type', 'folio', 'amount', 'issued_on', 'name')},
                                matches=row.get('matches', []), supplier_matches=row.get('supplier_matches', []),
                                file_duplicates=row.get('file_duplicates', []), token=row['review_token'])
                reason = override['reason'].strip()
                db.execute('INSERT INTO ap_import_resolutions VALUES(?,?,?,?,?,?,?,?)',
                           (batch, row['row'], invoice['id'], override['action'], reason, actor, json.dumps(evidence, ensure_ascii=False), now()))
                ap.event(db, 'sii_source_linked' if override['action']=='link' else 'sii_duplicate_overridden', actor, reason,
                         before=evidence, after={'batch': batch, 'row': row['row'], 'invoice_id': invoice['id'], 'action': override['action']},
                         supplier=supplier, invoice=invoice['id'])
        if imported or linked_count:
            sii_review.after_confirm(db, batch, list(candidates.values()), actor)
        return {'imported': imported, 'linked': linked_count, 'already_imported': already}
