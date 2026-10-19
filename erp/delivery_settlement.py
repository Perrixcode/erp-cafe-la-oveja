"""Read-only provisional settlement projection and private, generated PDF."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from erp import delivery_chat as chat, delivery_audit as audit
from erp.domain import DomainError


def view(store, month, driver='all'):
    from erp.delivery_summary import view as summary_view, includes
    summary=summary_view(store,month,driver)
    drivers=summary['drivers']
    snapshot=chat.view(store,month)
    rows=[r for r in snapshot['records'] if includes(drivers,r['driver'])]
    totals=chat.totals(rows)
    comparison=audit.view(store,snapshot['month'],snapshot)
    reports=[r for r in comparison['reports'] if includes(drivers,r['conductor'])]
    cases=[c for c in comparison['cases'] if 'all' in drivers or any(d!='missing' and d in c['conductores_relacionados'] for d in drivers)]
    days=[dict(day=d['day'],**chat.totals([r for r in rows if r['sent_at'].startswith(d['day'])])) for d in snapshot['days']]
    return {'month':snapshot['month'],'driver':summary['driver'],'drivers':drivers,'driver_label':summary['selection'],
            'generated_at':datetime.now(ZoneInfo('America/Santiago')).isoformat(timespec='seconds'),
            'totals':dict(totals,held_known=totals['known_gross']-totals['base'],**{k:summary['totals'][k] for k in ('principal','additions','deductions','net','missing_reports','unapplied_additions','unapplied_deductions')}),
            'adjustments':[a for a in summary['adjustments'] if not a['void_at']],
            'days':days,'records':rows,'reports':reports,'cases':cases,'incidents':snapshot['incidents'],
            'source_sha256':snapshot['source_sha256'],'payment_executed':False,'final_settlement':False,
            'notice':summary['notice']}


def pdf(data):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
    if 'Oveja' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('Oveja',str(Path(__file__).resolve().parents[1]/'static/fonts/NotoSans.ttf')))
    green=colors.HexColor('#194d38');cream=colors.HexColor('#f6f4ea');muted=colors.HexColor('#657469')
    styles={name:ParagraphStyle(name,fontName='Oveja',fontSize=size,leading=leading,textColor=color,spaceAfter=after)
            for name,size,leading,color,after in [('body',9,14,green,7),('small',7.5,11,muted,5),('title',27,34,green,12),('h2',14,20,green,10)]}
    def p(text,style='body'):return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    def money(value):return 'Sin dato' if value is None else '$'+f'{value:,.0f}'.replace(',','.')
    def table(headers,rows,widths):
        values=[[p(h,'small') for h in headers]]+[[p(v,'small') for v in row] for row in rows]
        t=Table(values,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),cream),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,0),.7,green),('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#dedfd7'))]))
        return t
    stream=BytesIO();doc=SimpleDocTemplate(stream,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=66,bottomMargin=55,title='Liquidación provisional de repartos',author='ERP Oveja')
    width=A4[0]-84
    def chrome(canvas,document):
        canvas.saveState();canvas.setFillColor(green);canvas.rect(0,A4[1]-8,A4[0],8,fill=1,stroke=0)
        canvas.setFont('Oveja',9);canvas.drawString(42,A4[1]-36,'OVEJA / COCINA Y CAFÉ')
        canvas.setFont('Oveja',8);canvas.drawRightString(A4[0]-42,A4[1]-36,'PROVISIONAL · SOLO SOCIOS')
        canvas.setStrokeColor(colors.HexColor('#dedfd7'));canvas.line(42,42,A4[0]-42,42)
        canvas.setFillColor(muted);canvas.setFont('Oveja',7)
        canvas.drawString(42,28,f"{data['month']} · Sin aprobación ni ejecución de pago")
        canvas.drawRightString(A4[0]-42,28,f'Página {document.page}');canvas.restoreState()
    t=data['totals'];story=[p('Liquidación de\nrepartidores','title'),p(data['driver_label'],'h2'),p(f"Período {data['month']} · Emisión {data['generated_at'].replace('T',' ')} · Chile",'small'),Spacer(1,10),
        table(['Monto declarado en planilla','Base chat (referencia)','Subtotal con planilla' if t['missing_reports'] else 'Total a pagar'],[[money(t['principal']),money(t['base']),'Pendiente' if t['net'] is None else money(t['net'])]],[width*.34,width*.33,width*.33]),Spacer(1,10),
        p(f"Adicionales: {money(t['additions'])} · Descuentos registrados: {money(t['deductions'])}. Calculados para repartidores con planilla."),
        p(data['notice']),p(f"Referencia chat: {t['records']} fichas candidatas, {t['pending']} pendientes y {t['unknown_amounts']} importes sin confirmar. Estos registros no alteran el monto principal de la planilla."),Spacer(1,8)]
    if t['missing_reports']:
        story.extend([p(f"{t['missing_reports']} filas sin planilla: total pendiente, fuera del subtotal. Adicionales por aplicar: {money(t['unapplied_additions'])}; descuentos por aplicar: {money(t['unapplied_deductions'])}."),Spacer(1,8)])
    if data['adjustments']:
        story.extend([p('Conceptos registrados por socios','h2'),table(['Repartidor / concepto','Tipo','Importe','Responsable'],
            [[a['driver']+' / '+a['concept'],'Adicional' if a['kind']=='addition' else 'Descuento',money(a['amount']),a['actor']] for a in data['adjustments']],
            [width*.43,width*.15,width*.17,width*.25]),Spacer(1,8)])
    if data['reports']:
        story.append(p('Informes individuales declarados','h2'))
        for r in data['reports']:
            story.extend([p(f"{r['conductor']} · {r['cantidad_importes_informe']} tarifas · {money(r['bruto_declarado_informe_clp'])}"),
                p(f"Base chat: {money(r['chat_base'])} · Diferencia informe - base: {money(r['difference'])}. {r['detalle_diferencia']}",'small')])
            if r.get('adelanto_declarado_clp') is not None:
                story.append(p(f"Adelanto declarado: {money(r['adelanto_declarado_clp'])}, sin comprobante validado ni descuento automático. Saldo declarado: {money(r.get('saldo_declarado_clp'))}, no aprobado.",'small'))
        story.append(p('Los informes corroboran reclamaciones; no demuestran ejecución. Toteat es global y no se atribuye individualmente.','small'))
    story.extend([Spacer(1,8),p('Referencia chat · detalle diario','h2'),table(['Fecha','Candidatas / base','Base chat','Retenido conocido','Sin importe'],
        [[d['day'],f"{d['records']} / {d['base_records']}",money(d['base'] if d['records'] else None),money(d['known_gross']-d['base'] if d['records'] else None),str(d['unknown_amounts'])] for d in data['days']],[85,100,110,120,width-415])])
    if data['cases'] or data['incidents']:
        story.extend([PageBreak(),p('Incidencias y revisión pendiente','title')])
        for c in data['cases']:
            story.append(KeepTogether([p(f"{c['fecha']} · {c['caso_id']} · {money(c.get('importe_clp'))}",'h2'),p(c['hallazgo']),p(c['evidencia_pendiente'],'small'),p(f"Estado: {c['estado']} · Referencias chat: {c['chat_registros_id']}",'small'),Spacer(1,7)]))
        story.append(p('Mensajes contextuales del mes · fuera de totales','h2'))
        for i in data['incidents']:story.append(p(f"{i['date']} · {i['label']}",'small'))
    story.extend([PageBreak(),p('Fichas del registro','title'),p('Identificadores ordinales de auditoría del export, no IDs comerciales de pedidos. Cliente y dirección se consultan en la evidencia privada del ERP.','small'),
        table(['Referencia / fecha','Repartidor','Importe','Tratamiento original'],[[r['id']+'\n'+r['sent_at'].replace('T',' '),r['driver'] or 'Sin confirmar',money(r['amount_clp']),r['status_label']] for r in data['records']],[140,100,85,width-325]),Spacer(1,14),p('Fuente chat SHA-256: '+data['source_sha256'],'small')])
    doc.build(story,onFirstPage=chrome,onLaterPages=chrome)
    return stream.getvalue()
