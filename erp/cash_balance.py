"""Global cash projection over existing evidence and custody events, never a new ledger."""
from datetime import date, datetime, timedelta
import json
from uuid import uuid4

from erp.domain import DomainError
from erp import cash_control as cc


def mutate(db, action, data, actor, now):
    if action == 'balance-origin':
        cutoff = cc.day(data.get('cutoff_day'))
        if cutoff < '2000-01-01' or cutoff > datetime.now(cc.TZ).date().isoformat():
            raise DomainError('El punto de partida debe ser una fecha pasada o actual válida.')
        amount = cc.integer(data.get('amount'), 'Saldo total al comenzar la fecha')
        reason = cc.text(data.get('evidence'), 'Evidencia y motivo del punto de partida', 1000)
        prior = db.execute('SELECT id FROM cc_balance_origins ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
        key = uuid4().hex
        db.execute('INSERT INTO cc_balance_origins VALUES(?,?,?,?,?,?,?)', (key,cutoff,amount,reason,actor,now,prior['id'] if prior else None))
        return dict(id=key, cutoff_day=cutoff, amount=amount, financial_movement=False)
    event = db.execute('SELECT * FROM cc_cash_events WHERE id=?', (cc.identifier(data.get('event_id')),)).fetchone()
    if not event or event['kind'] != 'income':
        raise DomainError('Selecciona un ingreso externo existente.')
    classification = data.get('classification')
    if classification not in ('other_external','already_in_sales'):
        raise DomainError('Indica si el ingreso está incluido en ventas Toteat.')
    reason = cc.text(data.get('evidence'), 'Evidencia de la clasificación', 1000)
    prior = db.execute('SELECT id FROM cc_income_classifications WHERE event_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (event['id'],)).fetchone()
    key = uuid4().hex
    db.execute('INSERT INTO cc_income_classifications VALUES(?,?,?,?,?,?,?)', (key,event['id'],classification,reason,actor,now,prior['id'] if prior else None))
    return dict(id=key, event_id=event['id'], classification=classification, financial_movement=False)


def calendar(start, end):
    cursor = date.fromisoformat(start)
    while cursor <= date.fromisoformat(end):
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def sales_evidence(db, start, end):
    """One latest successful read per day; stable payment identities deduplicate reads."""
    latest = {r['business_date']:dict(r) for r in db.execute('''SELECT * FROM cc_source_reads WHERE id IN
      (SELECT MAX(id) FROM cc_source_reads WHERE endpoint='sales' AND business_date BETWEEN ? AND ? GROUP BY business_date)''', (start,end))}
    good = {r['business_date']:dict(r) for r in db.execute('''SELECT * FROM cc_source_reads WHERE id IN
      (SELECT MAX(id) FROM cc_source_reads WHERE endpoint='sales' AND state<>'error' GROUP BY business_date)''')}
    identities, daily, issues = {}, {}, []
    # Include other observed days when validating identity: a duplicate across a
    # configured opening or filter boundary must not become fresh income.
    for day in sorted(set(calendar(start,end)) | set(good)):
        last, read = latest.get(day), good.get(day)
        daily[day] = dict(day=day, state='missing' if not read else 'stale_error' if last and last['state']=='error' else 'observed_partial',
                          read_id=read['id'] if read else None, read_at=read['read_at'] if read else None,
                          payload_hash=read['payload_hash'] if read else None, cash_payments=0, known_cash=None,
                          excluded_payments=0, reasons=[])
        if not read:
            daily[day]['reasons'].append('Sin lectura utilizable; no se infiere efectivo cero.')
            continue
        body=json.loads(read['normalized_json'])
        if 'payments' not in body:
            daily[day]['reasons'].append('La lectura no contiene identidades de pagos; no se suma un agregado sin deduplicación.')
            continue
        for payment in body['payments']:
            cash=[m for m in payment.get('methods',[]) if m.get('provider_id')=='1000' and m.get('key')=='cash']
            if not cash:
                if any(str(m.get('provider_method','')).upper()=='CASH' for m in payment.get('methods',[])):
                    daily[day]['reasons'].append('Medio efectivo sin identidad validada.')
                continue
            key=payment.get('payment_key')
            if not key or payment.get('issues') or len(cash)!=1 or type(cash[0].get('total')) is not int or cash[0]['total']<0:
                daily[day]['excluded_payments']+=1
                continue
            value=dict(key=key, day=day, amount=cash[0]['total'], register_id=payment.get('register_id'),
                       read_id=read['id'], read_at=read['read_at'], payload_hash=read['payload_hash'])
            old=identities.get(key)
            if old:
                old['days'].add(day)
                if old['amount']!=value['amount'] or old['register_id']!=value['register_id']:
                    old['conflict']=True
            else:
                identities[key]=dict(value, days={day}, conflict=False)
    payments=[]
    for value in identities.values():
        days=sorted(value.pop('days'))
        if value['conflict'] or len(days)>1:
            # Cross-day assignment could move money across an opening/range boundary.
            if any(start<=day<=end for day in days):
                issues.append(dict(payment_key=value['key'], days=days, reason='Pago repetido entre fechas o con valores distintos; requiere asignación de origen.'))
            for day in days:daily[day]['excluded_payments']+=1
            continue
        if not start<=value['day']<=end:continue
        payments.append(value)
        row=daily[value['day']];row['cash_payments']+=1;row['known_cash']=(row['known_cash'] or 0)+value['amount']
    return dict(payments=payments, daily=[r for d,r in daily.items() if start<=d<=end], issues=issues)


def projection(db, f):
    origin_row=db.execute('SELECT * FROM cc_balance_origins ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
    origin=dict(origin_row) if origin_row else None
    history=[dict(r) for r in db.execute('SELECT * FROM cc_balance_origins ORDER BY created_at DESC,rowid DESC')]
    start,end=f['start'],f['end']
    # Operational queries never mix the bag simulator/test population into cash.
    if f.get('scope')=='tests':
        return dict(state='test_scope', origin=origin, origin_history=history, initial=None, final=None, known_variation=None,
                    reason='El saldo global operativo no se calcula a partir de fichas de prueba.', daily=[], income=None, expenses=None, sales=None, unknown_incomes=[], coverage=dict(complete=False))
    base=min(start,origin['cutoff_day']) if origin else start
    evidence=sales_evidence(db,base,end)
    events=[dict(r) for r in db.execute('SELECT * FROM cc_cash_events WHERE business_date BETWEEN ? AND ? ORDER BY occurred_at,id', (base,end))]
    classifications={r['event_id']:dict(r) for r in db.execute('''SELECT * FROM cc_income_classifications WHERE rowid IN
      (SELECT MAX(rowid) FROM cc_income_classifications GROUP BY event_id)''')}
    unknown=[]
    for event in events:
        event['classification']=classifications.get(event['id'])
        if event['kind']=='income' and not event['classification']:unknown.append(event)
    def flows(first,last):
        sales=[p for p in evidence['payments'] if first<=p['day']<=last]
        selected=[e for e in events if first<=e['business_date']<=last]
        incomes=[e for e in selected if e['kind']=='income' and e['classification'] and e['classification']['classification']=='other_external']
        expenses=[e for e in selected if e['kind']=='expense']
        observed=bool(sales or incomes or expenses)
        amounts=dict(sales=sum(p['amount'] for p in sales) if sales else None,
                     income=sum(e['amount'] for e in incomes) if incomes else None,
                     expenses=sum(e['amount'] for e in expenses) if expenses else None)
        return dict(**amounts, known_variation=sum(p['amount'] for p in sales)+sum(e['amount'] for e in incomes)-sum(e['amount'] for e in expenses) if observed else None,
                    sales_count=len(sales), income_count=len(incomes), expense_count=len(expenses),
                    transfers=sum(e['kind']=='transfer' for e in selected),
                    sales_mirrors=sum(e['kind']=='income' and bool(e['classification']) and e['classification']['classification']=='already_in_sales' for e in selected))
    selected=flows(start,end)
    initial,final=None,None
    state='opening_required' if not origin else 'provisional'
    if origin and origin['cutoff_day']<=start:
        before=(date.fromisoformat(start)-timedelta(days=1)).isoformat()
        prior=flows(origin['cutoff_day'],before)
        initial=origin['amount']+(prior['known_variation'] or 0)
        accumulated=flows(origin['cutoff_day'],end)
        if accumulated['known_variation'] is not None:
            final=origin['amount']+accumulated['known_variation']
        else:state='flow_coverage_required'
    elif origin:state='range_precedes_origin'
    daily=[]
    for source in evidence['daily']:
        if source['day']<start:continue
        day_flow=flows(source['day'],source['day'])
        daily.append(dict(source,**{k:v for k,v in day_flow.items() if k!='sales_count'}))
    # No provider contract currently certifies all sales, refunds and external expenses.
    coverage=dict(complete=False, days_requested=len(daily), days_with_sales_read=sum(d['read_id'] is not None for d in daily),
                  days_missing=sum(d['read_id'] is None for d in daily),days_with_source_error=sum(d['state']=='stale_error' for d in daily),
                  excluded_payments=sum(d['excluded_payments'] for d in daily), identity_issues=evidence['issues'],
                  external_movements_complete=False, prior_period_coverage='partial' if origin and origin['cutoff_day']<start else 'not_applicable')
    return dict(selected,state=state,origin=origin,origin_history=history,initial=initial,final=final,daily=daily,
                initial_basis='confirmed_cut' if origin and origin['cutoff_day']==start else 'confirmed_cut_plus_known_prior_flows' if initial is not None else 'unknown',
                reason='Saldo provisional de fuentes conocidas; no acredita disponibilidad ni cobertura completa. Sin punto de partida se muestra solo la variación conocida.',
                coverage=coverage, unknown_incomes=[e for e in unknown if start<=e['business_date']<=end],
                payment_keys=[p['key'] for p in evidence['payments'] if start<=p['day']<=end],
                source='Ventas Toteat identificables, netas de vuelto, y movimientos externos del libro de custodia existente.',
                scope='whole_business', filters_applied=['start','end'],
                exclusions='No suma A, B, C, collection.cash, traslados internos, vínculos de pagos ni movimientos netos Toteat sin destino validado.')
