"""Compact, read-only courier summaries from the same audited analytical base."""
import csv
import json
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo
from erp import delivery_chat as chat, delivery_audit as audit
from erp.domain import DomainError


def selection(snapshot, driver):
    requested = [driver] if isinstance(driver, str) else driver
    if not isinstance(requested, (list, tuple)) or not requested or any(not isinstance(d, str) or not d for d in requested):
        raise DomainError('Selecciona al menos un repartidor.')
    allowed = ['all', *[c['driver'] or 'missing' for c in snapshot['couriers']]]
    if any(d not in allowed for d in requested):
        raise DomainError('Repartidor no encontrado.', 404)
    if 'all' in requested and any(d != 'all' for d in requested):
        raise DomainError('Selecciona todos o una combinación de repartidores.')
    return list(dict.fromkeys(requested))


def includes(drivers, driver):
    return 'all' in drivers or (driver or 'missing') in drivers


def view(store, month, driver='all'):
    snapshot = chat.view(store, month)
    if not snapshot['available']:
        raise DomainError('No hay un registro auditado para este mes.', 404)
    drivers = selection(snapshot, driver)
    comparison = audit.view(store, snapshot['month'], snapshot)
    reports = {r['conductor']: r for r in comparison['reports']}
    couriers = [c for c in snapshot['couriers'] if includes(drivers, c['driver'])]
    rows = []
    with store.connect() as db:
        adjustments = [dict(r) for r in db.execute('SELECT * FROM delivery_adjustments WHERE month=? ORDER BY created_at,id', (snapshot['month'],))]
        previous_batch = db.execute('SELECT source_sha256 FROM delivery_chat_batches WHERE month=?', (snapshot['previous_month'],)).fetchone()
        previous = [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM delivery_chat_records WHERE source_sha256=?', (previous_batch[0],))] if previous_batch else None
    for courier in couriers:
        report = reports.get(courier['driver'], {})
        active = [a for a in adjustments if a['driver'] == courier['driver'] and not a['void_at']]
        additions = sum(a['amount'] for a in active if a['kind'] == 'addition')
        deductions = sum(a['amount'] for a in active if a['kind'] == 'deduction')
        principal = report.get('bruto_declarado_informe_clp')
        rows.append({
            'driver': courier['driver'], 'label': courier['driver'] or 'Sin repartidor confirmado',
            **{k: courier[k] for k in ('records', 'base_records', 'base', 'pending', 'unknown_amounts')},
            'held_known': courier['known_gross'] - courier['base'],
            'average_base': courier['metrics']['average_base'],
            'report_amount': principal,
            'report_difference': report.get('difference'),
            'declared_advance': report.get('adelanto_declarado_clp'),
            'declared_balance': report.get('saldo_declarado_clp'),
            'principal': principal, 'additions': additions, 'deductions': deductions,
            'net': None if principal is None else principal + additions - deductions,
            'adjustments': active,
        })
    totals = {k: sum(r[k] for r in rows) for k in ('records', 'base_records', 'base', 'pending', 'unknown_amounts', 'held_known')}
    covered = [r for r in rows if r['principal'] is not None]
    totals.update({k: sum(r[k] for r in covered) if covered else None for k in ('principal', 'net')})
    totals.update({k: sum(r[k] for r in covered) for k in ('additions', 'deductions')})
    totals['missing_reports'] = len(rows) - len(covered)
    totals['unapplied_additions'] = sum(r['additions'] for r in rows if r['principal'] is None)
    totals['unapplied_deductions'] = sum(r['deductions'] for r in rows if r['principal'] is None)
    return {'month': snapshot['month'], 'driver': drivers[0] if len(drivers) == 1 else 'selected', 'drivers': drivers,
            'selection': 'Todos los repartidores' if 'all' in drivers else ', '.join(c['driver'] or 'Sin repartidor confirmado' for c in couriers),
            'generated_at': datetime.now(ZoneInfo('America/Santiago')).isoformat(timespec='seconds'),
            'rows': rows, 'totals': totals, 'adjustments': [a for a in adjustments if includes(drivers, a['driver'])],
            'metrics': chat.metrics([r for r in snapshot['records'] if includes(drivers, r['driver'])], None if previous is None else [r for r in previous if includes(drivers, r['driver'])]),
            'payment_executed': False, 'final_settlement': False,
            'notice': 'Total a pagar = monto bruto de la planilla del repartidor + adicionales - descuentos registrados. La base del chat es solo referencia. Sin planilla, el total queda pendiente. Los anticipos declarados requieren registro manual para descontarse. Importes provisionales; no acredita ejecución de pagos.'}


def csv_bytes(data):
    def safe(value):
        if value is None:
            return 'Sin dato'
        if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + value
        return value
    output = StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Repartidor', 'Base chat (referencia)', 'Monto declarado en planilla', 'Adicionales', 'Descuentos', 'Total a pagar'])
    for r in data['rows']:
        writer.writerow([safe(v) for v in [r['label'], r['base'], r['principal'], r['additions'], r['deductions'],
                                          'Pendiente' if r['net'] is None else r['net']]])
    return output.getvalue().encode('utf-8-sig')


def pdf(data):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    if 'Oveja' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('Oveja', str(Path(__file__).resolve().parents[1] / 'static/fonts/NotoSans.ttf')))
    green = colors.HexColor('#194d38')
    styles = {name: ParagraphStyle(name, fontName='Oveja', fontSize=size, leading=leading, textColor=green, spaceAfter=after)
              for name, size, leading, after in [('title', 25, 31, 12), ('heading', 13, 19, 9), ('body', 9, 14, 8), ('small', 8, 12, 4)]}
    def p(value, style='small'):
        return Paragraph(escape(str(value)), styles[style])
    def money(value):
        return 'Sin dato' if value is None else '$' + f'{value:,.0f}'.replace(',', '.')
    def table(heads, values, widths, total=False):
        t = Table([[p(h) for h in heads]] + [[p(v) for v in row] for row in values], colWidths=widths, repeatRows=1, hAlign='LEFT')
        commands = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f2e9')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                    ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('LINEBELOW', (0, 0), (-1, -1), .3, colors.HexColor('#dedfd7'))]
        if total:
            commands.append(('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e4ece5')))
        t.setStyle(TableStyle(commands))
        return t
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=62, bottomMargin=52,
                            title='Resumen de repartos por repartidor', author='ERP Oveja')
    width = A4[0] - 84
    def chrome(canvas, document):
        canvas.saveState(); canvas.setFillColor(green)
        canvas.rect(0, A4[1] - 8, A4[0], 8, fill=1, stroke=0)
        canvas.setFont('Oveja', 8); canvas.drawString(42, A4[1] - 34, 'OVEJA / COCINA Y CAFÉ')
        canvas.drawRightString(A4[0] - 42, A4[1] - 34, 'PROVISIONAL · SOLO SOCIOS')
        canvas.setFont('Oveja', 7); canvas.drawString(42, 27, data['month'] + ' · Importes en CLP · Sin aprobación de pago')
        canvas.drawRightString(A4[0] - 42, 27, 'Página ' + str(document.page)); canvas.restoreState()
    story = [p('Resumen de repartos', 'title'), p(data['selection'], 'heading'),
             p(f"Período: {data['month']} · Emisión: {data['generated_at'].replace('T', ' ')} · Chile"), Spacer(1, 12),
             p('Importes por repartidor', 'heading')]
    totals = data['totals']
    payable = lambda amount: 'Pendiente' if amount is None else money(amount)
    values = [[r['label'], money(r['base']), money(r['principal']), money(r['additions']), money(r['deductions']), payable(r['net'])] for r in data['rows']]
    covered = [r for r in data['rows'] if r['principal'] is not None]
    values.append(['SUBTOTAL CON PLANILLA' if totals['missing_reports'] else 'TOTAL', money(sum(r['base'] for r in covered)) if covered else 'Sin dato', money(totals['principal']), money(totals['additions']), money(totals['deductions']), payable(totals['net'])])
    story += [table(['Repartidor', 'Base chat (referencia)', 'Monto declarado en planilla', 'Adicionales', 'Descuentos', 'Total a pagar'], values,
                    [width * .24, width * .17, width * .18, width * .12, width * .12, width * .17], total=True),
              Spacer(1, 12), p(data['notice'], 'body')]
    if totals['missing_reports']:
        story += [p(f"{totals['missing_reports']} filas sin planilla: pendientes, fuera del subtotal. Sus adicionales ({money(totals['unapplied_additions'])}) y descuentos ({money(totals['unapplied_deductions'])}) quedan registrados para aplicar al disponer de su planilla.")]
    active = [a for a in data['adjustments'] if not a['void_at']]
    if active:
        story += [Spacer(1, 8), p('Conceptos incorporados al resumen', 'heading'),
                  table(['Repartidor / concepto', 'Tipo', 'Importe', 'Registrado por'],
                        [[a['driver'] + ' / ' + a['concept'], 'Adicional' if a['kind'] == 'addition' else 'Descuento', money(a['amount']), a['actor']] for a in active],
                        [width * .43, width * .15, width * .17, width * .25])]
    story += [Spacer(1, 9), p('Resumen del mes seleccionado. Las comparaciones con chat y Toteat, los conceptos y las evidencias se conservan en la conciliación del ERP.')]
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return stream.getvalue()
