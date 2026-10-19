"""Read-only daily payment evidence. No ledger, payment or attachment mutations."""
import hashlib
import json
import re
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from erp import payables as ap
from erp.domain import DomainError

PREFIX = '/api/suppliers/payment-receipts'
MAX_PAYMENTS = 100
MAX_FILES = 40
MAX_BYTES = 32 * 1024 * 1024
MAX_PAGES = 120
MAX_PIXELS = 20_000_000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def snapshot(db, day):
    day = ap.day(day or ap.local_day(), 'Fecha de pago')
    rows = []
    for source, sql in (
        ('legacy', '''SELECT p.*,s.name supplier_name,s.id supplier_id,i.folio invoice_folio,i.document_type
          FROM ap_payments p JOIN ap_invoices i ON i.id=p.invoice_id JOIN ap_suppliers s ON s.id=i.supplier_id
          WHERE p.paid_on=? AND p.voided_at IS NULL ORDER BY p.id'''),
        ('account', '''SELECT p.*,s.name supplier_name FROM ap_account_entries p
          JOIN ap_suppliers s ON s.id=p.supplier_id
          WHERE p.occurred_on=? AND p.kind='payment' AND p.voided_at IS NULL ORDER BY p.id''')):
        for r in db.execute(sql, (day,)):
            key = ('PH-' if source == 'legacy' else 'CC-') + str(r['id'])
            if source == 'legacy':
                invoices = [dict(id=r['invoice_id'], folio=r['invoice_folio'], document_type=r['document_type'], amount=r['amount'])]
                table, field = 'ap_files', 'payment_id'
            else:
                invoices = [dict(x) for x in db.execute('''SELECT i.id,i.folio,i.document_type,SUM(a.amount) amount
                  FROM ap_account_applications a JOIN ap_invoices i ON i.id=a.invoice_id
                  WHERE a.entry_id=? AND a.voided_at IS NULL GROUP BY i.id ORDER BY i.id''', (r['id'],))]
                table, field = 'ap_account_files', 'entry_id'
            files = [dict(f) for f in db.execute(f'SELECT id,name,mime,sha256,bytes,actor,created_at FROM {table} WHERE {field}=? ORDER BY created_at DESC,id DESC', (r['id'],))]
            rows.append(dict(key=key, source=source, id=r['id'], supplier_id=r['supplier_id'], supplier_name=r['supplier_name'],
                             amount=r['amount'], date=day, method=r['method'], reference=r['reference'], actor=r['actor'],
                             created_at=r['created_at'], invoices=invoices, unapplied=r['amount']-sum(i['amount'] for i in invoices),
                             files=files, proof_state='available' if files else 'missing'))
    rows.sort(key=lambda r: (r['supplier_name'].casefold(), r['key']))
    undated = db.execute("SELECT COUNT(*) FROM ap_payments WHERE paid_on='' AND voided_at IS NULL").fetchone()[0]
    value = dict(date=day, payments=rows, undated_payments=undated, limits=dict(payments=MAX_PAYMENTS, files=MAX_FILES, megabytes=32, pages=MAX_PAGES))
    value['token'] = digest(value)
    return value


def listing(store, day=None):
    with store.connect() as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return snapshot(db, day)


def inspect_file(raw, meta):
    """Validate decoded contents, not just the uploader's MIME or extension."""
    from PIL import Image, ImageOps
    from pypdf import PdfReader
    if len(raw) != meta['bytes'] or hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('integridad del archivo no verificada')
    if meta['mime'] == 'application/pdf':
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ValueError('PDF protegido con contraseña')
        if not 0 < len(reader.pages) <= MAX_PAGES:
            raise ValueError(f'PDF sin páginas o con más de {MAX_PAGES} páginas')
        for page in reader.pages:
            # Decode content streams now: malformed pages must not fail after selection.
            if page.get_contents() is not None:
                page.get_contents().get_data()
            if page.mediabox.width <= 0 or page.mediabox.height <= 0:
                raise ValueError('tamaño de página no válido')
        return dict(kind='pdf', raw=raw, pages=len(reader.pages))
    if meta['mime'] not in ('image/png', 'image/jpeg'):
        raise ValueError('formato no admitido')
    with Image.open(BytesIO(raw)) as image:
        expected = 'PNG' if meta['mime'] == 'image/png' else 'JPEG'
        if image.format != expected or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('formato de imagen no coincide o contiene varias imágenes')
        if image.width * image.height > MAX_PIXELS or max(image.size) > 12000:
            raise ValueError('imagen supera el límite de 20 megapíxeles o 12000 píxeles por lado')
        image.load()
        fixed = ImageOps.exif_transpose(image).convert('RGBA')
        white = Image.new('RGB', fixed.size, 'white')
        white.paste(fixed, mask=fixed.getchannel('A'))
        stream = BytesIO()
        white.save(stream, format='PNG')
        return dict(kind='image', raw=stream.getvalue(), pages=1, size=white.size)


def prepare(store, day, keys, token, choices=None):
    choices = choices or {}
    if not isinstance(choices, dict) or set(choices)-set(keys) or any(type(v) is not int or v < 1 for v in choices.values()):
        raise DomainError('Selecciona versiones válidas para los pagos elegidos.')
    if not keys or len(keys) > MAX_PAYMENTS or len(set(keys)) != len(keys) or any(not re.fullmatch(r'(PH|CC)-[1-9][0-9]*', k) for k in keys):
        raise DomainError(f'Selecciona entre 1 y {MAX_PAYMENTS} pagos distintos.')
    with store.connect() as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        data = snapshot(db, day)
        if token != data['token']:
            raise DomainError('Los pagos o sus comprobantes cambiaron. Actualiza la lista y vuelve a revisar la selección.', 409)
        selected = [r for r in data['payments'] if r['key'] in keys]
        if len(selected) != len(keys):
            raise DomainError('La selección contiene pagos ajenos a la fecha o anulados.', 409)
        evidence, cache, consumed, pages = {}, {}, 0, 0
        for row in selected:
            row['warnings'], row['selected_file'], row['attachment'] = [], None, None
            table, field = ('ap_files', 'payment_id') if row['source'] == 'legacy' else ('ap_account_files', 'entry_id')
            row['chosen_explicitly'] = row['key'] in choices
            candidates = [m for m in row['files'] if m['id'] == choices[row['key']]] if row['chosen_explicitly'] else row['files'][:1]
            if row['chosen_explicitly'] and not candidates:
                raise DomainError('El respaldo elegido no pertenece al pago.', 409)
            for meta in candidates:
                sha = meta['sha256']
                if (table, meta['id']) not in cache:
                    consumed += meta['bytes']
                    if consumed > MAX_BYTES:
                        raise DomainError('Los archivos a revisar superan 32 MB. Reduce la selección.', 413)
                    raw = bytes(db.execute(f'SELECT content FROM {table} WHERE id=? AND {field}=?', (meta['id'], row['id'])).fetchone()[0])
                    try:
                        cache[(table, meta['id'])] = inspect_file(raw, meta)
                    except Exception as exc:
                        # Never expose parser internals, file contents or local paths.
                        reason = str(exc) if type(exc) is ValueError else 'no se pudo abrir el contenido completo'
                        cache[(table, meta['id'])] = dict(error=reason)
                item = cache[(table, meta['id'])]
                if 'error' in item:
                    label = "Comprobante elegido no legible" if row["chosen_explicitly"] else "Último comprobante no legible"
                    row['warnings'].append(f"{label}: adjunto #{meta['id']} ({meta['created_at']}): {item['error']}. Respaldo pendiente; puedes elegir expresamente una versión anterior.")
                    continue
                if sha not in evidence:
                    pages += item['pages']
                    if len(evidence) >= MAX_FILES or pages > MAX_PAGES:
                        raise DomainError(f'La selección supera {MAX_FILES} comprobantes o {MAX_PAGES} páginas de respaldo. Reduce la selección; no se han omitido páginas.', 413)
                    evidence[sha] = dict(item, number=len(evidence)+1, sha256=sha, payments=[])
                evidence[sha]['payments'].append(row['key'])
                row['selected_file'] = meta
                row['attachment'] = evidence[sha]['number']
                break
            if row['files'] and row['selected_file'] is None:
                row['warnings'].append('Sin comprobante incorporable. Los originales permanecen en el ERP.')
        return dict(date=data['date'], token=data['token'], payments=selected, evidence=list(evidence.values()),
                    generated_at=datetime.now(ZoneInfo('America/Santiago')).isoformat(timespec='seconds'))


def render_pdf(data):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject
    from reportlab.platypus import Image
    from erp.supplier_finance_pdf import Report, day as date_label, money
    out = PdfWriter()
    report = Report('Comprobantes de pagos del día', data['date'])
    rows = data['payments']
    report.story.append(report.text('Pagos del '+date_label(data['date']), 'title'))
    report.gap()
    report.cards([('Total seleccionado · CLP', sum(r['amount'] for r in rows))])
    missing = sum(r['selected_file'] is None for r in rows)
    report.story.append(report.text(f"{len(rows)} pagos · {len(data['evidence'])} comprobantes únicos · {missing} pagos sin respaldo incorporable."))
    report.story.append(report.text('Generado: '+data['generated_at']+' · hora de Chile', 'small'))
    report.gap()
    report.story.append(report.text('Consulta de registros existentes; no registra pagos ni certifica ejecución bancaria. Las facturas vinculadas son las vigentes al generar este documento. Un anticipo puede no tener folio aplicado.'))
    report.gap()
    table = [[report.text(s, 'small') for s in ('Pago / proveedor', 'Facturas vinculadas', 'Importe', 'Respaldo')]]
    for row in rows:
        folios = ', '.join(ap.TYPES.get(i['document_type'], 'Documento')+' '+i['folio'] for i in row['invoices'][:5]) or 'Sin aplicación'
        if len(row['invoices']) > 5:
            folios += f"\nY {len(row['invoices'])-5} folios; detalle después del resumen."
        if row['unapplied']:
            folios += '\nSin aplicar: '+money(row['unapplied'])
        status = 'Anexo '+str(row['attachment']) if row['attachment'] else 'Sin comprobante'
        table.append([report.text(row['key']+'\n'+row['supplier_name']), report.text(folios, 'small'), report.number(row['amount'], 85), report.text(status, 'small')])
    report.table(table, [155, report.width-310, 85, 70])
    report.gap()
    for row in rows:
        if len(row['invoices']) > 5:
            report.story.append(report.text(row['key']+' · facturas vinculadas', 'heading'))
            for invoice in row['invoices']:
                report.story.append(report.text(ap.TYPES.get(invoice['document_type'], 'Documento')+' '+invoice['folio']+' · aplicado '+money(invoice['amount']), 'small'))
            report.gap()
    report.story.append(report.text('Adjuntos: se intenta incorporar el último adjunto, o una versión seleccionada expresamente. Si no puede abrirse, el respaldo queda pendiente. La apertura técnica no acredita que sea el comprobante correcto. Las versiones anteriores se conservan en el ERP.', 'small'))
    for row in rows:
        if row['warnings']:
            report.gap(6)
            report.story.append(report.text(row['key']+': '+' '.join(row['warnings']), 'small'))
    out.append(PdfReader(BytesIO(report.finish())), import_outline=False)
    for item in data['evidence']:
        cover = Report('Anexo '+str(item['number'])+' · comprobante', data['date'])
        cover.story.append(cover.text('Anexo '+str(item['number']), 'title'))
        cover.gap()
        cover.story.append(cover.text(f"{item['pages']} página(s) de respaldo · incluido una sola vez."))
        for row in rows:
            if row['key'] not in item['payments']:
                continue
            meta = row['selected_file']
            cover.gap()
            cover.story.append(cover.text(row['key']+' · '+row['supplier_name']+' · '+money(row['amount']), 'heading'))
            cover.story.append(cover.text(ap.METHODS.get(row['method'], row['method'])+' · referencia: '+(row['reference'] or 'Sin referencia')))
            cover.story.append(cover.text("Versión seleccionada expresamente por quien exporta." if row["chosen_explicitly"] else "Último adjunto registrado; sin elección de versión anterior.", "small"))
            cover.story.append(cover.text(f"Archivo #{meta['id']}: {meta['name']} · {meta['created_at']} · cargado por {meta['actor']}. {len(row['files'])} versión(es) conservadas.", 'small'))
        cover.gap()
        cover.story.append(cover.text('SHA-256: '+item['sha256'], 'small'))
        start = len(out.pages)
        out.append(PdfReader(BytesIO(cover.finish())), import_outline=False)
        out.add_outline_item('Anexo '+str(item['number'])+' · '+', '.join(item['payments']), start)
        if item['kind'] == 'pdf':
            reader = PdfReader(BytesIO(item['raw']), strict=True)
            reader.add_form_topname('anexo_'+str(item['number']))
            # Keep native pages, crop boxes, rotation and form appearances; no rasterization.
            out.append(reader, import_outline=False)
        else:
            image_report = Report('Anexo '+str(item['number'])+' · imagen', data['date'])
            w, h = item['size']
            scale = min(image_report.width/w, (image_report.size[1]-150)/h)
            image_report.story.append(Image(BytesIO(item['raw']), width=w*scale, height=h*scale))
            out.append(PdfReader(BytesIO(image_report.finish())), import_outline=False)
    # Export passive evidence. Retain visible appearances but remove embedded actions.
    for obj in out._objects:
        if isinstance(obj, DictionaryObject):
            for key in ('/AA', '/A', '/OpenAction', '/JS', '/JavaScript', '/RichMediaContent', '/RichMediaSettings'):
                obj.pop(key, None)
    out.add_metadata({'/Title':'Comprobantes de pagos · '+data['date'], '/Author':'Oveja', '/Subject':'Consulta sin movimientos financieros'})
    stream = BytesIO()
    out.write(stream)
    if len(stream.getvalue()) > 40*1024*1024 or len(out.pages) > 240:
        raise DomainError('El PDF consolidado supera el límite de 40 MB o 240 páginas. Reduce la selección.', 413)
    return stream.getvalue()


def handle(handler, store, path, query):
    if handler.command != 'GET':
        raise DomainError('La exportación es de solo lectura.', 405)
    if path not in (PREFIX, PREFIX+'.pdf', PREFIX+'/preview') or set(query)-{'date','ids','token','files'} or any(len(v)!=1 for v in query.values()):
        raise DomainError('Consulta de comprobantes no válida.')
    day = query.get('date', [None])[0]
    if path == PREFIX:
        return handler.send(200, listing(store, day))
    try:
        choices = json.loads(query.get('files', ['{}'])[0])
    except (ValueError, TypeError):
        raise DomainError('La selección de versiones no es válida.') from None
    data = prepare(store, day, query.get('ids', [''])[0].split(','), query.get('token', [''])[0], choices)
    if path == PREFIX+'/preview':
        return handler.send(200, {k:v for k,v in data.items() if k != 'evidence'})
    return handler.send(200, render_pdf(data), 'application/pdf', {'Content-Disposition':'inline; filename="comprobantes-pagos-'+data['date']+'.pdf"'})
