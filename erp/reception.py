"""Decisiones de recepción, evidencia de anulaciones y trazabilidad. Solo lectura de Toteat."""
from datetime import datetime, timezone
import hashlib
import json
from zoneinfo import ZoneInfo
from erp.domain import DomainError
from erp.store import clean_text, now
from erp.toteat_scheduling import parse_comment, settlement, channel_from_platform


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def source_key(scope,identifier):return canonical([str(scope['restaurant_id']),str(scope['local_id']),str(identifier)])


def linked_order(db,key):
    row=db.execute("SELECT order_id FROM toteat_order_links WHERE source_id=? UNION SELECT id FROM orders WHERE source='toteat' AND source_id=?",(key,key)).fetchone()
    return row[0] if row else None


def event(store,db,key,action,actor,reason,before,after,order_id=None):
    order_id=order_id or linked_order(db,key)
    db.execute('INSERT INTO reception_events(source_id,order_id,action,actor,reason,occurred_at,before_json,after_json) VALUES(?,?,?,?,?,?,?,?)',
               (key,order_id,action,actor,reason,now(),canonical(before),canonical(after)))
    if order_id:store._event(db,order_id,None,action,actor,reason,before,after)


def order_alert(db,order_id):
    row=db.execute('SELECT source_id,state,evidence_json,resolution,version,updated_at FROM order_source_alerts WHERE order_id=?',(order_id,)).fetchone()
    if not row:return None
    value=dict(row);value['evidence']=json.loads(value.pop('evidence_json'));return value


def require_bindable(db,key):
    row=db.execute('SELECT state FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
    if row and row[0]=='cancelled':raise DomainError('La comanda está anulada; no puede agendarse.',409)
    alert=db.execute('SELECT resolution FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
    if alert and not alert[0]:raise DomainError('Resuelve primero la alerta de anulación parcial o documento financiero.',409)


def guard_import(db,key):
    alert=db.execute('SELECT state,resolution FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
    if alert and alert[0]=='cancelled':raise ValueError('source_cancelled')
    if alert and not alert[1]:raise ValueError('source_alert_requires_review')
    decision=db.execute('SELECT decision FROM reception_decisions WHERE source_id=?',(key,)).fetchone()
    if decision and decision[0]=='immediate':raise ValueError('classified_immediate')
    if decision and decision[0]=='review':raise ValueError('manual_review_required')


def require_importable(store,key):
    with store.connect() as db:guard_import(db,key)


def observe_sale(store,scope,transaction,candidate=None):
    """Conserva solo campos necesarios. NC sin productos también queda visible si se conoce la orden."""
    key=source_key(scope,transaction['orderId'])
    fields=('orderId','paymentId','dateOpen','dateClosed','comment','total','payed','discounts','difference','fiscalId','fiscalType')
    row={k:transaction.get(k) for k in fields}
    reference=transaction.get('referencedPayment',transaction.get('referencedPayment*'))
    if isinstance(reference,dict):row['referencedPayment']={k:reference.get(k) for k in ('id','fiscalId','documentType')}
    payload={'transaction':row,'candidate':candidate};fingerprint=digest(payload)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT digest,payload_json FROM reception_sources WHERE source_id=?',(key,)).fetchone()
        # Una nota de crédito no reemplaza la evidencia del pago original.
        refund=str(row.get('fiscalType') or '').upper()=='NC' or bool(reference)
        if not refund and (not old or old['digest']!=fingerprint):
            db.execute('INSERT INTO reception_sources(source_id,digest,payload_json,observed_at) VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET digest=excluded.digest,payload_json=excluded.payload_json,version=version+1,observed_at=excluded.observed_at',(key,fingerprint,canonical(payload),now()))
            event(store,db,key,'venta_recibida' if not old else 'venta_actualizada','Lector Toteat','Venta cerrada recibida; no implica autorización de agendamiento.',json.loads(old['payload_json']) if old else None,payload)
    if refund:record_alert(store,key,'refund_review',{'source':'sales','fiscal_type':row['fiscalType'],'payment_id':str(row['paymentId']),'referenced_payment':row.get('referencedPayment')})
    return key


def revision(db,received):
    key=received['key']
    values={}
    for table in ('reception_sources','reception_decisions','order_source_alerts'):
        row=db.execute('SELECT version FROM '+table+' WHERE source_id=?',(key,)).fetchone();values[table]=row[0] if row else 0
    # Los tiempos de polling no representan un cambio de la comanda.
    snapshot={k:v for k,v in received.items() if k not in ('last_seen','first_seen','version','scheduling','sales_comment_evidence','comments','reception')}
    return digest({'source':snapshot,'revisions':values})


def check_revision(db,received,expected):
    if not isinstance(expected,str) or revision(db,received)!=expected:raise DomainError('La comanda cambió. Actualiza y revisa antes de decidir.',409)


def immediate_eligible(sale,alert,order_id,decision):
    """Evidencia cerrada recibida y saldada, con comentario vacío/incompleto."""
    if not sale or alert or order_id or (decision and decision['decision']=='immediate'):return False
    row=sale['transaction']
    try:
        payment=settlement(row)
        reference=datetime.fromisoformat(payment['date_closed'])
        if reference.tzinfo is None:reference=reference.replace(tzinfo=timezone.utc)
        parsed=parse_comment(row.get('comment') or '',reference.astimezone(ZoneInfo('America/Santiago')).date())
    except (ValueError,TypeError,KeyError):return False
    if channel_from_platform(parsed['platform'])=='Web/Mercat':return False
    return bool(parsed['issues'] or any(w in ('telefono_pendiente','telefono_por_revisar') for w in parsed['warnings']))


def context(store,received,include_history=False):
    key=received['key']
    with store.connect() as db:
        sale=db.execute('SELECT payload_json FROM reception_sources WHERE source_id=?',(key,)).fetchone()
        sale=json.loads(sale[0]) if sale else None
        decision=db.execute('SELECT * FROM reception_decisions WHERE source_id=?',(key,)).fetchone()
        alert=db.execute('SELECT * FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
        order_id=linked_order(db,key)
        result={'revision':revision(db,received),'decision':dict(decision) if decision else None,'order_id':order_id,'alert':dict(alert) if alert else None,'payment':None}
        if alert:result['alert']['evidence']=json.loads(result['alert'].pop('evidence_json'))
        if include_history:
            result['history']=[dict(r) for r in db.execute('SELECT action,actor,reason,occurred_at,before_json,after_json FROM reception_events WHERE source_id=? ORDER BY id DESC',(key,))]
            for entry in result['history']:
                entry['before']=json.loads(entry.pop('before_json'));entry['after']=json.loads(entry.pop('after_json'))
        closed_at=None
        original='\n'.join(c['text'] for c in received.get('comments',[]) if isinstance(c.get('text'),str))
        if sale:
            row=sale['transaction'];original=row.get('comment') or ''
            try:result['payment']=settlement(row)
            except (ValueError,TypeError,KeyError):pass
            closed_at=row.get('dateClosed')
        if not closed_at:
            evidence=[e for e in received.get('sales_comment_evidence',[]) if str(e.get('order_id'))==str(received['order_id']) and e.get('date_closed')]
            if evidence:closed_at=evidence[-1]['date_closed']
            elif received.get('status_label')=='CLOSED':closed_at=received.get('modified_at')
        try:
            reference_time=datetime.fromisoformat(closed_at)
            if reference_time.tzinfo is None:reference_time=reference_time.replace(tzinfo=timezone.utc)
        except (ValueError,TypeError):closed_at=None;reference_time=datetime.now(timezone.utc)
        result['closed_at']=closed_at
        result['closed_payload_received']=bool(sale and closed_at)
        result['can_classify_immediate']=immediate_eligible(sale,alert,order_id,decision)
        result['parsed']=parse_comment(original,reference_time.astimezone(ZoneInfo('America/Santiago')).date())
        result['channel']=channel_from_platform(result['parsed']['platform'])
        result['web_delivery_review']=result['channel']=='Web/Mercat'
        result['original_comment']=original
        labels={'nombre_pendiente':'Nombre y apellido','fecha_pendiente':'Fecha de entrega','horario_pendiente':'Hora de entrega','telefono_pendiente':'Teléfono','telefono_por_revisar':'Teléfono por revisar','dia_semana_no_coincide':'Día y fecha no coinciden'}
        result['missing_fields']=[labels.get(code,'Comentario por revisar') for code in result['parsed']['issues']+result['parsed']['warnings'] if code in labels or code.startswith('campo_repetido_')]
        result['review_state']='delivered_immediate' if decision and decision['decision']=='immediate' else 'scheduled' if order_id else 'waiting_closed_payload' if closed_at and not sale else 'payment_review' if closed_at and not result['payment'] else 'schedule_incomplete' if result['payment'] and result['can_classify_immediate'] else 'source_review' if closed_at else 'waiting_close_payment'
        return result


def record_scheduled(store,db,received,order_id,partner,reason):
    key=received['key'];before=db.execute('SELECT * FROM reception_decisions WHERE source_id=?',(key,)).fetchone()
    db.execute("INSERT INTO reception_decisions(source_id,decision,order_id,partner_username,reason,updated_at) VALUES(?,'scheduled',?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET decision='scheduled',order_id=excluded.order_id,partner_username=excluded.partner_username,reason=excluded.reason,version=version+1,updated_at=excluded.updated_at",(key,order_id,partner['username'],reason,now()))
    event(store,db,key,'agendado_por_socio',partner['username'],reason,dict(before) if before else None,{'order_id':order_id,'source_id':key,'authorization':'without_settlement_requirement'},order_id)


def schedule(store,received,data,partner,reason,request_id,expected):
    from erp import sos
    reason=clean_text(reason,'Motivo de agendamiento',maximum=500)
    value=dict(data or {});value['payment_status']='unpaid';value['payment_verified']=False;value['payment_evidence']=''
    # Productos desde el catálogo recibido; la UI no puede sustituir la comanda.
    value['items']=[{'sku':sku,'quantity':quantity} for sku,quantity in sos.received_totals(store,received).items()]
    details=context(store,received)
    if details['channel']!='No informado':value['channel']=details['channel']
    saved=sos.create(store,value,partner,reason,request_id,received,authorize_now=True,reception_revision=expected)
    with store.connect() as db:row=db.execute('SELECT payload_json FROM reception_sources WHERE source_id=?',(received['key'],)).fetchone()
    if row:
        sale=json.loads(row[0])
        if sale.get('candidate'):
            try:saved=store.import_toteat_schedule({'restaurant_id':received['restaurant_id'],'local_id':received['local_id']},sale['transaction'],sale['candidate'],is_test=saved['is_simulation'])
            except ValueError:pass # Conserva autorización y revisión; el lector vuelve a conciliar.
    return saved


def decide(store,received,decision,partner,reason,expected):
    if decision not in ('immediate','review'):raise DomainError('Decisión no válida.')
    reason=clean_text(reason,'Motivo de revisión',maximum=500);key=received['key']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        before=db.execute('SELECT * FROM reception_decisions WHERE source_id=?',(key,)).fetchone()
        if before and before['decision']==decision:return dict(before)
        check_revision(db,received,expected);require_bindable(db,key)
        if linked_order(db,key):raise DomainError('La comanda ya tiene un pedido. Revisa ese pedido sin duplicarlo.',409)
        if decision=='immediate':
            sale=db.execute('SELECT payload_json FROM reception_sources WHERE source_id=?',(key,)).fetchone()
            alert=db.execute('SELECT * FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
            if not immediate_eligible(json.loads(sale[0]) if sale else None,alert,None,before):
                raise DomainError('Requiere venta cerrada y saldada recibida, comentario vacío o incompleto y ninguna anulación.',409)
        db.execute('INSERT INTO reception_decisions(source_id,decision,partner_username,reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET decision=excluded.decision,partner_username=excluded.partner_username,reason=excluded.reason,version=version+1,updated_at=excluded.updated_at',(key,decision,partner['username'],reason,now()))
        event(store,db,key,'venta_inmediata_confirmada' if decision=='immediate' else 'revision_reabierta',partner['username'],reason,dict(before) if before else None,{'decision':decision,'delivery_status':'Entregada inmediata' if decision=='immediate' else None,'payment_recorded':False,'stock_movement':False})
        return dict(db.execute('SELECT * FROM reception_decisions WHERE source_id=?',(key,)).fetchone())


def record_alert(store,key,state,evidence):
    fingerprint=digest({'state':state,'evidence':evidence})
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
        if old and (old['digest']==fingerprint or old['state']=='cancelled'):return
        order_id=linked_order(db,key)
        before=store._get(db,order_id) if order_id else None
        db.execute('INSERT INTO order_source_alerts(source_id,order_id,state,digest,evidence_json,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET order_id=excluded.order_id,state=excluded.state,digest=excluded.digest,evidence_json=excluded.evidence_json,resolution=NULL,version=version+1,updated_at=excluded.updated_at',(key,order_id,state,fingerprint,canonical(evidence),now()))
        # Detectar conserva los estados locales; anular requiere confirmación del socio.
        event(store,db,key,'anulacion_detectada' if state=='cancelled' else 'revision_fuente_requerida','Lector Toteat','Anulación explícita recibida de Toteat.' if state=='cancelled' else 'Revisar anulación parcial o documento financiero antes de modificar el encargo.',before,{'state':state,'evidence':evidence},order_id)


def inspect_cancellation(store,scope,row):
    """Solo enum explícito o flags booleanos documentados. Nunca códigos numéricos/ausencia."""
    if not isinstance(row,dict) or row.get('orderId') is None:return
    if any(k in row and str(row[k])!=str(scope[s]) for k,s in [('restaurantId','restaurant_id'),('localNumber','local_id')]):raise ValueError('cancellation_scope_mismatch')
    key=source_key(scope,row['orderId'])
    code=row.get('orderStatus')
    if isinstance(code,str) and code.upper()=='CANCELLED':
        record_alert(store,key,'cancelled',{'source':'orderstatus','order_status':'CANCELLED'});return
    products={int(p['source_product']['idToteat']) for p in store.catalog() if p.get('source_product')}
    document=row.get('document');raw_lines=document.get('line') if isinstance(document,dict) else []
    lines=[r for r in (raw_lines if isinstance(raw_lines,list) else []) if isinstance(r,dict) and r.get('isExtra') is False and r.get('productCodeToteat') in products]
    cancelled=[r for r in lines if r.get('cancelled') is True]
    if cancelled:
        record_alert(store,key,'cancelled' if len(cancelled)==len(lines) else 'partial_cancel',
                     {'source':'orderstatus.document.line','all_cake_lines_cancelled':len(cancelled)==len(lines),'lines':[{'line':r.get('lineNumber'),'product':r.get('productCodeToteat'),'quantity':r.get('quantity'),'cancelled':r.get('cancelled') is True} for r in lines]})


def resolve_alert(store,key,partner,reason,version,decision):
    if decision not in ('keep','cancel'):raise DomainError('Selecciona mantener el encargo o confirmar su anulación.')
    reason=clean_text(reason,'Motivo de resolución',maximum=500)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');alert=db.execute('SELECT * FROM order_source_alerts WHERE source_id=?',(key,)).fetchone()
        if not alert:raise DomainError('No hay una alerta para revisar.',404)
        if alert['state']=='cancelled' and decision!='cancel':raise DomainError('La fuente está anulada. Solo puede confirmarse la anulación local.',409)
        if alert['resolution']==decision:return dict(alert)
        if type(version) is not int or version!=alert['version']:raise DomainError('La alerta cambió; actualiza antes de resolver.',409)
        order_id=linked_order(db,key)
        if decision=='cancel' and order_id:
            before=store._get(db,order_id)
            db.execute("UPDATE items SET status='cancelado' WHERE order_id=? AND status NOT IN ('entregado','cancelado')",(order_id,));store._touch(db,order_id)
        db.execute('UPDATE order_source_alerts SET resolution=?,state=?,version=version+1,updated_at=? WHERE source_id=?',(decision,'cancelled' if decision=='cancel' else alert['state'],now(),key))
        event(store,db,key,'anulacion_confirmada_socio' if decision=='cancel' else 'alerta_revisada_socio',partner['username'],reason,dict(alert),{'decision':decision,'no_toteat_or_stock_write':True},order_id)
        return dict(db.execute('SELECT * FROM order_source_alerts WHERE source_id=?',(key,)).fetchone())
