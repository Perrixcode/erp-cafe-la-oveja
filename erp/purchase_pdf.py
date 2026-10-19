"""Read-only supplier-facing purchase draft; excludes financial/account information."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from erp import payables as ap,procurement as pc,purchase_agenda


def payload(db,kind,key):
    row=purchase_agenda.view(db,key) if kind=='agenda' else pc.order_view(db,key)
    supplier=db.execute('SELECT name,tax_id,contact FROM ap_suppliers WHERE id=?',(row['supplier_id'],)).fetchone()
    profile=db.execute('SELECT order_contact FROM pc_supplier_profiles WHERE supplier_id=?',(row['supplier_id'],)).fetchone()
    lines=[]
    for line in row['lines']:
        if kind=='agenda':
            unit=db.execute('SELECT name FROM pc_units WHERE id=?',(line['unit_id'],)).fetchone() if line.get('unit_id') else None
            lines.append(dict(description=line['description'],quantity=line['quantity'],unit=unit[0] if unit else 'Por confirmar'))
        else:
            unit=line['unit_code']
            if line['unit_id']!=line['base_unit_id']:
                base=db.execute('SELECT code FROM pc_units WHERE id=?',(line['base_unit_id'],)).fetchone()
                unit+=' · '+pc.decimal(line['base_ratio_micros'])+' '+base[0]
            lines.append(dict(description=line['item_name'],quantity=line['ordered'],unit=unit))
    return dict(identifier=('AG-' if kind=='agenda' else 'OC-')+str(key),supplier=supplier['name'],tax_id=supplier['tax_id'],contact=(profile[0] if profile else '') or supplier['contact'],
                date=row['scheduled_on'] if kind=='agenda' else row['ordered_on'],expected=row['expected_on'],notes=row['notes'],lines=lines,
                linked_order=row.get('order_id') if kind=='agenda' else None,cancelled=bool(row.get('cancelled_at')))


def render(data):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    if 'OvejaPurchase' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('OvejaPurchase',str(Path(__file__).resolve().parents[1]/'static/fonts/NotoSans.ttf')))
    body=ParagraphStyle('body',fontName='OvejaPurchase',fontSize=9,leading=13,textColor=colors.HexColor('#25352e'),wordWrap='CJK')
    title=ParagraphStyle('title',parent=body,fontSize=25,leading=30,spaceAfter=8)
    heading=ParagraphStyle('heading',parent=body,fontSize=13,leading=18,spaceAfter=8)
    muted=ParagraphStyle('muted',parent=body,textColor=colors.HexColor('#657466'),fontSize=8,leading=12)
    p=lambda value,style=body:Paragraph(escape(str(value or '')).replace('\n','<br/>'),style)
    stream=BytesIO();doc=SimpleDocTemplate(stream,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=42,bottomMargin=54,title='Pedido '+data['identifier'],author='Oveja')
    width=A4[0]-84
    story=[p('OVEJA',title),p(('PEDIDO ANULADO' if data['cancelled'] else 'BORRADOR DE PEDIDO')+' · '+data['identifier'],heading),p('Proveedor: '+data['supplier'])]
    if data['tax_id']:story.append(p('RUT: '+data['tax_id']))
    if data['contact']:story.append(p('Contacto de pedidos: '+data['contact']))
    story.extend([Spacer(1,12),p('Fecha del pedido: '+data['date']),p('Entrega solicitada: '+(data['expected'] or 'Por confirmar'))])
    if data.get('linked_order'):story.append(p('Pedido vinculado: OC-'+str(data['linked_order'])))
    story.append(Spacer(1,18))
    rows=[[p('CANTIDAD'),p('UNIDAD / FORMATO'),p('PRODUCTO')]]
    rows.extend([[p(l['quantity']),p(l['unit']),p(l['description'])] for l in data['lines']])
    if len(rows)==1:rows.append([p('—'),p('Por confirmar'),p('Sin productos detallados. Completar antes de enviar.')])
    table=Table(rows,colWidths=[65,125,width-190],repeatRows=1,hAlign='LEFT',splitByRow=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7ecdf')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#dce2d7'))]))
    story.append(table)
    if data['notes']:story.extend([Spacer(1,18),p('Notas del pedido',heading),p(data['notes'])])
    story.extend([Spacer(1,18),p('Documento para revisión. Exportarlo no envía ni confirma el pedido al proveedor.',muted)])
    def page(canvas,document):
        canvas.saveState();canvas.setFillColor(colors.white);canvas.rect(0,0,A4[0],A4[1],fill=1,stroke=0)
        canvas.setFont('OvejaPurchase',8);canvas.setFillColor(colors.HexColor('#657466'))
        canvas.drawString(42,30,'OVEJA · '+data['identifier']+' · '+('ANULADO' if data['cancelled'] else 'BORRADOR'))
        canvas.drawRightString(A4[0]-42,30,'Página '+str(document.page));canvas.restoreState()
    doc.build(story,onFirstPage=page,onLaterPages=page)
    return stream.getvalue()


def export(db,kind,key):
    data=payload(db,kind,key)
    return render(data),'pedido-'+data['identifier'].lower()+'.pdf'
