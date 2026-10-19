"""Server-owned weekly V2 pilot. No provider writes, local storage or ledger postings."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import base64, hashlib, hmac, json, math, os, re, secrets, sqlite3, warnings
from PIL import Image
from erp.domain import DomainError
from erp import cash_closures as cl, cash_closure_calc as calc, cash_bags as bags, cash_toteat as toteat

PREFIX = cl.PREFIX + '/v2'
LIMIT = 10**10
MAX_IMAGE = 3 * 1024 * 1024
DECLARATION_FIELDS = {'identity','cash','retained','terminals','bagNumber','note','photos','missing_evidence_reasons'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def initialize(db):
    statement=''
    for line in Path(__file__).with_name('cashier_v2_schema.sql').read_text().splitlines(True):
        statement+=line
        if sqlite3.complete_statement(statement):db.execute(statement);statement=''
    if statement.strip():raise ValueError('Incomplete V2 migration')
    for table in ('cv2_submissions','cv2_reviews','cv2_events','cv2_requests','cv2_images','cv2_cash_references'):
        for action in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable V2 evidence'); END")


def enabled():
    return os.environ.get('OVEJA_CASHIER_V2', '1') == '1'


def is_v2(db, cid):
    return bool(db.execute('SELECT 1 FROM cv2_drafts WHERE closure_id=?', (cid,)).fetchone())


def integer(value, label, nullable=True, signed=False, maximum=LIMIT):
    if value is None and nullable:
        return None
    if type(value) is not int or abs(value) > maximum or (value < 0 and not signed):
        raise DomainError(label + ': usa un entero válido, sin decimales ni texto.')
    return value


def text(value, label, maximum=1500, required=False):
    if value is None and not required:
        return ''
    if not isinstance(value,str) or len(value)>maximum or '\x00' in value or (required and not value.strip()):
        raise DomainError(label + ': revisa este campo.')
    return value.strip()


def period(value):
    if value in (None, ''):
        return None
    if not isinstance(value,str):
        raise DomainError('Período no válido.')
    try:
        dt = datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:
            choices = set()
            for fold in (0,1):
                local = dt.replace(tzinfo=calc.TZ, fold=fold)
                utc = local.astimezone(timezone.utc)
                if utc.astimezone(calc.TZ).replace(tzinfo=None) == dt:
                    choices.add(utc.isoformat(timespec='seconds'))
            if len(choices) != 1:
                raise ValueError()
            return choices.pop()
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
    except ValueError:
        raise DomainError('El período contiene una hora inexistente, ambigua o no válida en Santiago.') from None


def normalize(db, row, raw, previous=None):
    calc.shape(raw, DECLARATION_FIELDS, 'Declaración V2')
    ident=calc.shape(raw.get('identity'), ('business_date','register_id','session_key','counted_at'), 'Identificación')
    actual=cl.identity(db, dict(business_date=ident.get('business_date'), register_id=ident.get('register_id'), shift_name=row['shift_name']), row['business_date'])
    same_scope=previous and all(previous['identity'][k]==actual[k] for k in ('business_date','register_id'))
    source=toteat.project(db, actual['business_date'], actual['register_id'])
    # Selection is server-owned; ambiguity always remains pending.
    selected=source['selected']
    identity={k:actual[k] for k in ('business_date','register_id')}
    identity.update(session_key=selected['session_key'] if selected else None,
                    counted_at=previous['identity'].get('counted_at') if same_scope else None)
    counts={}
    for name in ('cash','retained'):
        values=calc.shape(raw.get(name), {str(d) for d in calc.DENOMINATIONS}, name)
        counts[name]={str(d):integer(values.get(str(d)), 'Cantidad '+str(d), maximum=100000) for d in calc.DENOMINATIONS}
    for d in counts['cash']:
        a,b=counts['cash'][d],counts['retained'][d]
        if a is not None and b is not None and b>a:
            raise DomainError('B no puede superar A en ninguna denominación.')
    if not identity['counted_at'] and all(v is not None for group in counts.values() for v in group.values()):
        identity['counted_at']=cl.stamp()
    terminals=raw.get('terminals')
    if not isinstance(terminals,list) or len(terminals)>12:
        raise DomainError('Revisa los terminales (máximo 12).')
    rows=[];seen=set()
    for entry in terminals:
        calc.shape(entry, ('terminal_id','total','period_start','period_end','scope_confirmed'), 'Terminal')
        name=text(entry.get('terminal_id'),'Terminal',100)
        key=cl.key(name)
        if key and key in seen:
            raise DomainError('Registra cada terminal una sola vez.')
        if key:seen.add(key)
        start,end=period(entry.get('period_start')),period(entry.get('period_end'))
        if start and end and end<=start:
            raise DomainError('El fin del período debe ser posterior al inicio.')
        if type(entry.get('scope_confirmed',False)) is not bool:
            raise DomainError('Confirma el período del terminal de forma explícita.')
        rows.append(dict(terminal_id=name,total=integer(entry.get('total'),'Total del papel'),period_start=start,period_end=end,scope_confirmed=entry.get('scope_confirmed',False)))
    photos=raw.get('photos',[])
    if not isinstance(photos,list) or len(photos)>12:
        raise DomainError('Hasta 12 fotos por cierre.')
    normalized=[];seen=set()
    for item in photos:
        calc.shape(item, ('id','kind','name','type','size','width','height','sha256','legible'), 'Foto')
        image=db.execute('SELECT a.id,a.name,a.mime,a.bytes,a.sha256,i.kind,i.width,i.height FROM cl_attachments a JOIN cv2_images i ON i.attachment_id=a.id WHERE a.id=? AND a.closure_id=?',(item.get('id'),row['id'])).fetchone()
        if not image or image['id'] in seen:
            raise DomainError('El respaldo no está guardado en este cierre o está repetido.')
        if type(item.get('legible')) is not bool:
            raise DomainError('Indica la legibilidad del respaldo.')
        seen.add(image['id']);normalized.append(dict(id=image['id'],name=image['name'],kind=image['kind'],type=image['mime'],size=image['bytes'],sha256=image['sha256'],width=image['width'],height=image['height'],legible=item['legible']))
    reasons=calc.shape(raw.get('missing_evidence_reasons',{}), ('mp','toteat'), 'Respaldo pendiente')
    bag_number=text(raw.get('bagNumber'),'Número de bolsa',30)
    if bag_number:
        match=re.fullmatch(r'(?:BOL-)?([0-9]{1,10})',bag_number,re.I)
        if not match:raise DomainError('Usa el número impreso junto al QR: BOL-000041.')
        bag_number='BOL-'+str(int(match[1])).zfill(6)
    return dict(identity=identity,**counts,terminals=rows,photos=normalized,bagNumber=bag_number,note=text(raw.get('note'),'Comentario'),missing_evidence_reasons={k:text(reasons.get(k),'Motivo '+k) for k in ('mp','toteat')})


def empty(row):
    return dict(identity=dict(business_date=row['business_date'],register_id=row['register_id'],session_key=None,counted_at=None), cash={str(d):None for d in calc.DENOMINATIONS},retained={str(d):None for d in calc.DENOMINATIONS},terminals=[],photos=[],bagNumber='',note='',missing_evidence_reasons={'mp':'','toteat':''})


def scope(declaration):
    return declaration['identity']

def comparison_scope(db,declaration):
    ident=dict(declaration['identity'])
    selected=toteat.project(db,ident['business_date'],ident['register_id'],ident['session_key'])['selected']
    if selected:ident['session_key']=selected['session_key']
    return ident


def source_summary(db,row,declaration):
    from erp.cash_insights import comparable_report
    ident=declaration['identity']
    src=toteat.project(db,ident['business_date'],ident['register_id'],ident['session_key'])
    # Poll timestamps alone must not invalidate a review. Only relevant economic evidence does.
    source=dict(selected_session=src['selected'],source_state=src['sources']['collection']['state'],
        source_read={k:src['sources']['collection'].get(k) for k in ('read_id','payload_hash','state','error')},
        missing_session=src['missing_session'],source_notice=src['notice'])
    expected=None;cash_reference=None
    r=db.execute('SELECT * FROM cv2_cash_references WHERE closure_id=? ORDER BY id DESC LIMIT 1',(row['id'],)).fetchone()
    if r and json.loads(r['scope_json'])==comparison_scope(db,declaration):
        expected=r['expected'];cash_reference={k:r[k] for k in ('id','expected','evidence','attachment_id','actor','created_at')}
    terminals=[]
    for t in declaration['terminals']:
        reference=None;report_id=None
        # Only independently reviewed, terminal-specific economic cuts can be reused.
        # A provider session aggregate never proves an individual terminal's amount.
        for r in db.execute('SELECT * FROM ci_terminal_reports WHERE closure_id=? ORDER BY created_at DESC,id DESC',(row['id'],)):
            v=json.loads(r['report_json']);cut=v.get('economic_scope') or {};s=v['source'];session=s.get('session') or {}
            superseded=db.execute('SELECT 1 FROM ci_terminal_reports WHERE supersedes=?',(r['id'],)).fetchone()
            if superseded or cut.get('kind')!='verified_cut' or not comparable_report(v)['comparable']:
                continue
            if (cl.key(v['terminal_id'])==cl.key(t['terminal_id']) and v['period_start']==t['period_start'] and v['period_end']==t['period_end']
                and s['business_date']==ident['business_date'] and s['register_id']==ident['register_id'] and session.get('session_key')==comparison_scope(db,declaration)['session_key']
                and src['selected'] and session.get('opened_at')==src['selected']['opened_at']
                and src['sources']['collection']['state']=='observed' and t['scope_confirmed']):
                reference=cut['toteat_total'];report_id=r['id'];break
        difference=t['total']-reference if t['total'] is not None and reference is not None else None
        terminals.append(dict(terminal_id=t['terminal_id'],declared=t['total'],expected=reference,difference=difference,comparison_state='pending' if difference is None else 'matched' if difference==0 else 'difference',report_id=report_id))
    source.update(expected=expected,cash_reference=cash_reference,terminals=terminals)
    return source


def summary(db,row,declaration):
    value=source_summary(db,row,declaration)
    a,b=calc.denom_total(declaration['cash']),calc.denom_total(declaration['retained'])
    c=a-b if a is not None and b is not None else None
    diff=a-value['expected'] if a is not None and value['expected'] is not None else None
    requires=diff not in (None,0) or any(t['difference'] not in (None,0) for t in value['terminals'])
    missing=[]
    if a is None or b is None:missing.append('Completa A y B; escribe 0 donde no haya piezas.')
    if any(not t['terminal_id'] or t['total'] is None for t in declaration['terminals']):missing.append('Completa los terminales declarados.')
    if requires and not declaration['note']:missing.append('Explica los descuadres comparables antes de presentar.')
    for kind in ('mp','toteat'):
        if not any(p['kind']==kind and p['legible'] for p in declaration['photos']) and not declaration['missing_evidence_reasons'][kind]:
            missing.append('Falta un respaldo legible o motivo individual: '+kind+'.')
    if not declaration['bagNumber']:missing.append('Indica el número de bolsa.')
    value.update(A=a,B=b,C=c,difference=diff,requires_comment=requires,can_submit=not missing,missing=missing,
        mp_total=sum(t['total'] for t in declaration['terminals']) if declaration['terminals'] and all(t['total'] is not None for t in declaration['terminals']) else None,
        movement_notice='A es el conteo físico final. Los movimientos ya están incorporados; C = A − B.',
        custody_state='prepared',cash_at_seal=a,post_count_net=0)
    return value


def latest(db,cid):
    return db.execute('SELECT * FROM cv2_submissions WHERE closure_id=? ORDER BY number DESC LIMIT 1',(cid,)).fetchone()


def verify_submission(db,r):
    snap=json.loads(r['snapshot_json']);sig=json.loads(r['signature_json'])
    metadata={'closure_id':'closure_id','submission_id':'id','submission_version':'number','folio':'folio','submitted_by_user_id':'actor','submitted_at_server':'created_at'}
    if any(snap.get(k)!=r[v] for k,v in metadata.items()):
        raise DomainError('La presentación no corresponde a sus metadatos originales.',409)
    if digest(snap)!=r['snapshot_hash'] or digest([snap,sig])!=r['integrity_hash'] or sig.get('snapshot_hash')!=r['snapshot_hash'] or sig.get('version')!=r['number'] or sig.get('signer_key')!=r['actor'] or sig.get('signed_at')!=r['created_at']:
        raise DomainError('La integridad de la presentación requiere revisión. No se mostrará con una firma distinta.',409)
    for meta in snap['declaration']['photos']:
        a=db.execute('SELECT * FROM cl_attachments WHERE id=? AND closure_id=?',(meta['id'],r['closure_id'])).fetchone()
        if not a or a['sha256']!=meta['sha256'] or a['bytes']!=meta['size'] or hashlib.sha256(a['content']).hexdigest()!=meta['sha256']:
            raise DomainError('La evidencia de la presentación requiere revisión de integridad.',409)
    return dict(id=r['id'],folio=r['folio'],number=r['number'],snapshot=snap,signature=sig,snapshot_hash=r['snapshot_hash'],created_at=r['created_at'])


def event(db,cid,kind,detail,account,submission=None):
    db.execute('INSERT INTO cv2_events(closure_id,submission_id,kind,detail_json,actor,created_at) VALUES(?,?,?,?,?,?)',(cid,submission,kind,canonical(detail),cl.actor(account),cl.stamp()))


def view_db(db,row):
    draft=db.execute('SELECT * FROM cv2_drafts WHERE closure_id=?',(row['id'],)).fetchone()
    if not draft:raise DomainError('Este cierre corresponde al flujo anterior.',404)
    declaration=json.loads(draft['declaration_json']);frozen=latest(db,row['id']);submitted=verify_submission(db,frozen) if frozen else None
    history=[verify_submission(db,r) for r in db.execute('SELECT * FROM cv2_submissions WHERE closure_id=? ORDER BY number',(row['id'],))]
    events=[dict(id=e['id'],kind=e['kind'],submission_id=e['submission_id'],detail=json.loads(e['detail_json']),actor=e['actor'],created_at=e['created_at']) for e in db.execute('SELECT * FROM cv2_events WHERE closure_id=? ORDER BY id',(row['id'],))]
    state=draft['state'];declaration=submitted['snapshot']['declaration'] if state=='submitted' and submitted else declaration
    calculated=submitted['snapshot']['summary'] if state=='submitted' and submitted else summary(db,row,declaration)
    deposit=next((e for e in reversed(events) if e['kind']=='deposit_safe' and submitted and e['submission_id']==submitted['id']),None)
    delivery=db.execute('SELECT * FROM cl_deliveries WHERE closure_id=?',(row['id'],)).fetchone()
    bag=bags.project(bags.row_for(db,row['bag_id']),db) if row['bag_id'] else None
    return dict(declaration, id=row['id'],revision=draft['revision'],version=row['version'],status=state,summary=calculated,
        live_summary=summary(db,row,declaration) if state=='submitted' else calculated,
        reference_attachments=[dict(a) for a in db.execute('SELECT a.id,a.name,a.mime,a.bytes,a.sha256,i.kind FROM cl_attachments a LEFT JOIN cv2_images i ON i.attachment_id=a.id WHERE a.closure_id=?',(row['id'],))],
        responsible=submitted['snapshot']['responsible'] if submitted and state=='submitted' else json.loads(row['responsible_json']),folio=submitted['folio'] if submitted else None,
        signature=submitted['signature'] if submitted and state=='submitted' else None,
        frozen_submission=dict(submitted['snapshot'],signature=submitted['signature']) if submitted else None,
        submitted_history=history,events=events,deposit=deposit,deposit_status='Depósito declarado' if deposit else 'Depósito pendiente',
        delivery=dict(delivery) if delivery else None,bag=bag,can_write=enabled(),
        can_revise=enabled() and state=='submitted' and not deposit and (not delivery or delivery['state']=='prepared') and (not bag or bag['verified_amount'] is None),
        server_confirmed=True,server_time=cl.stamp())


def view(store,cid,account):
    with store.connect() as db:
        db.execute('BEGIN');return view_db(db,cl.get(db,cid,account))


def legacy_calculation(db,row):
    draft=db.execute('SELECT * FROM cv2_drafts WHERE closure_id=?',(row['id'],)).fetchone()
    d=json.loads(draft['declaration_json']);s=summary(db,row,d)
    submitted=latest(db,row['id'])
    if draft['state']=='submitted' and submitted:
        frozen=verify_submission(db,submitted)['snapshot'];s=frozen['summary'];d=frozen['declaration']
    methods=[]
    for m in calc.empty(2)['methods']:
        total=s['mp_total'] if m['key']=='point_smart' else None
        methods.append(dict(m,total=total,independent_difference=None,comparison_state='pending',bank_settlement=None,split_matches=None))
    return dict(A=s['A'],B=s['B'],C=s['C'],post_count_net=0,cash_at_seal=s['A'],before_count_net=None,
        expected=s['expected'],difference=s['difference'],methods=methods,all_methods_total=None,
        coverage='reviewed' if s['expected'] is not None else 'incomplete',review_matches=bool(s['cash_reference']),source_matches=bool(s['cash_reference']),
        missing=s['missing'],warnings=[s['movement_notice'],'Fuentes sin alcance comprobado permanecen pendientes.'],can_submit=s['can_submit'],can_reconcile=False,
        source_reference=s['cash_reference'],imports=[],notice='V2: bolsa C = A − B. Firma y depósito no acreditan recepción ni conciliación.',v2=True,terminals=s['terminals'])


def update_legacy(db,row,d,account,action,reason=''):
    # Preserve all legacy movements, imports and evidence; only bridge the V2 declaration.
    legacy=json.loads(row['data_json']);legacy.update(cash=d['cash'],retained=d['retained'],note=d['note'],difference_explanation=d['note'],counted_at=d['identity']['counted_at'])
    db.execute('UPDATE cl_closures SET business_date=?,register_id=?,data_json=?,version=version+1,last_editor=?,updated_at=? WHERE id=?',
        (d['identity']['business_date'],d['identity']['register_id'],bags.canonical(legacy),cl.actor(account),cl.stamp(),row['id']))
    return cl.version(db,cl.get(db,row['id'],account),action,account,reason)


def review_fingerprint(snapshot):
    value=json.loads(canonical(snapshot))
    value['summary'].get('source_read',{}).pop('read_id',None)
    return digest(value)


def review_snapshot(db,row,d,account):
    s=summary(db,row,d)
    return dict(protocol='oveja-cierre-v2/1',closure_id=row['id'],declaration=d,summary=s,
                responsible=json.loads(row['responsible_json']),reviewer=cl.actor(account))


def upload(db,row,payload,account):
    kind=payload.get('kind')
    if kind not in ('mp','toteat'):raise DomainError('Selecciona el tipo de respaldo.')
    mime=payload.get('file_type');name=text(payload.get('file_name'),'Nombre de imagen',120,True)
    if mime not in ('image/jpeg','image/png','image/webp'):raise DomainError('Usa una foto JPG, PNG o WebP.')
    try:body=base64.b64decode(payload.get('file_data',''),validate=True)
    except (ValueError,TypeError):raise DomainError('Imagen no válida.') from None
    if not 0<len(body)<=MAX_IMAGE:raise DomainError('La foto comprimida admite hasta 3 MiB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(body)) as image:
                if Image.MIME.get(image.format)!=mime or image.width<1 or image.height<1 or image.width*image.height>5000000:raise ValueError()
                width,height=image.size;image.verify()
            with Image.open(BytesIO(body)) as image:image.load()
    except Exception:
        raise DomainError('El contenido o las dimensiones de la imagen no son válidos.') from None
    sha=hashlib.sha256(body).hexdigest()
    prior=db.execute('SELECT * FROM cl_attachments WHERE closure_id=? AND sha256=?',(row['id'],sha)).fetchone()
    aid=prior['id'] if prior else uuid4().hex
    if prior:
        p=db.execute('SELECT kind FROM cv2_images WHERE attachment_id=?',(aid,)).fetchone()
        if not p or p['kind']!=kind:raise DomainError('Ese archivo ya está asociado a otro tipo de respaldo.')
    else:
        count=db.execute('SELECT COUNT(*) FROM cv2_images WHERE closure_id=?',(row['id'],)).fetchone()[0]
        if count>=48:raise DomainError('Este cierre alcanzó el límite de archivos conservados; solicita revisión administrativa.')
        db.execute('INSERT INTO cl_attachments VALUES(?,?,?,?,?,?,?,?,?)',(aid,row['id'],name,mime,sha,len(body),body,cl.actor(account),cl.stamp()))
        db.execute('INSERT INTO cv2_images VALUES(?,?,?,?,?)',(aid,row['id'],kind,width,height))
        event(db,row['id'],'attachment',dict(id=aid,kind=kind,sha256=sha),account)
    return dict(id=aid,kind=kind,name=prior['name'] if prior else name,type=mime,size=len(body),width=width,height=height,sha256=sha,legible=False)


def strokes(raw):
    if not isinstance(raw,list) or not 1<=len(raw)<=100:raise DomainError('Dibuja tu firma.')
    count=0;lines=[]
    for line in raw:
        if not isinstance(line,list) or len(line)>5000:raise DomainError('Firma demasiado extensa.')
        points=[]
        for p in line:
            if isinstance(p,list) and len(p)==2:p=dict(x=p[0],y=p[1])
            if not isinstance(p,dict) or set(p)-{'x','y','t'}:raise DomainError('Trazo de firma no válido.')
            for k in ('x','y'):
                if type(p.get(k)) not in (float,int) or not math.isfinite(p[k]) or not 0<=p[k]<=1:raise DomainError('Coordenada de firma no válida.')
            points.append(dict(x=round(p['x'],6),y=round(p['y'],6)))
        count+=len(points);lines.append(points)
    if count>10000 or not any(len({(p['x'],p['y']) for p in line})>=2 for line in lines):raise DomainError('La firma está vacía o supera el límite.')
    return lines


def command(store,action,payload,account,cid=None,identity_check=None):
    cl.allow(account)
    fields={'create':('identity','bag_id'), 'save':('declaration',), 'review':(), 'submit':('review_token','strokes','consent'),
        'attach':('kind','file_name','file_type','file_data'), 'reference-attach':('kind','file_name','file_type','file_data'), 'revise':('reason',), 'deposit':('amount','bag_number','deposit_confirmed'),
        'compare':(), 'cash-reference':('opening','net_sales','movement_net','basis','evidence','attachment_id','opening_verified','sales_verified','movements_verified')}
    if action not in fields:raise DomainError('Operación V2 no disponible.',404)
    calc.shape(payload,('request_id','version','confirmed',*fields[action]),'Operación V2')
    if payload.get('confirmed') is not True:raise DomainError('Confirma expresamente la operación.')
    rid=payload.get('request_id')
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',rid):raise DomainError('Falta la clave de operación.')
    who=cl.actor(account);signature=digest([action,cid,payload,who])
    if action in ('cash-reference','compare','reference-attach') and not cl.partner(account):raise DomainError('Esta acción requiere una cuenta de socio.',403)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if identity_check and not identity_check(db):raise DomainError('La sesión venció o fue revocada.',401)
        if cid:row=cl.get(db,cid,account)
        old=db.execute('SELECT * FROM cv2_requests WHERE actor=? AND request_id=?',(who,rid)).fetchone()
        if old:
            if not hmac.compare_digest(old['signature'],signature):raise DomainError('La clave ya se usó con otros datos.',409)
            return json.loads(old['response_json'])
        if not enabled() and action not in ('compare',):raise DomainError('Las nuevas operaciones V2 están pausadas. El historial permanece disponible.',409)
        now=cl.stamp()
        if action=='create':
            if account.get('shared'):raise DomainError('Usa el acceso existente para declarar la persona responsable de esta cuenta compartida.',409)
            cid=uuid4().hex;given=calc.shape(payload.get('identity'),('business_date','register_id'),'Identificación')
            ident=cl.identity(db,dict(given,shift_name='Entrega '+cid[:8]))
            responsible=dict(key=who,name=account.get('name') or account['username'],shared=False)
            db.execute("INSERT INTO cl_closures(id,business_date,register_id,shift_name,shift_key,creator_key,responsible_json,last_editor,status,version,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'draft',1,?,?,?)",
                (cid,ident['business_date'],ident['register_id'],ident['shift_name'],cid,who,bags.canonical(responsible),who,bags.canonical(calc.empty(2)),now,now))
            row=cl.get(db,cid,account);d=empty(row)
            if payload.get('bag_id'):
                cl.reserve_bag(db,row,payload['bag_id']);d['bagNumber']=bags.project(bags.row_for(db,row['bag_id']),db)['label']
            d=normalize(db,row,d)
            db.execute("INSERT INTO cv2_drafts VALUES(?,?,1,'draft',?)",(cid,canonical(d),now))
            cl.version(db,row,'create',account,'Cierre digital V2 de prueba semanal');event(db,cid,'created',{},account)
            result=view_db(db,cl.get(db,cid,account))
        else:
            saved=db.execute('SELECT * FROM cv2_drafts WHERE closure_id=?',(cid,)).fetchone()
            if not saved:raise DomainError('Este cierre corresponde al flujo anterior.',404)
            if type(payload.get('version')) is not int or payload['version']!=row['version']:raise DomainError('El cierre cambió en otra sesión. Vuelve a abrirlo sin sobrescribir los datos.',409)
            d=json.loads(saved['declaration_json'])
            if action in ('save','attach','review','submit') and saved['state']!='draft':raise DomainError('La presentación es inmutable. Abre una corrección autorizada.',409)
            if action=='save':
                d=normalize(db,row,payload.get('declaration'),d)
                if canonical(d)!=saved['declaration_json']:
                    db.execute('UPDATE cv2_drafts SET declaration_json=?,revision=revision+1,updated_at=? WHERE closure_id=?',(canonical(d),now,cid))
                    event(db,cid,'draft_changed',dict(previous_revision=saved['revision'],review_invalidated=True,signature_requires_recapture=True),account)
                    row=update_legacy(db,row,d,account,'save')
                result=view_db(db,row)
            elif action in ('attach','reference-attach'):
                result=upload(db,row,payload,account)
            elif action=='review':
                snap=review_snapshot(db,row,d,account)
                if not snap['summary']['can_submit']:raise DomainError(' '.join(snap['summary']['missing']))
                number=int(d['bagNumber'].split('-')[-1]);bag=db.execute('SELECT * FROM cash_bags WHERE number=?',(number,)).fetchone()
                if not bag or bag['status']=='void':raise DomainError('El número no corresponde a una bolsa disponible.')
                other=db.execute('SELECT id FROM cl_closures WHERE bag_id=?',(bag['id'],)).fetchone()
                if (other and other[0]!=cid) or (bag['status']!='blank' and row['bag_id']!=bag['id']):raise DomainError('La bolsa ya está utilizada o reservada.',409)
                if row['bag_id'] and row['bag_id']!=bag['id']:raise DomainError('Este cierre ya está reservado a otra bolsa.',409)
                token=secrets.token_urlsafe(32)
                expiry=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(timespec='seconds')
                db.execute('INSERT INTO cv2_reviews VALUES(?,?,?,?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),cid,saved['revision'],who,review_fingerprint(snap),canonical(snap),now,expiry))
                result=dict(token=token,summary=snap['summary'],snapshot=snap,expires_at=expiry)
            elif action=='submit':
                token=payload.get('review_token')
                if not isinstance(token,str) or len(token)>100:raise DomainError('Abre la revisión final antes de firmar.')
                review=db.execute('SELECT * FROM cv2_reviews WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
                snap=review_snapshot(db,row,d,account)
                if not review or review['closure_id']!=cid or review['actor']!=who or review['revision']!=saved['revision'] or review['expires_at']<now or review['fingerprint']!=review_fingerprint(snap):raise DomainError('La revisión venció o los datos/fuentes cambiaron. Revisa y firma nuevamente.',409)
                snap=json.loads(review['snapshot_json'])
                if payload.get('consent') is not True:raise DomainError('Marca la conformidad de esta versión.')
                lines=strokes(payload.get('strokes'));number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM cv2_submissions WHERE closure_id=?',(cid,)).fetchone()[0]
                number_on_bag=int(d['bagNumber'].split('-')[-1]);bag=db.execute('SELECT id FROM cash_bags WHERE number=?',(number_on_bag,)).fetchone()
                if not bag:raise DomainError('Bolsa no encontrada.')
                cl.reserve_bag(db,row,bag['id'])
                cl.sync_bag(db,row,dict(A=snap['summary']['A'],B=snap['summary']['B'],C=snap['summary']['C'],cash_at_seal=snap['summary']['A'],post_count_net=0),account,'Presentación firmada V2')
                db.execute("INSERT OR IGNORE INTO cl_deliveries(closure_id,bag_id,state) VALUES(?,?,'prepared')",(cid,row['bag_id']))
                sid=uuid4().hex;folio='CV2-'+cid.upper()+'-'+str(number)
                snap.update(submission_id=sid,submission_version=number,folio=folio,submitted_at_server=now,submitted_by_user_id=who)
                sha=digest(snap);sig=dict(strokes=lines,consent=True,signer_key=who,signer_name=account.get('name') or account['username'],signed_at=now,version=number,snapshot_hash=sha,valid=True,notice='Constancia dibujada; no certifica identidad ni validez jurídica.')
                db.execute('INSERT INTO cv2_submissions VALUES(?,?,?,?,?,?,?,?,?,?)',(sid,cid,number,folio,canonical(snap),sha,canonical(sig),digest([snap,sig]),who,now))
                db.execute("UPDATE cv2_drafts SET state='submitted',updated_at=? WHERE closure_id=?",(now,cid))
                db.execute("UPDATE cl_closures SET status='pending',submitted_at=COALESCE(submitted_at,?) WHERE id=?",(now,cid))
                row=update_legacy(db,row,d,account,'submit','Presentación V2 con firma; depósito pendiente')
                event(db,cid,'submitted',dict(folio=folio,snapshot_hash=sha),account,sid);result=view_db(db,row)
            elif action=='revise':
                reason=text(payload.get('reason'),'Motivo de corrección',1500,True);current=view_db(db,row)
                if not current['can_revise']:raise DomainError('Después del depósito, entrega o conteo, conserva la versión custodiada y solicita rectificación administrativa.',409)
                if not cl.partner(account) and current['signature']['signer_key']!=who:raise DomainError('Solo quien firmó puede corregir su declaración antes del depósito.',403)
                db.execute("UPDATE cv2_drafts SET state='draft',revision=revision+1,updated_at=? WHERE closure_id=?",(now,cid))
                db.execute("UPDATE cl_closures SET status='draft' WHERE id=?",(cid,))
                row=update_legacy(db,row,d,account,'save',reason)
                event(db,cid,'signature_invalidated_for_revision',dict(reason=reason,previous_snapshot_hash=current['signature']['snapshot_hash']),account,latest(db,cid)['id']);result=view_db(db,row)
            elif action=='deposit':
                current=view_db(db,row);last=latest(db,cid)
                if saved['state']!='submitted' or not last:raise DomainError('Primero presenta y firma el cierre.')
                if payload.get('deposit_confirmed') is not True or payload.get('bag_number')!=current['bagNumber'] or integer(payload.get('amount'),'Monto depositado',False)!=current['summary']['C']:raise DomainError('Confirma exactamente el número de bolsa y C al depositar.')
                if current['deposit']:raise DomainError('El depósito ya está registrado.',409)
                if current['delivery'] and current['delivery']['state']!='prepared':raise DomainError('La bolsa ya está bajo custodia de entrega/recepción; conserva ese historial.',409)
                event(db,cid,'deposit_safe',dict(amount=current['summary']['C'],bag_number=current['bagNumber'],snapshot_hash=current['signature']['snapshot_hash'],confirmed=True),account,last['id'])
                result=view_db(db,row)
            elif action=='cash-reference':
                for k in ('opening_verified','sales_verified','movements_verified'):
                    if payload.get(k) is not True:raise DomainError('Verifica apertura, ventas netas y movimientos por separado.')
                if payload.get('basis')!='independent_net_sales_before_movements':raise DomainError('No se admite collection.cash ni una base económica incierta como esperado.')
                components={k:integer(payload.get(k),k,False,k=='movement_net') for k in ('opening','net_sales','movement_net')}
                expected=sum(components.values());integer(expected,'Esperado',False)
                evidence=text(payload.get('evidence'),'Reporte independiente y corte verificados',1500,True)
                attachment=db.execute('SELECT id FROM cl_attachments WHERE id=? AND closure_id=?',(payload.get('attachment_id'),cid)).fetchone()
                if not attachment or not d['identity']['counted_at'] or not comparison_scope(db,d)['session_key']:raise DomainError('Adjunta el reporte independiente, completa el conteo y resuelve la sesión inequívoca antes de verificar el esperado.')
                db.execute('INSERT INTO cv2_cash_references(closure_id,scope_json,components_json,expected,evidence,attachment_id,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',(cid,canonical(comparison_scope(db,d)),canonical(components),expected,evidence,attachment[0],who,now))
                event(db,cid,'cash_reference_reviewed',dict(scope=comparison_scope(db,d),expected=expected,evidence=evidence,attachment_id=attachment[0]),account)
                result=view_db(db,row)
            elif action=='compare':
                last=latest(db,cid)
                if not last:raise DomainError('Primero debe existir una presentación.')
                submitted=verify_submission(db,last);frozen=submitted['snapshot']
                comparison=source_summary(db,row,frozen['declaration'])
                comparison['difference']=frozen['summary']['A']-comparison['expected'] if comparison['expected'] is not None else None
                detail=dict(submission_hash=last['snapshot_hash'],comparison=comparison)
                if not db.execute("SELECT 1 FROM cv2_events WHERE submission_id=? AND kind='late_comparison' AND detail_json=?",(last['id'],canonical(detail))).fetchone():event(db,cid,'late_comparison',detail,account,last['id'])
                result=view_db(db,row)
        db.execute('INSERT INTO cv2_requests VALUES(?,?,?,?)',(who,rid,signature,canonical(result)))
        return result


def handle(handler,store,path,query,account,identity_check=None):
    cl.allow(account)
    match=re.fullmatch(PREFIX+'/('+cl.ID+r')(?:/(save|review|submit|attach|reference-attach|revise|deposit|compare|cash-reference|preview.pdf|download.pdf))?',path)
    if handler.command=='GET':
        if path==PREFIX:
            result=cl.listing(store,account)
            result.update(user=dict(name=account.get('name') or account['username'],role=account['role'],shared=bool(account.get('shared'))),can_write=enabled())
            return handler.send(200,result)
        if match:
            value=view(store,match[1],account)
            if match[2] in ('preview.pdf','download.pdf'):
                from erp.cashier_v2_pdf import render
                with store.connect() as db:
                    cl.get(db,match[1],account)
                    body=render(db,value,query.get('submission',[None])[0])
                return handler.send(200,body,'application/pdf',{'Content-Disposition':('inline' if match[2]=='preview.pdf' else 'attachment')+'; filename="cierre-v2-'+match[1][:8]+'.pdf"'})
            if not match[2]:return handler.send(200,value)
    if handler.command=='POST' and (match or path==PREFIX+'/create'):
        origin=handler.headers.get('Origin');host=handler.headers.get('Host','')
        if origin and origin not in ('http://'+host,'https://'+host):raise DomainError('Origen externo rechazado.',403)
        action=match[2] if match else 'create'
        try:return handler.send(200,command(store,action,handler.body(maximum=5*1024*1024 if action in ('attach','reference-attach') else 1024*1024),account,match[1] if match else None,identity_check))
        except sqlite3.IntegrityError:raise DomainError('La bolsa u operación ya cambió. Abre el cierre y revisa el estado guardado.',409) from None
    raise DomainError('Ruta V2 no disponible.',404)
