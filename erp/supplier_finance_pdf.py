"""Read-only PDFs of canonical supplier balances and the existing filtered journal."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from erp import payables as ap

KINDS={'all':'Todos los movimientos','invoices':'Facturas y anulaciones','payments':'Pagos y anulaciones','credit_notes':'Notas de crédito y anulaciones','applications':'Aplicaciones y reversos'}


def money(amount):
    return ('−' if amount<0 else '')+ap.money(abs(amount)).replace('$','$ ')


def day(value):
    return '/'.join(reversed(value.split('-'))) if value else 'Sin fecha'


def pending_data(store):
    """One canonical snapshot; credits stay with their supplier, never net across suppliers."""
    data=ap.view(store);groups=[]
    for supplier in data['suppliers']:
        invoices=[r for r in data['invoices'] if r['supplier_id']==supplier['id'] and not r['voided_at']]
        pending=sorted((r for r in invoices if not r['payment_review'] and r['balance']>0),key=lambda r:(not bool(r['due_on']),r['due_on'],r['issued_on'],r['id']))
        review=sorted((r for r in invoices if r['payment_review']),key=lambda r:(r['issued_on'],r['id']))
        available=supplier['available_credit']
        if not pending and not review and not available:continue
        balance=sum(r['balance'] for r in pending)
        net=balance-available
        groups.append(dict(supplier_id=supplier['id'],supplier_name=supplier['name'],tax_id=supplier['tax_id'],invoices=pending,review=review,
                           amount=sum(r['amount'] for r in pending),paid=sum(r['paid'] for r in pending),credited=sum(r['credited'] for r in pending),
                           balance=balance,available_credit=available,net_balance=net,payable=max(net,0),in_favor=max(-net,0),
                           consistent=balance==supplier['balance']))
    totals={key:sum(g[key] for g in groups) for key in ('amount','paid','credited','balance','available_credit','payable','in_favor')}
    totals.update(invoices=sum(len(g['invoices']) for g in groups),review=sum(len(g['review']) for g in groups),suppliers=len(groups))
    return dict(today=data['today'],groups=groups,totals=totals)


class Report:
    """Reuse ReportLab and the same bundled Noto Sans as the ERP's existing PDFs."""
    def __init__(self,title,today,wide=False):
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import A4,landscape
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate
        self.colors=colors;self.pdfmetrics=pdfmetrics
        self.green=colors.HexColor('#233d32');self.muted=colors.HexColor('#607066');self.pale=colors.HexColor('#eef2e9');self.line=colors.HexColor('#d9e1d5')
        self.font='OvejaFinance'
        if self.font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(self.font,str(Path(__file__).resolve().parents[1]/'static/fonts/NotoSans.ttf')))
        self.styles={}
        for name,size,leading in [('body',9,13),('small',8,11),('title',23,29),('heading',12,17)]:
            self.styles[name]=ParagraphStyle('finance-'+name,fontName=self.font,fontSize=size,leading=leading,textColor=self.green,spaceAfter=0,splitLongWords=True)
        self.styles['small'].textColor=self.muted
        self.styles['heading'].keepWithNext=True
        self.styles['number']=ParagraphStyle('finance-number',parent=self.styles['body'],alignment=TA_RIGHT)
        self.size=landscape(A4) if wide else A4;self.width=self.size[0]-84
        self.stream=BytesIO();self.title=title;self.today=today
        self.doc=SimpleDocTemplate(self.stream,pagesize=self.size,leftMargin=42,rightMargin=42,topMargin=82,bottomMargin=52,title=title,author='Oveja')
        self.story=[]

    def text(self,value,style='body'):
        from reportlab.platypus import Paragraph
        return Paragraph(escape(str(value)).replace('\n','<br/>'),self.styles[style])

    def number(self,value,width):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph
        label=money(value)
        size=min(9,9*(width-14)/max(1,self.pdfmetrics.stringWidth(label,self.font,9)))
        style=ParagraphStyle('fitted-money',parent=self.styles['number'],fontSize=size,leading=max(11,size+3))
        return Paragraph(escape(label),style)

    def gap(self,height=12):
        from reportlab.platypus import Spacer
        self.story.append(Spacer(1,height))

    def cards(self,values):
        from reportlab.platypus import Table,TableStyle,Paragraph
        from reportlab.lib.styles import ParagraphStyle
        width=self.width/len(values);cells=[]
        for label,value in values:
            label_value=money(value);size=min(23,23*(width-26)/max(1,self.pdfmetrics.stringWidth(label_value,self.font,23)))
            style=ParagraphStyle('card-amount',parent=self.styles['body'],fontSize=size,leading=size+6,spaceBefore=7)
            cells.append([self.text(label,'small'),Paragraph(escape(label_value),style)])
        table=Table([cells],colWidths=[width]*len(values),hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),self.pale),('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(-1,-1),.5,self.line),('INNERGRID',(0,0),(-1,-1),.5,self.line),('LEFTPADDING',(0,0),(-1,-1),13),('RIGHTPADDING',(0,0),(-1,-1),13),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),14)]))
        self.story.append(table);self.gap(14)

    def table(self,rows,widths,headers=1,group=False,total=False,compact=False):
        from reportlab.platypus import Table,TableStyle
        table=Table(rows,colWidths=widths,repeatRows=headers,hAlign='LEFT',splitByRow=1,splitInRow=0)
        style=[('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6 if compact else 8),('BOTTOMPADDING',(0,0),(-1,-1),6 if compact else 8),('LINEBELOW',(0,headers-1),(-1,-1),.4,self.line),('BACKGROUND',(0,headers-1),(-1,headers-1),self.pale)]
        if group:style.extend([('SPAN',(0,0),(-1,0)),('BACKGROUND',(0,0),(-1,0),self.colors.HexColor('#e2e9db'))])
        if total:style.extend([('BACKGROUND',(0,-2),(-1,-1),self.pale),('SPAN',(0,-1),(-1,-1)),('NOSPLIT',(0,-3),(-1,-1))])
        table.setStyle(TableStyle(style));self.story.append(table)

    def finish(self):
        def page(canvas,doc):
            w,h=self.size;canvas.saveState();canvas.setFillColor(self.colors.white);canvas.rect(0,0,w,h,fill=1,stroke=0)
            canvas.setFillColor(self.green);canvas.setFont(self.font,19);canvas.drawString(42,h-40,'OVEJA')
            canvas.setFont(self.font,8);canvas.setFillColor(self.muted);canvas.drawRightString(w-42,h-32,self.title)
            canvas.drawRightString(w-42,h-46,'Consulta: '+day(self.today)+' · CLP')
            canvas.setStrokeColor(self.line);canvas.line(42,h-61,w-42,h-61);canvas.line(42,40,w-42,40)
            canvas.setFont(self.font,7.5);canvas.drawString(42,26,'ERP Oveja · Información de cuenta corriente · Montos en pesos chilenos')
            canvas.drawRightString(w-42,26,'Página '+str(doc.page));canvas.restoreState()
        self.doc.build(self.story,onFirstPage=page,onLaterPages=page)
        return self.stream.getvalue()


def statement_pdf(data):
    r=Report('Cartola de cuenta corriente',data['today']);p=data['period'];t=data['totals']
    r.story.extend([r.text('Cartola de cuenta corriente','title'),r.text(data['supplier']['name'],'heading')]);r.gap(5)
    if data['supplier']['tax_id']:r.story.append(r.text('RUT '+data['supplier']['tax_id'],'small'))
    r.story.append(r.text('Período: '+(day(p['start']) if p['start'] else 'Desde el inicio')+' — '+(day(p['end']) if p['end'] else 'Hasta la consulta')+' · '+KINDS[p['kind']],'small'));r.gap(16)
    r.cards([('Saldo al inicio del intervalo',p['opening_balance']),('Saldo al final del intervalo',p['closing_balance']),('Saldo actual de la cuenta',t['net_balance'])])
    r.story.append(r.text('Saldo negativo: crédito a favor de Oveja. No implica una devolución de dinero confirmada.','small'))
    r.gap(10)
    r.story.append(r.text(str(p['shown_count'])+' movimientos visibles · Cargos '+money(p['shown_debit'])+' · Abonos '+money(p['shown_credit']),'heading'))
    r.story.append(r.text('El saldo acumulado incluye todos los tipos de movimiento, incluso los ocultos por el filtro. Aplicar un crédito a una factura no vuelve a registrar un abono.','small'));r.gap(10)
    widths=[64,r.width-64-3*79,79,79,79]
    rows=[[r.text(x,'small') for x in ['FECHA','DOCUMENTO / MOVIMIENTO','CARGO','ABONO','SALDO']]]
    invoices={i['id']:i for i in data['invoices']}
    for item in data['journal']:
        label=item['label'];invoice=invoices.get(item.get('invoice_id'))
        if item['kind']=='invoice' and invoice and invoice['due_on']:label+='\nVence '+day(invoice['due_on'])
        if item.get('actor'):label+='\nRegistrado por '+item['actor']
        if item['kind']=='payment':
            entry=next(e for e in data['entries'] if e['id']==item['entry_id'])
            label+='\nComprobante: '+(', '.join(f['name'] for f in entry['files']) or 'Sin adjunto')
        rows.append([r.text(day(item['date']),'small'),r.text(label),r.number(item['debit'],79),r.number(item['credit'],79),r.number(item['balance'],79)])
    if len(rows)==1:rows.append([r.text('—'),r.text('Sin movimientos para los filtros aplicados.'),r.number(0,79),r.number(0,79),r.text('—')])
    r.table(rows,widths,compact=True);r.gap(16)
    summary_start=len(r.story)
    r.story.append(r.text('Posición actual del proveedor','heading'));r.gap(6)
    r.cards([('Deuda pendiente',t['outstanding']),('Saldo a nuestro favor disponible',t['available_credit'])])
    r.story.append(r.text('Facturas pendientes validadas: '+money(t['outstanding'])+' · Créditos sin aplicar: '+money(t['available_credit'])+' · '+('A favor de Oveja: '+money(t['in_favor']) if t['in_favor'] else 'Neto por pagar: '+money(t['payable']))))
    notices=['La cartola utiliza los valores vigentes de los documentos; las correcciones pueden cambiar los saldos de fechas anteriores.']
    if p['undated_count']:notices.append(str(p['undated_count'])+' movimientos sin fecha: se conservan al inicio de la cuenta, sin inventarles una fecha.')
    review=sum(bool(i['payment_review']) and not i['voided_at'] for i in data['invoices'])
    if review:notices.append(str(review)+' facturas con saldo por validar no se incluyen en los cargos ni saldos de esta cartola.')
    if not t['consistent']:notices.append('ADVERTENCIA: los movimientos y el saldo actual presentan una diferencia; revisa la cuenta antes de usarla para pagos.')
    for note in notices:r.gap(6);r.story.append(r.text(note,'small'))
    # Keep the current position and its explanation together on the same page.
    from reportlab.platypus import KeepTogether
    r.story[summary_start:]=[KeepTogether(r.story[summary_start:])]
    return r.finish()


def pending_pdf(data):
    r=Report('Pendientes por proveedor',data['today'],wide=True);t=data['totals']
    r.story.extend([r.text('Pendientes por proveedor','title'),r.text('Todos los proveedores · Saldos vigentes a la fecha de consulta','small')]);r.gap(16)
    r.cards([('Pendiente validado de facturas',t['balance']),('Créditos sin aplicar',t['available_credit']),('Neto por pagar',t['payable'])])
    r.story.append(r.text(str(t['invoices'])+' facturas pendientes · '+str(t['suppliers'])+' proveedores con pendientes, créditos o saldos por validar · '+str(t['review'])+' facturas por validar','small'))
    r.story.append(r.text('Los créditos se descuentan solo dentro de su proveedor. Saldos a favor de Oveja: '+money(t['in_favor'])+'. Los documentos por validar no suman deuda confirmada.','small'));r.gap(16)
    widths=[r.width-2*70-4*102,70,70,102,102,102,102]
    for group in data['groups']:
        name=group['supplier_name']+(' · RUT '+group['tax_id'] if group['tax_id'] else '')
        rows=[[r.text(name,'heading')]+['']*6,[r.text(x,'small') for x in ['DOCUMENTO','EMISIÓN','VENCIMIENTO','MONTO FACTURA','PAGOS APLICADOS','NC APLICADAS','PENDIENTE']]]
        for item in group['invoices']:
            label=ap.TYPES[item['document_type']]+' '+item['folio']
            if item['payment_hold']:label+='\nPago en pausa'
            elif item['overdue']:label+='\nVencida'
            if item['scheduled_on']:label+='\nProgramada '+day(item['scheduled_on'])
            rows.append([r.text(label),r.text(day(item['issued_on']),'small'),r.text(day(item['due_on']) if item['due_on'] else 'Por confirmar','small'),*[r.number(item[k],102) for k in ('amount','paid','credited','balance')]])
        for item in group['review']:
            rows.append([r.text(ap.TYPES[item['document_type']]+' '+item['folio']+'\nPOR VALIDAR · no suma saldo'),r.text(day(item['issued_on']),'small'),r.text(day(item['due_on']) if item['due_on'] else 'Por confirmar','small'),r.number(item['amount'],102),r.text('Por validar','small'),r.text('Por validar','small'),r.text('Por validar','small')])
        if not group['invoices'] and not group['review']:rows.append([r.text('Sin facturas pendientes; conserva crédito disponible.')]+['']*6)
        rows.append([r.text('Subtotal validado','heading'),'','',*[r.number(group[k],102) for k in ('amount','paid','credited','balance')]])
        summary='Créditos sin aplicar: '+money(group['available_credit'])+' · '+('A favor de Oveja: '+money(group['in_favor']) if group['in_favor'] else 'Neto por pagar: '+money(group['payable']))
        if not group['consistent']:summary+='\nADVERTENCIA: hay una diferencia en el saldo de este proveedor; revisa su cuenta corriente.'
        rows.append([r.text(summary)]+['']*6)
        r.table(rows,widths,headers=2,group=True,total=True)
        r.gap(18)
    if not data['groups']:r.story.append(r.text('No hay facturas pendientes, créditos sin aplicar ni documentos por validar.','heading'));r.gap(16)
    r.story.append(r.text('Total general','heading'));r.gap(7)
    r.cards([('Pendiente validado de facturas',t['balance']),('Neto por pagar a proveedores',t['payable'])])
    r.story.append(r.text('Pagos y notas de crédito aplicados ya están descontados de cada factura. Los créditos disponibles se muestran aparte y no se asignan automáticamente. Exportar este informe no registra pagos ni modifica movimientos.','small'))
    return r.finish()
