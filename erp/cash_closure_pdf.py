"""Versioned Letter report, preview first in the UI. No state changes on render."""
from io import BytesIO
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
from erp.cash_closure_calc import DENOMINATIONS,LABELS,TZ

def money(value):return 'Sin información' if value is None else '$'+format(value,',').replace(',','.')
def local_time(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(TZ).strftime('%d/%m/%Y %H:%M') if value else 'Pendiente'
def render(view):
 font='OvejaClosure'
 if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,str(Path(__file__).resolve().parents[1]/'static/fonts/NotoSans.ttf')))
 styles=getSampleStyleSheet()
 for name in ('Normal','Heading1','Heading2','Title'):styles[name].fontName=font
 styles['Normal'].fontSize=8;styles['Normal'].leading=11
 out=BytesIO();doc=SimpleDocTemplate(out,pagesize=letter,rightMargin=16*mm,leftMargin=16*mm,topMargin=15*mm,bottomMargin=17*mm,title='Cierre digital · Oveja')
 def p(t,style='Normal'):return Paragraph(escape(str(t)).replace('\n','<br/>'),styles[style])
 def table(rows,widths=None):
  value=Table([[p(c) for c in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
  value.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5eade')),('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#60735c')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#d9ddd3'))]));return value
 c=view['calculation'];d=view['data'];bag=view.get('bag')
 def person(key):return view['responsible']['name'] if key==view['responsible']['key'] else str(key or '').removeprefix('erp:')
 state={'draft':'Borrador','submitted':'Presentado','pending':'Pendiente de conciliación','reconciled':'Conciliado','prepared':'Preparada','delivered':'Entregada','received':'Recibida','counted':'Contabilizada','difference':'Con diferencia','reserved':'Reservada','unlinked':'Sin bolsa'}
 flow=[p('OVEJA · CIERRE DIGITAL','Title'),p('MARCHA BLANCA · conservar papel como respaldo'),Spacer(1,4*mm),
  p(f"{datetime.strptime(view['business_date'],'%Y-%m-%d').strftime('%d/%m/%Y')} · {view['register_name']} · {view['shift_name']}",'Heading1'),
  p(f"Versión {view['version']} · Estado: {state[view['status']]} · ID {view['id']}"),
  p('Responsable original: '+view['responsible']['name']+' · Creador: '+person(view['creator_key'])+' · Último editor: '+person(view['last_editor'])),
  p('Fechas y horas de Chile. Referencia externa: '+str(d.get('external_id') or 'Sin referencia')),
  p('Conteo: '+local_time(d['counted_at'])+' · Sellado: '+local_time(d['sealed_at'])),
  Spacer(1,4*mm),table([['A · Conteo','B · Fondo','Ajuste posterior','C · Bolsa'],[money(c['A']),money(c['B']),money(c['post_count_net']),money(c['C'])]],[46*mm]*4),
  p('C = A + movimientos posteriores documentados − B. Total al sellar: '+money(c['cash_at_seal'])),
  p('Esperado: '+money(c['expected'])+' · Diferencia A − esperado: '+money(c['difference'])+' · Evidencia: '+('Revisada' if c['coverage']=='reviewed' else 'Incompleta')),
  p('Bolsa: '+(bag['label'] if bag else 'Sin vincular')+' · Entrega: '+state[view['delivery_status']]+' · Contabilización de efectivo: '+money(bag.get('verified_amount') if bag else None)),
  p('Contabilizado por '+str(bag.get('verified_by_name') or person(bag.get('verified_by'))) if bag and bag.get('verified_amount') is not None else 'Contabilización de efectivo pendiente.'),
  p('Conteo por denominaciones','Heading2'),table([['Valor','Cantidad A','Subtotal A','Cantidad B','Subtotal B']]+[[money(v),str(d['cash'][str(v)]) if d['cash'][str(v)] is not None else 'Pendiente',money(v*d['cash'][str(v)]) if d['cash'][str(v)] is not None else 'Sin información',str(d['retained'][str(v)]) if d['retained'][str(v)] is not None else 'Pendiente',money(v*d['retained'][str(v)]) if d['retained'][str(v)] is not None else 'Sin información'] for v in DENOMINATIONS],[30*mm,29*mm,47*mm,29*mm,49*mm]),
  p('Medios de pago','Heading2'),table([['Medio','Total cobrado','Estado']]+[[LABELS[m['key']]+(' · '+m['name'] if m['name'] else ''),money(m['total']),'Pendiente' if m['total'] is None else 'No usado' if m['used'] is False else 'Declarado'] for m in c['methods']],[85*mm,50*mm,49*mm]),
  p('Un contraste de terminal o transferencias no acredita abono bancario. El efectivo Toteat reutilizado ya es neto de vuelto; no se resta de nuevo.'),
  p('Apertura y origen','Heading2'),p('Apertura: '+money(d['opening']['amount'])+' · Corte: '+local_time(d['opening']['as_of'])+' · '+str(d['opening']['reference'] or 'Sin evidencia')),
  p('Ventas efectivo: '+{'manual_report':'Transcripción de reporte','toteat_sales':'Ventas de Toteat guardadas'}[d['cash_source']['type']]+' · '+str(d['cash_source']['reference'] or 'Sin referencia')+' · Base: '+{'net_sales_before_movements':'Ventas netas antes de movimientos','report_after_movements':'Reporte después de movimientos','unknown':'Pendiente de aclarar'}[d['cash_source']['basis']]),
  p('Movimientos detallados','Heading2')]
 if d['movements']:
  flow.append(table([['ID / origen','Momento','Tipo / dirección','Monto','Responsable / motivo']]+[[m['reference'] or m['id'],local_time(m['occurred_at']),{'income':'Ingreso','expense':'Egreso','custody':'Custodia','supplier':'Proveedor','transfer':'Traslado'}[m['kind']]+' · '+{'in':'Entrada','out':'Salida'}[m['direction']]+'\n'+str(m['origin'] or '?')+' → '+str(m['destination'] or '?'),money(m['amount']),str(m['responsible'] or 'Pendiente')+'\n'+str(m['reason'] or '')] for m in d['movements']],[33*mm,37*mm,36*mm,28*mm,50*mm]))
 else:flow.append(p('Sin movimientos declarados.' if d['movements_complete'] else 'Relación de movimientos pendiente; vacío no significa cero.'))
 flow.extend([p('Diferencias y revisión','Heading2'),p(d['difference_explanation'] or 'Sin explicación registrada.'),p(d['note'] or 'Sin observaciones adicionales.')])
 for notice in c['warnings']+c['missing']:flow.append(p('• '+notice))
 flow.append(p('La lectura automática de movimientos de Toteat sigue pendiente de validar. El saldo final del reporte no se usa como efectivo esperado. Las propinas mensuales por transferencia no generan una salida diaria de efectivo.'))
 if view.get('history'):
  flow.append(p('Historial de versiones','Heading2'))
  actions={'create':'Creación','adopt':'Vinculación de bolsa existente','save':'Guardado','submit':'Presentación','review':'Revisión de fuentes','reconcile':'Conciliación','deliver':'Entrega','receive':'Recepción','count':'Contabilización de efectivo','attach':'Adjunto'}
  for h in view['history']:flow.append(p(f"v{h['version']} · {actions.get(h['action'],h['action'])} · {person(h['actor'])} · {local_time(h['created_at'])} · {h['reason'] or 'Sin corrección'}"))
 def footer(canvas,document):
  canvas.saveState();canvas.setFont(font,7);canvas.drawString(16*mm,10*mm,'ERP Oveja · '+view['id'][:8]+' · v'+str(view['version'])+' · documento de revisión');canvas.drawRightString(letter[0]-16*mm,10*mm,str(document.page));canvas.restoreState()
 doc.build(flow,onFirstPage=footer,onLaterPages=footer);return out.getvalue()
