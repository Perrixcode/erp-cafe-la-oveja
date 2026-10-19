"""Cash-bag declarations and explicit counts. No Toteat calls, payments or ledger effects."""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from erp.domain import DomainError

PREFIX = '/api/cash-bags'
BATCH_ID = r'[a-f0-9]{32}'
BAG_ID = r'[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}'
REQUIRED_PREPRINTED_KEYS = ('preprinted-000001-000020-v1', 'preprinted-000021-000040-v1')


def preprints_pending(db):
    return db.execute('SELECT COUNT(*) FROM cash_bag_imports WHERE import_key IN (?,?)',REQUIRED_PREPRINTED_KEYS).fetchone()[0] != len(REQUIRED_PREPRINTED_KEYS)


STATUS = {'blank': 'Sin completar', 'pending': 'Pendiente de verificar', 'verified': 'Verificada', 'difference': 'Con diferencia', 'void': 'Anulada'}
FIELDS = ('business_date', 'register_name', 'shift_name', 'responsible', 'counted', 'retained')


def initialize(store):
    """Startup migration only; no schema or data writes on GET/scan."""
    with store.connect() as db:
        db.executescript(Path(__file__).with_name('cash_bags_schema.sql').read_text())


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def text(value, label, maximum=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or any(ord(c)<32 for c in value):
        raise DomainError(f'{label}: completa el texto (máximo {maximum} caracteres).')
    return value.strip()


def integer(value, label, maximum=10**10):
    if type(value) is not int or not 0 <= value <= maximum:
        raise DomainError(f'{label}: usa un número entero entre 0 y {maximum}.')
    return value


def checked_origin(origin):
    parsed = urlsplit(origin)
    if (parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or
        not parsed.hostname or parsed.scheme not in {'http', 'https'} or
        (parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost'})):
        raise DomainError('Origen de etiquetas no válido.', 503)
    return origin


def project(row,db=None):
    result = dict(row)
    result.update(label=f'BOL-{result["number"]:06d}', status_label=STATUS[result['status']],
                  path='/?module=cash&bag='+result['id'])
    if db is not None:
        verification=db.execute("SELECT after_json FROM cash_bag_events WHERE bag_id=? AND action='verify' ORDER BY id DESC LIMIT 1",(result['id'],)).fetchone()
        identity=json.loads(verification[0]).get('verification_identity',{}) if verification else {}
        result['verified_by_name']=identity.get('name') or (result.get('verified_by') or '').removeprefix('erp:') or None
        from erp.cash_closures import bag_projection
        digital=bag_projection(db,result['id'])
        if digital:
            result.update(digital)
            if digital['physical_count_A'] is not None:result['counted']=digital['physical_count_A']
    return result


def row_for(db, bag_id):
    row = db.execute('SELECT * FROM cash_bags WHERE id=?', (bag_id,)).fetchone()
    if not row:
        raise DomainError('Bolsa no encontrada.', 404)
    return row


def traceability(db, bag_id):
    events = [dict(r) for r in db.execute(
        "SELECT id,action,actor,created_at,before_json,after_json FROM cash_bag_events WHERE bag_id=? ORDER BY id", (bag_id,))]
    declarations = [r for r in events if r['action'] in {'complete','correct'}]
    original = next((r for r in declarations if r['action']=='complete'), None)
    first = json.loads(original['after_json']) if original else {}
    latest = declarations[-1] if declarations else None
    return {
        'original_declaration': dict(actor=original['actor'], created_at=original['created_at'],
            responsible=first.get('responsible'), identity=first.get('declaration_identity')) if original else None,
        'last_editor': dict(actor=latest['actor'],created_at=latest['created_at'],action=latest['action']) if latest else None,
        'declaration_identity': first.get('declaration_identity'),
        'history': [dict(action=r['action'], actor=r['actor'], created_at=r['created_at'],
            reason=json.loads(r['after_json']).get('correction_reason'),
            declaration_identity=json.loads(r['after_json']).get('declaration_identity')) for r in reversed(events)]}


def bag_view(store, bag_id):
    with store.connect() as db:
        return dict(project(row_for(db, bag_id),db), **traceability(db, bag_id))


def batch_view(store, batch_id):
    with store.connect() as db:
        batch = db.execute('SELECT * FROM cash_bag_batches WHERE id=?', (batch_id,)).fetchone()
        if not batch:
            raise DomainError('Lote no encontrado.', 404)
        imported = db.execute('SELECT manifest_sha256 FROM cash_bag_imports WHERE batch_id=?', (batch_id,)).fetchone()
        return dict(batch, preprinted_manifest_sha256=imported[0] if imported else None,
                    bags=[project(r) for r in db.execute('SELECT * FROM cash_bags WHERE batch_id=? ORDER BY number', (batch_id,))])


def listing(store, query):
    raw = query.get('before', [''])[0]
    if raw and (not re.fullmatch(r'[0-9]{1,13}', raw) or int(raw)<1):
        raise DomainError('Página no válida.')
    before = int(raw) if raw else 10**13
    with store.connect() as db:
        rows = [project(r,db) for r in db.execute('SELECT * FROM cash_bags WHERE number<? ORDER BY number DESC LIMIT 51', (before,))]
        return {'bags': rows[:50], 'next_before': rows[49]['number'] if len(rows)>50 else None,
                'batches': [dict(r) for r in db.execute("SELECT b.*, SUM(c.status='blank') blank_count, SUM(c.status='pending') pending_count, SUM(c.status='verified') verified_count, SUM(c.status='difference') difference_count FROM cash_bag_batches b JOIN cash_bags c ON c.batch_id=b.id GROUP BY b.id ORDER BY b.first_number DESC LIMIT 20")],
                'total': db.execute('SELECT COUNT(*) FROM cash_bags').fetchone()[0],
                'next_number': db.execute('SELECT next_number FROM cash_bag_sequence WHERE singleton=1').fetchone()[0]}


def execute(store, action, data, account, origin, bag_id=None, bag_identity=None):
    limited = account['role'] == 'bag_cashier'
    if limited and (action != 'complete' or bag_identity is None):
        raise DomainError('Acceso limitado a entregar una bolsa.',403)
    if account['role'] not in {'partner', 'cashier', 'bag_cashier'} or (action in {'create', 'void', 'verify'} and account['role']!='partner'):
        raise DomainError('Tu perfil no permite esta acción sobre bolsas.', 403)
    common = {'request_id', 'confirmed'}
    allowed = common | ({'quantity'} if action=='create' else {'version', 'reason'} if action=='void' else {'version','verified_amount','note'} if action=='verify' else {'version', 'reason', *FIELDS})
    if limited:allowed.add('declared_name')
    if set(data)-allowed:
        raise DomainError('La solicitud contiene campos no admitidos.')
    request_id = data.get('request_id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',request_id):
        raise DomainError('Falta una clave válida de operación.')
    if data.get('confirmed') is not True:
        raise DomainError('Confirma expresamente esta operación.')
    if action not in {'create', 'complete', 'void', 'verify'}:
        raise DomainError('Operación no disponible.', 404)
    actor = account['username']
    signature = hashlib.sha256(canonical([action, bag_id, data, actor, origin if action=='create' else None]).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if limited:
            identity=bag_identity(db)
            if not identity or identity['id'] != account['username']:
                raise DomainError('Ingresa con tu correo y contraseña para entregar esta bolsa.',401)
            if identity['shared']:
                responsible=text(data.get('declared_name'),'Persona que declara este cierre',100)
            else:
                if 'declared_name' in data:raise DomainError('El nombre se obtiene de la cuenta registrada.')
                responsible=identity['name']
            data=dict(data,responsible=responsible)
        old = db.execute('SELECT signature,response_json FROM cash_bag_requests WHERE request_id=?',(request_id,)).fetchone()
        if old:
            if old['signature'] != signature:
                raise DomainError('Esta clave ya se usó para otra operación.', 409)
            return json.loads(old['response_json'])
        now = stamp()
        event_action = action
        correction_reason = None
        if action == 'create':
            quantity = integer(data.get('quantity'), 'Cantidad de bolsas', 100)
            if quantity<1:
                raise DomainError('Crea entre 1 y 100 bolsas por lote.')
            checked_origin(origin)
            if urlsplit(origin).hostname not in {'127.0.0.1','localhost'} and preprints_pending(db):
                raise DomainError('Primero importa los manifiestos definitivos de las 40 etiquetas preimpresas.',409)
            first = db.execute('SELECT next_number FROM cash_bag_sequence WHERE singleton=1').fetchone()[0]
            if first+quantity>10**12:
                raise DomainError('La numeración requiere revisión.', 409)
            batch_id = uuid4().hex
            test = urlsplit(origin).hostname in {'127.0.0.1','localhost'}
            db.execute('INSERT INTO cash_bag_batches VALUES(?,?,?,?,?,?,?)',(batch_id,first,quantity,origin,int(test),actor,now))
            db.execute('UPDATE cash_bag_sequence SET next_number=? WHERE singleton=1',(first+quantity,))
            for number in range(first, first+quantity):
                key = str(uuid4())
                db.execute('INSERT INTO cash_bags(id,number,batch_id,created_at,updated_at) VALUES(?,?,?,?,?)',(key,number,batch_id,now,now))
                after = project(row_for(db,key))
                db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?,?)',
                           (key,batch_id,'created',actor,canonical(after),now))
            result = {'batch_id': batch_id, 'first_label': f'BOL-{first:06d}',
                      'last_label': f'BOL-{first+quantity-1:06d}', 'quantity': quantity}
        else:
            before = dict(row_for(db,bag_id))
            from erp.cash_closures import bag_projection
            if bag_projection(db,bag_id):raise DomainError('Esta bolsa pertenece a un cierre digital. Abre ese cierre para corregir, entregar o contar; el QR no se reutiliza.',409)
            if type(data.get('version')) is not int or data['version'] != before['version']:
                raise DomainError('La ficha cambió. Vuelve a abrirla antes de guardar.',409)
            if before['status']=='void':
                raise DomainError('La bolsa está anulada y conserva su número.',409)
            if action == 'complete':
                if before['status'] not in {'blank','pending'} or (before['status']=='pending' and account['role']!='partner'):
                    raise DomainError('Esta bolsa ya fue entregada. Las correcciones requieren una persona autorizada.',409)
                fields = {key:text(data.get(key), label) for key,label in
                          [('register_name','Caja'),('shift_name','Turno'),('responsible','Responsable')]}
                try:
                    day = data['business_date']
                    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:
                        raise ValueError()
                except (KeyError,TypeError,ValueError):
                    raise DomainError('Fecha operativa no válida.') from None
                counted = integer(data.get('counted'),'A contado')
                retained = integer(data.get('retained'),'B fondo')
                if retained>counted:
                    raise DomainError('B fondo no puede superar A contado.')
                values = dict(fields,business_date=day,counted=counted,retained=retained)
                correcting = before['status']=='pending'
                if correcting and all(before[k]==v for k,v in values.items()):
                    result = dict(project(row_for(db,bag_id)),unchanged=True)
                    db.execute('INSERT INTO cash_bag_requests VALUES(?,?,?)',(request_id,signature,canonical(result)))
                    return result
                if correcting:
                    correction_reason = text(data.get('reason'),'Motivo de la corrección',500)
                    event_action = 'correct'
                db.execute('UPDATE cash_bags SET business_date=?,register_name=?,shift_name=?,responsible=?,counted=?,retained=?,bag_amount=?,status=\'pending\',version=version+1,updated_at=?,declared_at=?,declared_by=? WHERE id=?',
                           (day,fields['register_name'],fields['shift_name'],fields['responsible'],counted,retained,counted-retained,now,
                            before['declared_at'] if correcting else now,before['declared_by'] if correcting else actor,bag_id))
            elif action == 'verify':
                if before['status']!='pending':
                    raise DomainError('Primero confirma la entrega; solo se verifica una bolsa pendiente.',409)
                amount=integer(data.get('verified_amount'),'Efectivo contabilizado en la bolsa')
                difference=amount-before['bag_amount']
                note=text(data.get('note'),'Observación de la diferencia',1000) if difference else (text(data['note'],'Observación',1000) if data.get('note') else '')
                db.execute("UPDATE cash_bags SET status=?,verified_amount=?,verified_difference=?,verified_at=?,verified_by=?,verification_note=?,version=version+1,updated_at=? WHERE id=?",
                           ('difference' if difference else 'verified',amount,difference,now,actor,note,now,bag_id))
            else:
                reason = text(data.get('reason'),'Motivo de anulación',500)
                db.execute("UPDATE cash_bags SET status='void',void_reason=?,version=version+1,updated_at=? WHERE id=?",(reason,now,bag_id))
            result = project(row_for(db,bag_id))
            if action=='verify':result['verification_identity']={'key':actor,'name':account.get('name',actor)}
            if correction_reason:result['correction_reason']=correction_reason
            if limited:
                result['declaration_identity']={'account_id':identity['id'],'account_name':identity['name'],'email':identity['email'],'shared':bool(identity['shared']),'declared_person':responsible,'person_verified':False,'authentication':'password'}
            db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)',
                       (bag_id,before['batch_id'],event_action,actor,canonical(before),canonical(result),now))
        if limited:
            result={'received':True,'label':result['label']}
        db.execute('INSERT INTO cash_bag_requests VALUES(?,?,?)',(request_id,signature,canonical(result)))
        return result


def handle(handler, store, path, query, account, origin):
    if account['role'] not in {'partner','cashier'}:
        raise DomainError('Tu perfil no tiene acceso a las fichas de bolsas.',403)
    batch = re.fullmatch(PREFIX+r'/batches/('+BATCH_ID+r')(/labels.pdf)?',path)
    bag = re.fullmatch(PREFIX+r'/('+BAG_ID+r')(/complete|/void|/verify)?',path)
    if handler.command=='GET':
        if path==PREFIX:
            with store.connect() as db:
                pending = urlsplit(origin).hostname not in {'127.0.0.1','localhost'} and preprints_pending(db)
            return handler.send(200,dict(listing(store,query),can_create=account['role']=='partner' and not pending,import_pending=pending,cashier_management=account['role']=='partner' and getattr(handler,'bag_access_available',False)))
        if batch:
            result=batch_view(store,batch[1])
            if batch[2]:
                from erp.cash_bags_pdf import labels_pdf
                data=labels_pdf(result)
                disposition='attachment' if query.get('download')==['1'] else 'inline'
                return handler.send(200,data,'application/pdf',{'Content-Disposition':f'{disposition}; filename="etiquetas-bolsas-{result["first_number"]:06d}.pdf"'})
            return handler.send(200,result)
        if bag and not bag[2]:
            value=bag_view(store,bag[1]);legacy=not value.get('closure_id')
            return handler.send(200,dict(value,can_void=legacy and account['role']=='partner',can_verify=legacy and account['role']=='partner',can_correct=legacy and account['role']=='partner'))
    if handler.command=='POST':
        if path==PREFIX+'/batches':
            return handler.send(201,execute(store,'create',handler.body(),account,origin))
        if bag and bag[2]:
            return handler.send(200,execute(store,bag[2][1:],handler.body(),account,origin,bag[1]))
    raise DomainError('Ruta de bolsas no encontrada.',404)
