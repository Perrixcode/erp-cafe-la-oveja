"""Deterministic, traceable supplier KPIs and Power BI-ready CSV exports."""
import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from erp import payables as ap
from erp.domain import DomainError


def rows_for(store, source):
    if source != 'erp':
        raise DomainError('Fuente de informe no válida.')
    data=ap.view(store)
    return data['invoices'],data['suppliers'],{'label':'Facturas gestionadas en el ERP','as_of':ap.local_day(),'provisional':False},data['account_entries']


def report(store, source='erp', month=None):
    today=date.fromisoformat(ap.local_day())
    month=month or today.strftime('%Y-%m')
    try:
        start=date.fromisoformat(month+'-01')
        if start.strftime('%Y-%m')!=month:raise ValueError
    except (ValueError,TypeError):
        raise DomainError('Mes del informe no válido.') from None
    rows,suppliers,meta,account=rows_for(store,source)
    suppliers_by_id = {supplier['id']: supplier for supplier in suppliers}
    valid=[r for r in rows if not r['voided_at'] and not r['payment_review']]
    pending=[r for r in valid if (r['balance'] or 0)>0]
    aging={k:{'label':v,'amount':0,'count':0} for k,v in [('not_due','Por vencer'),('1_7','Vencido 1–7 días'),('8_30','Vencido 8–30 días'),('31_plus','Vencido más de 30 días'),('unknown','Sin vencimiento') ]}
    by_supplier=defaultdict(lambda:dict(amount=0,count=0))
    weeks=defaultdict(lambda:dict(amount=0,count=0))
    overdue=0;due_soon=0
    for row in pending:
        try:due=date.fromisoformat(row['due_on'])
        except (ValueError,TypeError):due=None
        age=(today-due).days if due else None
        key='unknown' if age is None else 'not_due' if age<=0 else '1_7' if age<=7 else '8_30' if age<=30 else '31_plus'
        aging[key]['amount']+=row['balance'];aging[key]['count']+=1
        if age is not None and age>0:overdue+=row['balance']
        if due and today<=due<=today+timedelta(days=7):due_soon+=row['balance']
        by_supplier[row['supplier_id']]['amount']+=row['balance'];by_supplier[row['supplier_id']]['count']+=1
        if due:
            monday=(due-timedelta(days=due.weekday())).isoformat()
            weeks[monday]['amount']+=row['balance'];weeks[monday]['count']+=1
    rank = []
    for supplier_id, totals in by_supplier.items():
        supplier = suppliers_by_id[supplier_id]
        label = supplier['name'] + (' · RUT '+supplier['tax_id'] if supplier['tax_id'] else '')
        rank.append(dict(supplier_id=supplier_id, name=supplier['name'], tax_id=supplier['tax_id'], label=label, **totals))
    rank.sort(key=lambda row: (-row['amount'], row['name'], row['tax_id'], row['supplier_id']))
    balance=sum(r['balance'] for r in pending)
    payments=[p for r in rows for p in r['payments'] if not p['voided_at'] and p['paid_on']]
    payments += [dict(e, paid_on=e['occurred_on']) for e in account if e['kind']=='payment' and not e['voided_at']]
    available_credit=sum(e['available'] for e in account)
    purchases=[r for r in rows if not r['voided_at'] and r['issued_on'].startswith(month)]
    trend=[]
    for offset in range(5,-1,-1):
        m=start.year*12+start.month-1-offset;period=f'{m//12:04d}-{m%12+1:02d}'
        trend.append(dict(month=period,purchases=sum(r['amount'] for r in rows if not r['voided_at'] and r['issued_on'].startswith(period)),payments=sum(p['amount'] for p in payments if p['paid_on'].startswith(period))))
    findings=[]
    held=sum(bool(r.get('payment_hold')) and not r['voided_at'] and r['balance']>0 for r in rows)
    if held:
        findings.append(dict(level='review',title='Pagos en disputa o pausa',detail=f'{held} facturas siguen formando parte de la deuda, pero se excluyen de propuestas y listados de pago hasta resolver su pausa.'))
    missing_issue=sum(not r['voided_at'] and not r['issued_on'] for r in rows)
    if missing_issue:
        findings.append(dict(level='review',title='Compras sin fecha de emisión',detail=f'{missing_issue} documentos no entran en la evolución mensual de compras hasta completar su fecha. Sus saldos sí se incluyen en la deuda.'))
    if meta['provisional']:
        findings.append(dict(level='info',title='La planilla es una referencia',detail='Sus saldos son los declarados en la copia indicada. Se conservan separados de las facturas operativas y no se suman entre sí.'))
    if overdue:
        findings.append(dict(level='urgent',title='Hay pagos vencidos',detail=f'{ap.money(overdue)} tienen vencimiento anterior a hoy. Revisa esos documentos antes de programar nuevos pagos.'))
    if aging['unknown']['count']:
        findings.append(dict(level='review',title='Faltan fechas para priorizar',detail=f"{aging['unknown']['count']} pendientes, por {ap.money(aging['unknown']['amount'])}, no tienen vencimiento confirmado."))
    review=sum(r['payment_review'] and not r['voided_at'] for r in rows)
    if review:
        findings.append(dict(level='review',title='Valida los saldos importados',detail=f'{review} facturas aún no se incluyen en la deuda por pagar. Contrástalas con los pagos anteriores.'))
    if rank and balance:
        concentration=round(rank[0]['amount']/balance*100,1)
        findings.append(dict(level='info',title='Mayor saldo por proveedor',detail=f"{rank[0]['label']} concentra {ap.money(rank[0]['amount'])} ({concentration} %) del saldo pendiente de esta fuente."))
    missing_bank=sum(s['balance']>0 and not all(s[k] for k in ('bank','account_number','account_type','account_holder','account_tax_id')) for s in suppliers)
    if missing_bank:
        findings.append(dict(level='review',title='Completa los datos de transferencia',detail=f'{missing_bank} proveedores con saldo pendiente tienen datos bancarios incompletos.'))
    missing_proof=sum(not any(f['payment_id']==p['id'] for f in r['files']) for r in rows for p in r['payments'] if not p['voided_at'] and p['paid_on'])
    missing_proof += sum(not e['files'] for e in account if e['kind']=='payment' and not e['voided_at'])
    if missing_proof:
        findings.append(dict(level='review',title='Comprobantes pendientes',detail=f'{missing_proof} pagos realizados aún no tienen comprobante adjunto.'))
    return dict(source=source,meta=meta,today=today.isoformat(),month=month,
                kpis=dict(available_credit=available_credit,net_balance=balance-available_credit,credit_notes=sum(e['amount'] for e in account if e['kind']=='credit_note' and not e['voided_at'] and e['occurred_on'].startswith(month)),balance=balance,pending=len(pending),suppliers=len(rank),overdue=overdue,due_soon=due_soon,
                          scheduled=sum(r['planned_amount'] for r in pending),review=review,missing_due=aging['unknown']['count'],
                          missing_proof=missing_proof,purchases=sum(r['amount'] for r in purchases) if source=='erp' else None,
                          payments=sum(p['amount'] for p in payments if p['paid_on'].startswith(month)) if source=='erp' else None),
                aging=list(aging.values()),suppliers=rank,weeks=[dict(week=k,**v) for k,v in sorted(weeks.items())],trend=trend if source=='erp' else [],findings=findings)


def export_csv(store,source='erp',kind='invoices'):
    if kind not in ('invoices','payments'):
        raise DomainError('Tipo de exportación no válido.')
    rows,suppliers,meta,account=rows_for(store,source)
    stream=io.StringIO(newline='');writer=csv.writer(stream,delimiter=';')
    def safe(value):
        if value is None:return ''
        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):return "'"+value
        return value
    if kind=='invoices':
        writer.writerow(['fuente','fecha_fuente','id','proveedor','tipo_documento','folio','emision','vencimiento','monto_clp','pagado_clp','saldo_clp','estado','saldo_por_validar','fecha_propuesta','monto_propuesto_clp','notas_credito_aplicadas_clp','pago_en_pausa','motivo_pausa'])
        for r in rows:
            writer.writerow([safe(v) for v in [meta['label'],meta['as_of'],r['id'],r['supplier_name'],r['document_type'],r['folio'],r['issued_on'],r['due_on'],r['amount'],None if r['payment_review'] else r['paid'],None if r['payment_review'] else r['balance'],r['status'],int(bool(r['payment_review'])),r['scheduled_on'],r['planned_amount'],None if r['payment_review'] else r['credited'],bool(r.get('payment_hold')),(r.get('payment_hold') or {}).get('reason','')]])
    else:
        writer.writerow(['id_pago','id_factura','proveedor','folio','monto_clp','fecha_pago','medio','referencia','registrado_por','fecha_registro','anulado','motivo_anulacion','saldo_apertura'])
        for r in rows:
            for p in r['payments']:
                writer.writerow([safe(v) for v in [p['id'],r['id'],r['supplier_name'],r['folio'],p['amount'],p['paid_on'],p['method'],p['reference'],p['actor'],p['created_at'],bool(p['voided_at']),p['void_reason'],not bool(p['paid_on'])]])
        for p in account:
            if p['kind']=='payment':
                supplier=next(s for s in suppliers if s['id']==p['supplier_id'])
                writer.writerow([safe(v) for v in ['cuenta-'+str(p['id']),'',supplier['name'],'',p['amount'],p['occurred_on'],p['method'],p['reference'],p['actor'],p['created_at'],bool(p['voided_at']),p['void_reason'],False]])
    return b'\xef\xbb\xbf'+stream.getvalue().encode('utf-8')
