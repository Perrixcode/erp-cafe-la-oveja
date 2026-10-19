"""Persistent review drafts, closure and immutable replacement of purchase files."""
import hashlib
import json
from collections import Counter, defaultdict
from erp import payables as ap
from erp.domain import DomainError
from erp.store import now

REASONS = {'paid':'Pagada anteriormente','cash':'Compra al contado','not_ours':'No corresponde','duplicate':'Documento duplicado','other':'Otro'}
FOLLOW_UP = {'':'Sin gestión pendiente','supplier':'Consultar al proveedor','credit_note':'Esperando nota de crédito','other':'Otra gestión'}
FILTERS = ('pending','attention','selected','registered','discarded','all')


def linked_rows(db, batch):
    return {r[0] for r in db.execute('SELECT source_row FROM ap_invoice_sources WHERE batch_id=? UNION SELECT source_row FROM ap_import_resolutions WHERE batch_id=?', (batch, batch))}


def source(db, batch, *, allow_trashed=False):
    result=db.execute('SELECT * FROM ap_import_batches WHERE id=?',(batch,)).fetchone()
    if not result: raise DomainError('Archivo no encontrado.',404)
    if not allow_trashed and db.execute('SELECT 1 FROM ap_import_trash WHERE batch_id=?',(batch,)).fetchone():
        raise DomainError('Este archivo está en la papelera. Restáuralo desde Archivos SII para continuar.',409)
    return result


def state(db, batch):
    row=db.execute('SELECT * FROM ap_import_reviews WHERE batch_id=?',(batch,)).fetchone()
    return dict(row) if row else dict(batch_id=batch,version=0,draft_json='{}',replaced_by=None,closed_at=None,closed_by=None,updated_at=None)


def active(review, *, allow_closed=False):
    if review['replaced_by']: raise DomainError('Este archivo fue reemplazado. Abre la versión vigente para continuar.',409)
    if review['closed_at'] and not allow_closed: raise DomainError('La revisión está cerrada. Reábrela antes de modificarla.',409)


def clean_draft(value, rows, handled, *, strict=False):
    candidates={r['row']:r for r in rows}; keys={str(n) for n in candidates}
    def numbers(key):
        items=value.get(key,[])
        if not isinstance(items,list) or len(items)>3000 or any(type(n) is not int or n not in candidates for n in items) or len(set(items))!=len(items):
            raise DomainError('El avance contiene filas no válidas.')
        return set(items)-handled
    selected,discarded=numbers('selected'),numbers('discarded')
    if selected & discarded: raise DomainError('Una fila descartada no puede estar seleccionada.')
    conditions,overrides,details=(value.get(k,{}) for k in ('conditions','overrides','details'))
    if any(not isinstance(v,dict) or any(k not in keys for k in v) for v in (conditions,overrides,details)):
        raise DomainError('El avance contiene datos o filas no válidos.')
    for condition in conditions.values(): ap.choice(condition,ap.CONDITIONS,'Condición')
    clean_details={}
    for key,detail in details.items():
        if not isinstance(detail,dict): raise DomainError('La nota de revisión no es válida.')
        note=detail.get('note','')
        if not isinstance(note,str) or len(note)>1000 or any(ord(c)<32 and c not in '\n\t' for c in note):
            raise DomainError('La nota debe tener hasta 1.000 caracteres.')
        follow=ap.choice(detail.get('follow_up',''),FOLLOW_UP,'Gestión pendiente')
        reason=ap.choice(detail.get('discard_reason',''),{'':'',**REASONS},'Motivo de descarte')
        clean_details[key]=dict(note=note.strip(),follow_up=follow,discard_reason=reason)
    for number in discarded:
        detail=clean_details.get(str(number),{})
        if not detail.get('discard_reason') or (detail['discard_reason']=='other' and not detail['note']):
            raise DomainError('Indica el motivo del descarte y una nota si eliges Otro.')
    approved={};invalidated=[]
    for number in list(selected):
        row=candidates[number];decision=overrides.get(str(number))
        valid=row['status']=='new' and not decision
        if isinstance(decision,dict):
            valid=(row.get('can_override') and decision.get('confirmed') is True and decision.get('action')==row.get('review_action') and decision.get('token')==row.get('review_token'))
            if valid:
                approved[str(number)]=dict(confirmed=True,action=decision['action'],token=decision['token'],reason=ap.text(decision.get('reason'),'Motivo de la excepción',True))
        if not valid:
            if strict: raise DomainError('Cambió una factura seleccionada. Recarga la revisión para comprobarla nuevamente.',409)
            selected.remove(number);invalidated.append(number)
    view=value.get('view',{})
    if not isinstance(view,dict): raise DomainError('Vista no válida.')
    view=dict(search=ap.text(view.get('search',''),'Búsqueda',maximum=160), supplier=ap.text(view.get('supplier',''),'Proveedor',maximum=200),
              filter=ap.choice(view.get('filter','pending'),FILTERS,'Filtro'),page=ap.integer(view.get('page',1),'Página',maximum=3000))
    return dict(selected=sorted(selected),discarded=sorted(discarded),conditions={k:v for k,v in conditions.items() if int(k) not in handled},
                overrides=approved,details=clean_details,view=view),invalidated


def decorate(db, batch, rows):
    review=state(db,batch);handled=linked_rows(db,batch)
    draft,invalidated=clean_draft(json.loads(review['draft_json']),rows,handled)
    for row in rows:
        detail=draft['details'].get(str(row['row']),{})
        row['review_state']=('imported' if row['row'] in handled else 'discarded' if row['row'] in draft['discarded'] else
                             'registered' if row['status']=='duplicate' else 'attention' if detail.get('follow_up') else 'pending')
    progress={key:dict(count=0,amount=0,unknown_amount=0) for key in ('pending','attention','imported','registered','discarded')}
    for row in rows:
        item=progress[row['review_state']];item['count']+=1
        if type(row.get('amount')) is int:
            if row.get('document_kind')=='invoice' and row['status']!='error': item['amount']+=row['amount']
        else:item['unknown_amount']+=1
    return dict(version=review['version'],replaced_by=review['replaced_by'],closed_at=review['closed_at'],closed_by=review['closed_by'],
                updated_at=review['updated_at'],draft=draft,invalidated=invalidated,total=len(rows),progress=progress,
                reasons=REASONS,follow_up=FOLLOW_UP)


def write(db,batch,rows,draft,actor,version):
    db.execute('''INSERT INTO ap_import_reviews(batch_id,version,total_rows,draft_json,actor,updated_at) VALUES(?,?,?,?,?,?)
       ON CONFLICT(batch_id) DO UPDATE SET version=excluded.version,total_rows=excluded.total_rows,draft_json=excluded.draft_json,actor=excluded.actor,updated_at=excluded.updated_at''',
       (batch,version,len(rows),json.dumps(draft,ensure_ascii=False,sort_keys=True),actor,now()))


def save(store,batch,data,actor):
    from erp import sii_purchases as sii
    ap.integer(data.get('version'),'Versión de la revisión',minimum=0)
    if not isinstance(data.get('draft'),dict):raise DomainError('El avance no es válido.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');src=source(db,batch);review=state(db,batch);active(review)
        rows=sii.classify(db,sii.parse(src['content']))
        draft,_=clean_draft(data['draft'],rows,linked_rows(db,batch),strict=True)
        before=json.loads(review['draft_json'])
        if data['version']!=review['version'] and draft!=before:
            raise DomainError('Otra sesión cambió el avance. Recarga el archivo para conservar la revisión más reciente.',409)
        if draft!=before:
            write(db,batch,rows,draft,actor,review['version']+1)
            changed = {}
            for field in ('selected','discarded'):
                old_set,new_set=set(before.get(field,[])),set(draft[field])
                if old_set!=new_set: changed[field]={'added':sorted(new_set-old_set),'removed':sorted(old_set-new_set)}
            for field in ('conditions','overrides','details'):
                old_map,new_map=before.get(field,{}),draft[field]
                delta={k:{'before':old_map.get(k),'after':new_map.get(k)} for k in old_map.keys()|new_map.keys() if old_map.get(k)!=new_map.get(k)}
                if delta: changed[field]=delta
            if changed: ap.event(db,'sii_review_saved',actor,after={'batch':batch,'changes':changed})
        return {'version':state(db,batch)['version']}


def after_confirm(db,batch,rows,actor):
    review=state(db,batch)
    draft,_=clean_draft(json.loads(review['draft_json']),rows,linked_rows(db,batch))
    write(db,batch,rows,draft,actor,review['version']+1)


def listing(db):
    batches=[dict(r) for r in db.execute('''SELECT b.id,b.name,b.created_at,COALESCE(r.version,0) AS version,r.total_rows,r.draft_json,r.replaced_by,r.closed_at,r.updated_at,t.deleted_at,t.deleted_by
        FROM ap_import_batches b LEFT JOIN ap_import_reviews r ON r.batch_id=b.id LEFT JOIN ap_import_trash t ON t.batch_id=b.id ORDER BY COALESCE(r.updated_at,b.created_at) DESC,b.id''')]
    for batch in batches:
        draft=json.loads(batch.pop('draft_json') or '{}');handled=linked_rows(db,batch['id'])
        batch.update(imported=len(handled),discarded=len(set(draft.get('discarded',[]))-handled),selected=len(set(draft.get('selected',[]))-handled))
    return batches


def set_trashed(store,batch,data,actor):
    """Reversible removal of the workspace file; keep all source/duplicate evidence."""
    from erp import sii_purchases as sii
    if type(data.get('trashed')) is not bool or data.get('confirmed') is not True:
        raise DomainError('Confirma si deseas eliminar o restaurar el archivo.')
    version=ap.integer(data.get('version'),'Versión de la revisión',minimum=0)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');src=source(db,batch,allow_trashed=True);review=state(db,batch)
        trashed=bool(db.execute('SELECT 1 FROM ap_import_trash WHERE batch_id=?',(batch,)).fetchone())
        if trashed==data['trashed']:
            return dict(trashed=trashed,version=review['version'])
        if version!=review['version']:
            raise DomainError('El archivo cambió en otra sesión. Vuelve al listado y revisa su avance antes de continuar.',409)
        write(db,batch,sii.parse(src['content']),json.loads(review['draft_json']),actor,version+1)
        if data['trashed']:
            db.execute('INSERT INTO ap_import_trash VALUES(?,?,?)',(batch,now(),actor))
        else:
            db.execute('DELETE FROM ap_import_trash WHERE batch_id=?',(batch,))
        ap.event(db,'sii_file_trashed' if data['trashed'] else 'sii_file_restored',actor,
                 after={'batch':batch,'name':src['name'],'incorporated':len(linked_rows(db,batch))})
    return dict(trashed=data['trashed'],version=version+1)


def set_closed(store,batch,data,actor):
    from erp import sii_purchases as sii
    if type(data.get('closed')) is not bool or data.get('confirmed') is not True:raise DomainError('Confirma el cierre o reapertura de la revisión.')
    ap.integer(data.get('version'),'Versión de la revisión',minimum=0)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');src=source(db,batch);review=state(db,batch);active(review,allow_closed=True)
        if bool(review['closed_at'])==data['closed']:return {'closed':data['closed']}
        if review['version']!=data['version']:raise DomainError('El avance cambió. Recarga antes de continuar.',409)
        rows=sii.classify(db,sii.parse(src['content']));decorated=decorate(db,batch,rows)
        if data['closed'] and (any(decorated['progress'][k]['count'] for k in ('pending','attention')) or decorated['draft']['selected'] or any(d.get('follow_up') for n,d in decorated['draft']['details'].items() if int(n) not in decorated['draft']['discarded'])):
            raise DomainError('Resuelve las facturas pendientes y las gestiones abiertas antes de cerrar la revisión.',409)
        write(db,batch,rows,decorated['draft'],actor,review['version']+1)
        db.execute('UPDATE ap_import_reviews SET closed_at=?,closed_by=? WHERE batch_id=?',(now() if data['closed'] else None,actor if data['closed'] else None,batch))
        ap.event(db,'sii_review_closed' if data['closed'] else 'sii_review_reopened',actor,after={'batch':batch})
    return {'closed':data['closed']}


def identity(row):
    if row['status'] in ('error','unsupported'):return None
    from erp.payable_duplicates import folio
    return row.get('tax_id'),row.get('document_type'),folio(row.get('folio'))


def fingerprint(row):
    key=identity(row)
    return key+tuple(row.get(k) for k in ('amount','issued_on')) if key else None


def comparison(db,batch,content,name,version):
    from erp import sii_purchases as sii
    ap.integer(version,'Versión de la revisión',minimum=0);name=ap.text(name,'Archivo',True,160)
    src=source(db,batch);review=state(db,batch);active(review)
    if review['version']!=version:raise DomainError('El avance cambió. Recarga antes de comparar el archivo.',409)
    new=sii.parse(content);old=sii.parse(src['content']);key=hashlib.sha256(content).hexdigest()
    if key!=batch and db.execute('SELECT 1 FROM ap_import_batches WHERE id=?',(key,)).fetchone():
        raise DomainError('Ese archivo ya está guardado. Ábrelo desde Archivos SII; el actual se conserva.',409)
    old_groups=defaultdict(list);new_groups=defaultdict(list)
    for row in old:old_groups[identity(row)].append(row)
    for row in new:new_groups[identity(row)].append(row)
    details=[];same={}
    for row in new:
        match=old_groups[identity(row)]
        if identity(row) is None or len(match)>1 or len(new_groups[identity(row)])>1:kind='ambiguous'
        elif not match:kind='new'
        elif fingerprint(row)==fingerprint(match[0]):kind='same';same[row['row']]=match[0]['row']
        else:kind='changed'
        details.append(dict(kind=kind,row=row['row'],name=row.get('name','Sin nombre'),folio=row.get('folio',''),tax_id=row.get('tax_id',''),amount=row.get('amount'),date=row.get('issued_on',''),before_amount=match[0].get('amount') if len(match)==1 else None,before_date=match[0].get('issued_on') if len(match)==1 else None))
    for row in old:
        if identity(row) is not None and identity(row) not in new_groups:
            details.append(dict(kind='missing',row=row['row'],name=row.get('name',''),folio=row.get('folio',''),tax_id=row.get('tax_id',''),amount=row.get('amount'),date=row.get('issued_on','')))
    counts=Counter(r['kind'] for r in details)
    token=hashlib.sha256(json.dumps([batch,version,key,name],ensure_ascii=False).encode()).hexdigest()
    return dict(id=key,name=name,same_file=key==batch,version=version,token=token,total=len(new),
                counts={k:counts[k] for k in ('same','changed','new','missing','ambiguous')},rows=details),same,new


def compare(store,batch,content,name,version):
    with store.connect() as db:
        db.execute('BEGIN')
        return comparison(db,batch,content,name,version)[0]


def replace(store,batch,content,name,actor,version,token):
    from erp import sii_purchases as sii
    key=hashlib.sha256(content).hexdigest()
    ap.integer(version,'Versión de la revisión',minimum=0);name=ap.text(name,'Archivo',True,160)
    expected=hashlib.sha256(json.dumps([batch,version,key,name],ensure_ascii=False).encode()).hexdigest()
    if not token or token!=expected:raise DomainError('Compara el archivo y confirma el reemplazo antes de continuar.',409)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');source(db,batch);review=state(db,batch)
        if review['replaced_by']==key:pass
        else:
            result,same,new=comparison(db,batch,content,name,version)
            if key!=batch:
                before=json.loads(review['draft_json']);handled=linked_rows(db,batch)
                draft=dict(selected=[],discarded=[],conditions={},overrides={},details={},view={})
                for new_row,old_row in same.items():
                    if old_row in before.get('discarded',[]) and old_row not in handled:draft['discarded'].append(new_row)
                    for field in ('details','conditions'):
                        if str(old_row) in before.get(field,{}):draft[field][str(new_row)]=before[field][str(old_row)]
                db.execute('INSERT INTO ap_import_batches VALUES(?,?,?,?,?)',(key,name,content,actor,now()))
                write(db,key,new,draft,actor,1)
                old=sii.parse(source(db,batch)['content']);write(db,batch,old,before,actor,review['version']+1)
                db.execute('UPDATE ap_import_reviews SET replaced_by=? WHERE batch_id=?',(key,batch))
                ap.event(db,'sii_file_replaced',actor,before={'batch':batch},after={'batch':key,'comparison':result['counts'],'discarded_carried':draft['discarded']})
    return sii.preview(store,key)
