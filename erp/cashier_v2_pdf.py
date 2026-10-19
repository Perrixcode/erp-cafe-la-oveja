"""Private preview/download of exact signed V2 snapshots. Rendering never changes state."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, Flowable, PageBreak
from erp.domain import DomainError
from erp.cash_closure_pdf import money, local_time
from erp import cashier_v2 as v2

class Signature(Flowable):
    def __init__(self,lines):
        super().__init__();self.lines=lines;self.width=160*mm;self.height=45*mm
    def draw(self):
        self.canv.setStrokeColor(colors.HexColor('#244f41'));self.canv.setLineWidth(1.1)
        for line in self.lines:
            if len(line)<2:continue
            path=self.canv.beginPath();path.moveTo(line[0]['x']*self.width,(1-line[0]['y'])*self.height)
            for p in line[1:]:path.lineTo(p['x']*self.width,(1-p['y'])*self.height)
            self.canv.drawPath(path)

def render(db,value,submission_id=None):
    if submission_id:
        row=db.execute('SELECT * FROM cv2_submissions WHERE id=? AND closure_id=?',(submission_id,value['id'])).fetchone()
        if not row:raise DomainError('Versión de presentación no encontrada.',404)
        entry=v2.verify_submission(db,row);snapshot=entry['snapshot'];signature=entry['signature']
    else:
        snapshot=value['frozen_submission'] if value['status']=='submitted' else None
        signature=value['signature'] if snapshot else None
    d=snapshot['declaration'] if snapshot else {k:value[k] for k in v2.DECLARATION_FIELDS};s=snapshot['summary'] if snapshot else value['summary']
    font='OvejaV2'
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,str(Path(__file__).resolve().parents[1]/'static/fonts/NotoSans.ttf')))
    styles=getSampleStyleSheet()
    for name in ('Normal','Heading1','Heading2','Title'):styles[name].fontName=font
    styles['Normal'].fontSize=9;styles['Normal'].leading=13
    p=lambda text,style='Normal':Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    def table(rows):
        t=Table([[p(c) for c in row] for row in rows],repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5ece7')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.3,colors.lightgrey)]));return t
    reg=db.execute('SELECT name FROM cl_registers WHERE id=?',(d['identity']['register_id'],)).fetchone()[0]
    flow=[p('OVEJA · CIERRE DIGITAL V2','Title'),p('PRUEBA SEMANAL · conservar impresos'),p(snapshot['folio'] if snapshot else 'BORRADOR · SIN PRESENTACIÓN NI FIRMA','Heading1'),
          p(d['identity']['business_date']+' · '+reg+' · '+value['responsible']['name']),p('Bolsa '+d['bagNumber']),Spacer(1,5*mm),
          table([['A · Total contado','B · Siguiente turno','C · Bolsa A − B'],[money(s['A']),money(s['B']),money(s['C'])]]),
          p('A es el conteo físico final; los movimientos incorporados no se vuelven a ajustar.'),
          p('Comparación de efectivo: esperado '+money(s['expected'])+' · diferencia '+money(s['difference'])),p('Pendiente significa que falta una fuente comparable; no es una falla de la cajera.'),
          p('Conteo por denominación','Heading2'),table([['Denominación','Cantidad A','Cantidad B']]+[[money(int(k)),a if a is not None else 'Pendiente',d['retained'][k] if d['retained'][k] is not None else 'Pendiente'] for k,a in d['cash'].items()]),
          p('Mercado Pago por terminal','Heading2')]
    for i,t in enumerate(d['terminals']):
        c=s['terminals'][i];flow.append(p(t['terminal_id']+' · total del papel '+money(t['total'])+' · referencia '+money(c['expected'])+' · diferencia '+money(c['difference'])))
        flow.append(p('Período Chile: '+local_time(t['period_start'])+' a '+local_time(t['period_end'])))
    if not d['terminals']:flow.append(p('Sin terminales declarados; total pendiente, no cero.'))
    flow.extend([p('Comentario','Heading2'),p(d['note'] or 'Sin comentario de descuadre; no había comparación distinta de cero que lo exigiera.')])
    for kind,reason in d['missing_evidence_reasons'].items():
        if reason:flow.append(p('Respaldo pendiente '+kind+': '+reason))
    if signature:
        flow.extend([p('Conformidad y firma de esta versión','Heading2'),Signature(signature['strokes']),p(signature['signer_name']+' · '+local_time(signature['signed_at'])+' · versión '+str(signature['version'])),p('SHA-256 del snapshot: '+signature['snapshot_hash']),p(signature['notice'])])
    flow.extend([p('Eventos posteriores · separados de la declaración firmada','Heading2'),p(value['deposit_status'])])
    for e in value['events']:
        if e['kind'] in ('deposit_safe','late_comparison','signature_invalidated_for_revision'):
            flow.append(p(local_time(e['created_at'])+' · '+e['kind']+' · '+str(e['actor'])))
            if e['kind']=='late_comparison':flow.append(p('Comparación posterior de efectivo: '+money(e['detail']['comparison'].get('difference'))+'; no modifica esta declaración.'))
    flow.append(p('Entrega: '+str((value.get('delivery') or {}).get('state') or 'Pendiente')+' · Conteo administrativo: '+money((value.get('bag') or {}).get('verified_amount'))))
    for meta in d['photos']:
        a=db.execute('SELECT content FROM cl_attachments WHERE id=? AND closure_id=?',(meta['id'],value['id'])).fetchone()
        if not a:raise DomainError('Respaldo ausente.',409)
        flow.extend([PageBreak(),p('Respaldo '+meta['kind']+' · '+meta['name'],'Heading2'),p('Legibilidad declarada: '+('sí' if meta['legible'] else 'pendiente'))])
        image=Image(BytesIO(a[0]));scale=min(170*mm/image.imageWidth,210*mm/image.imageHeight);image.drawWidth=image.imageWidth*scale;image.drawHeight=image.imageHeight*scale;flow.append(image)
    out=BytesIO();SimpleDocTemplate(out,pagesize=letter,leftMargin=17*mm,rightMargin=17*mm,topMargin=15*mm,bottomMargin=15*mm,title='Cierre V2 · Oveja').build(flow)
    return out.getvalue()
