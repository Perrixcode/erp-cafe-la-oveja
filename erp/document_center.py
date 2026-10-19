"""Documents and checklists. Explicit migration; immutable versions; existing roles."""
import base64
import binascii
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import unicodedata
import uuid
import zlib
from urllib.parse import quote
from erp.domain import DomainError
from erp.store import now
from erp import document_xlsx

MAX_FILE = 8 * 1024 * 1024
MAX_UPLOAD_JSON = 12 * 1024 * 1024
LEGACY_PATH = 'private/documents/salon/Conteo_semanal_lunes_a_viernes.pdf'
LEGACY_SHA = '83c97b2e0293b90f4db72f280be79c9718bc94641abfc35cee9ea0eac3679487'
LEGACY_ID = 'salon-conteo-semanal'
LABELS_PATH = 'private/documents/salon/bolsas-000001-000020-qr.pdf'
LABELS_SHA = '624b3fb49b1fdd061808741cf83805c1e1cc4390a9790f5294f9a755a4223b64'
LABELS_ID = 'salon-etiquetas-bolsas-000001-000020'
SECTIONS = [('salon','Salón'),('formatos','Formatos'),('checklists','Checklists'),('reglamento-interno','Reglamento interno'),('protocolos','Protocolos'),('manuales','Manuales'),('control-de-versiones','Control de versiones'),('constancias-de-lectura','Constancias de lectura')]
KINDS = {'document','checklist_template','checklist'}
STATES = {'draft','active','archived'}


def normalize(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c)).split())


def text(value, label, limit=160, required=True, multiline=False):
    if not isinstance(value, str):
        raise DomainError(f'{label}: se espera texto.')
    value = unicodedata.normalize('NFC', value).strip()
    if (required and not value) or len(value)>limit or any((ord(c)<32 and not(multiline and c=='\n')) or ord(c)==127 or unicodedata.category(c)=='Cf' for c in value):
        raise DomainError(f'{label}: revisa el contenido (máximo {limit} caracteres).')
    return value


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',value):
        raise DomainError('Identificador no válido.')
    return value


def revision(value):
    if type(value) is not int or value<1:raise DomainError('Falta la revisión del documento.')
    return value


def checklist_items(value):
    if not isinstance(value,list) or not 1<=len(value)<=100:raise DomainError('El checklist necesita entre 1 y 100 ítems.')
    return [text(v,'Ítem',240) for v in value]


def mime_type(content):
    if not 0<len(content)<=MAX_FILE:raise DomainError('El archivo debe tener entre 1 byte y 8 MB.',413)
    if content.startswith(b'PK\x03\x04'):return document_xlsx.validate(content)
    if content.startswith(b'%PDF-') and b'%%EOF' in content[-2048:]:
        # Reject obvious active payloads; the response remains sandboxed. Not an antivirus.
        decoded_names=re.sub(rb'#([0-9a-fA-F]{2})',lambda m:bytes([int(m[1],16)]),content)
        if re.search(rb'/(?:JavaScript|JS|Launch|EmbeddedFile|RichMedia|OpenAction|AA|XFA)\b',decoded_names):
            raise DomainError('El PDF contiene acciones o adjuntos activos. Exporta una copia simple.',415)
        return 'application/pdf'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        offset=8;seen_header=False;seen_data=False
        while offset+12<=len(content):
            length=struct.unpack('>I',content[offset:offset+4])[0]
            kind=content[offset+4:offset+8];end=offset+12+length
            if end>len(content):break
            chunk=content[offset+8:offset+8+length]
            if zlib.crc32(kind+chunk)&0xffffffff!=struct.unpack('>I',content[end-4:end])[0]:break
            if not seen_header:
                if kind!=b'IHDR' or length!=13:break
                width,height=struct.unpack('>II',chunk[:8])
                if not 0<width<=12000 or not 0<height<=12000 or width*height>24000000:raise DomainError('Imagen demasiado grande: máximo 24 megapíxeles.',413)
                seen_header=True
            elif kind==b'IHDR':break
            if kind==b'IDAT':seen_data=True
            if kind==b'IEND':
                if length==0 and end==len(content) and seen_data:return 'image/png'
                break
            offset=end
        raise DomainError('PNG dañado o no válido.',415)
    if content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9'):
        offset=2
        while offset+4<=len(content):
            if content[offset]!=255:break
            while offset<len(content) and content[offset]==255:offset+=1
            if offset>=len(content):break
            marker=content[offset];offset+=1
            if marker in (0xD8,0xD9,0x01) or 0xD0<=marker<=0xD7:continue
            if offset+2>len(content):break
            length=struct.unpack('>H',content[offset:offset+2])[0]
            if length<2 or offset+length>len(content):break
            if marker in (0xC0,0xC1,0xC2):
                if length<8:break
                height,width=struct.unpack('>HH',content[offset+3:offset+7])
                if not 0<width<=12000 or not 0<height<=12000 or width*height>24000000:raise DomainError('Imagen demasiado grande: máximo 24 megapíxeles.',413)
                if b'\xff\xda' not in content[offset+length:]:break
                return 'image/jpeg'
            if marker==0xDA:break
            offset+=length
        raise DomainError('JPG dañado o no válido.',415)
    raise DomainError('Solo se admiten PDF, PNG, JPG y XLSX válidos.',415)


def upload(value):
    if not isinstance(value,dict):raise DomainError('Selecciona un archivo.')
    name=text(value.get('name'),'Nombre del archivo',120)
    if any(c in name for c in ('/','\\',':')) or name.startswith('.') or name in ('.','..'):
        raise DomainError('Nombre del archivo no válido: usa solo el nombre, sin rutas.')
    encoded=value.get('base64')
    if not isinstance(encoded,str) or len(encoded)>4*((MAX_FILE+2)//3):raise DomainError('Archivo demasiado grande.',413)
    try:content=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise DomainError('Archivo codificado no válido.') from None
    mime=mime_type(content)
    if Path(name).suffix.casefold() not in {'application/pdf':{'.pdf'},'image/png':{'.png'},'image/jpeg':{'.jpg','.jpeg'},document_xlsx.MIME:{'.xlsx'}}[mime]:raise DomainError('La extensión no coincide con el contenido.',415)
    return {'name':name,'mime':mime,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'content':content}


def initialized(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dc_requests'").fetchone():
        raise DomainError('La gestión de documentos necesita completar su migración inicial.',503)


def event(db,doc_id,kind,actor,details):
    db.execute('INSERT INTO dc_events(document_id,event,actor,details_json,created_at) VALUES(?,?,?,?,?)',(doc_id,kind,actor,json.dumps(details,ensure_ascii=False),now()))


def migrate(store,root):
    """Explicit, additive and repeatable. The legacy PDF is referenced, never moved."""
    root=Path(root).resolve();legacy=root/LEGACY_PATH
    exists=legacy.exists()
    if legacy.is_symlink() or (exists and (not legacy.is_file() or not legacy.resolve().is_relative_to(root) or hashlib.sha256(legacy.read_bytes()).hexdigest()!=LEGACY_SHA)):
        raise DomainError('El PDF existente requiere revisión; no se ejecutó la migración.',409)
    labels=root/LABELS_PATH
    labels_exist=labels.exists()
    if labels.is_symlink() or (labels_exist and (not labels.is_file() or not labels.resolve().is_relative_to(root) or hashlib.sha256(labels.read_bytes()).hexdigest()!=LABELS_SHA)):
        raise DomainError('Las etiquetas existentes requieren revisión; no se ejecutó la migración.',409)
    with store.connect() as db:
        schema=Path(__file__).with_name('document_center.sql').read_text()
        db.executescript('BEGIN IMMEDIATE;\n'+schema)
        for position,(key,name) in enumerate(SECTIONS):
            db.execute('INSERT OR IGNORE INTO dc_sections VALUES(?,?,?,?,?,?)',(key,name,normalize(name),position,'migration',now()))
        if exists:
            db.execute('INSERT OR IGNORE INTO dc_assets VALUES(?,?,?,?,?)',(LEGACY_SHA,'application/pdf',legacy.stat().st_size,'legacy-salon',None))
            if not db.execute('SELECT 1 FROM dc_documents WHERE id=?',(LEGACY_ID,)).fetchone():
                timestamp=now()
                db.execute('INSERT INTO dc_documents VALUES(?,?,?,?,?,?,?,?,?)',(LEGACY_ID,'document','active',1,1,1,'migration',timestamp,timestamp))
                title='Conteo semanal de repostería y tortas — lunes a viernes'
                db.execute('INSERT INTO dc_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(LEGACY_ID,1,'salon',title,normalize(title),'3 páginas carta horizontal · 59 filas · Refrigerado / ambiente, congelación y total.',legacy.name,LEGACY_SHA,None,'migration',timestamp))
                event(db,LEGACY_ID,'imported','migration',{'source':'existing-salon','sha256':LEGACY_SHA})
        if labels_exist:
            db.execute('INSERT OR IGNORE INTO dc_assets VALUES(?,?,?,?,?)',(LABELS_SHA,'application/pdf',labels.stat().st_size,'legacy-labels',None))
            if not db.execute('SELECT 1 FROM dc_documents WHERE id=?',(LABELS_ID,)).fetchone():
                timestamp=now();title='Etiquetas de bolsas BOL-000001 a BOL-000020'
                description='20 etiquetas · 5 páginas carta · 4 por hoja · 7 × 12 cm. Imprimir una sola copia al 100%, sin ajustar. No reutilizar ni repetir correlativos. Los QR abren sus fichas de bolsa en el ERP; requieren sesión y lote importado. Mantener cierre en papel y etiquetas manuales como respaldo.'
                db.execute('INSERT INTO dc_documents VALUES(?,?,?,?,?,?,?,?,?)',(LABELS_ID,'document','active',1,1,1,'migration',timestamp,timestamp))
                db.execute('INSERT INTO dc_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(LABELS_ID,1,'salon',title,normalize(title),description,labels.name,LABELS_SHA,None,'migration',timestamp))
                event(db,LABELS_ID,'imported','migration',{'source':'existing-labels','sha256':LABELS_SHA})
        return {'sections':db.execute('SELECT count(*) FROM dc_sections').fetchone()[0],'legacy_imported':exists,'labels_imported':labels_exist,'documents':db.execute('SELECT count(*) FROM dc_documents').fetchone()[0]}


def require_section(db,value):
    key=identifier(value)
    if not db.execute('SELECT 1 FROM dc_sections WHERE id=?',(key,)).fetchone():raise DomainError('Destino no encontrado.',404)
    return key


def transaction(store,actor,operation,payload,callback):
    request_id=payload.get('request_id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',request_id):raise DomainError('Falta el identificador de la operación.')
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    with store.connect() as db:
        initialized(db);db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT * FROM dc_requests WHERE request_id=?',(request_id,)).fetchone()
        if existing:
            if (existing['actor'],existing['operation'],existing['payload_sha'])!=(actor,operation,digest):raise DomainError('Esta operación ya se usó con otro contenido.',409)
            return json.loads(existing['result_json'])
        result=callback(db)
        db.execute('INSERT INTO dc_requests VALUES(?,?,?,?,?,?)',(request_id,actor,operation,digest,json.dumps(result),now()))
        return result


def create_section(store,payload,actor):
    name=text(payload.get('name'),'Nombre de sección',60);key=normalize(name)
    def write(db):
        old=db.execute('SELECT id,name FROM dc_sections WHERE normalized_name=?',(key,)).fetchone()
        if old:return dict(old)
        if db.execute('SELECT count(*) FROM dc_sections').fetchone()[0]>=60:raise DomainError('Se alcanzó el máximo de 60 secciones.')
        section_id=uuid.uuid4().hex
        db.execute('INSERT INTO dc_sections VALUES(?,?,?,?,?,?)',(section_id,name,key,db.execute('SELECT COALESCE(MAX(position),0)+1 FROM dc_sections').fetchone()[0],actor,now()))
        return {'id':section_id,'name':name}
    return transaction(store,actor,'section.create',payload,write)


def row_document(db,doc_id):
    row=db.execute('SELECT * FROM dc_documents WHERE id=?',(identifier(doc_id),)).fetchone()
    if not row:raise DomainError('Documento no encontrado.',404)
    return dict(row)


def expect_document(db,doc_id,payload):
    doc=row_document(db,doc_id)
    if doc['revision']!=revision(payload.get('expected_revision')):raise DomainError('Otra persona actualizó este documento. Recarga antes de guardar.',409)
    return doc


def selected_version(db,doc,manager,requested=None):
    if not manager and doc['state']!='active':raise DomainError('Documento no encontrado.',404)
    selected=doc['latest_version'] if manager else doc['live_version']
    if requested is not None:
        try:requested=int(requested)
        except (ValueError,TypeError):raise DomainError('Versión no válida.') from None
        if not manager and requested!=selected:raise DomainError('Documento no encontrado.',404)
        selected=requested
    row=db.execute('SELECT v.*,a.mime,a.bytes,a.storage FROM dc_versions v LEFT JOIN dc_assets a ON a.sha256=v.asset_sha WHERE v.document_id=? AND v.version=?',(doc['id'],selected)).fetchone()
    if not row:raise DomainError('Versión no encontrada.',404)
    result=dict(row);raw_items=result.pop('items_json');result['items']=json.loads(raw_items) if raw_items else []
    result.pop('normalized_title');result.pop('storage');return result


def detail(store,doc_id,manager,requested=None):
    with store.connect() as db:
        initialized(db);doc=row_document(db,doc_id)
        value=selected_version(db,doc,manager,requested)
        result={**doc,**value}
        if not manager:
            result['latest_version']=doc['live_version'];result.pop('revision',None)
        if manager:
            result['versions']=[dict(r) for r in db.execute('SELECT version,title,section_id,filename,asset_sha,created_by,created_at FROM dc_versions WHERE document_id=? ORDER BY version DESC',(doc_id,))]
            result['events']=[{**dict(r),'details':json.loads(r['details_json'])} for r in db.execute('SELECT event,actor,details_json,created_at FROM dc_events WHERE document_id=? ORDER BY id DESC LIMIT 200',(doc_id,))]
            for entry in result['events']:entry.pop('details_json')
            result['runs']=[{**dict(r),'items':json.loads(r['items_json'])} for r in db.execute('SELECT * FROM dc_runs WHERE document_id=? ORDER BY created_at DESC,id DESC LIMIT 100',(doc_id,))]
            for entry in result['runs']:entry.pop('items_json')
        return result


def listing(store,manager,query):
    with store.connect() as db:
        initialized(db)
        sections=[dict(r) for r in db.execute('SELECT id,name FROM dc_sections ORDER BY position,name')]
        params=[];where=[]
        selected='d.latest_version' if manager else 'd.live_version'
        if not manager:where.append("d.state='active'")
        state=query.get('state',[''])[0]
        if manager and state:
            if state not in STATES:raise DomainError('Estado no válido.')
            where.append('d.state=?');params.append(state)
        section=query.get('section',[''])[0]
        if section:where.append('v.section_id=?');params.append(require_section(db,section))
        search=text(query.get('q',[''])[0],'Búsqueda',120,False)
        if search:
            where.append("(v.normalized_title LIKE ? ESCAPE '\\' OR v.description LIKE ? ESCAPE '\\')")
            escaped=normalize(search).replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            params.extend(['%'+escaped+'%']*2)
        try:page=int(query.get('page',['1'])[0])
        except ValueError:raise DomainError('Página no válida.') from None
        if not 1<=page<=100000:raise DomainError('Página no válida.')
        base=' FROM dc_documents d JOIN dc_versions v ON v.document_id=d.id AND v.version='+selected
        if where:base+=' WHERE '+' AND '.join(where)
        total=db.execute('SELECT count(*)'+base,params).fetchone()[0]
        rows=db.execute('SELECT d.id,d.kind,d.state,d.revision,d.latest_version,d.live_version,v.version,v.title,v.description,v.section_id,v.filename,v.created_at'+base+' ORDER BY d.updated_at DESC,d.id LIMIT 100 OFFSET ?',params+[(page-1)*100]).fetchall()
        documents=[dict(r) for r in rows]
        if not manager:
            for d in documents:d['latest_version']=d['live_version'];d.pop('revision',None)
        return {'sections':sections,'documents':documents,'total':total,'page':page,'page_size':100,'can_manage':manager,'max_file_bytes':MAX_FILE}


def save_version(store,payload,actor,doc_id=None):
    title=text(payload.get('title'),'Título');description=text(payload.get('description',''),'Descripción',2000,False,True)
    kind=payload.get('kind');new_file=upload(payload['file']) if payload.get('file') is not None else None
    if kind not in KINDS:raise DomainError('Tipo de documento no válido.')
    items=checklist_items(payload.get('items')) if kind=='checklist' else None
    if kind=='checklist' and new_file:raise DomainError('Usa plantilla adjunta para un checklist en archivo.')
    if doc_id is None and kind!='checklist' and not new_file:raise DomainError('Selecciona un archivo.')
    target_state=payload.get('state','draft')
    if target_state not in STATES:raise DomainError('Estado no válido.')
    def write(db):
        section=require_section(db,payload.get('section_id'))
        doc=expect_document(db,doc_id,payload) if doc_id else None
        if doc and doc['kind']!=kind:raise DomainError('No se puede cambiar el tipo de un documento.')
        old=selected_version(db,doc,True) if doc else None
        asset_sha=new_file['sha256'] if new_file else old['asset_sha'] if old else None
        filename=new_file['name'] if new_file else old['filename'] if old else None
        if kind=='checklist':asset_sha=None;filename=None
        for duplicate in db.execute('SELECT d.id,v.normalized_title,v.asset_sha FROM dc_documents d JOIN dc_versions v ON v.document_id=d.id AND v.version=d.latest_version WHERE v.section_id=? AND d.kind=? AND d.id<>?',(section,kind,doc_id or '')):
            if duplicate['normalized_title']==normalize(title) or (asset_sha and duplicate['asset_sha']==asset_sha):raise DomainError('Ya existe este título o archivo en el destino. Abre el documento existente para reemplazarlo.',409)
        if new_file:db.execute('INSERT OR IGNORE INTO dc_assets VALUES(?,?,?,?,?)',(asset_sha,new_file['mime'],new_file['bytes'],'database',new_file['content']))
        document_id=doc_id or uuid.uuid4().hex;version=doc['latest_version']+1 if doc else 1;timestamp=now()
        if doc:
            db.execute('UPDATE dc_documents SET latest_version=?,revision=revision+1,updated_at=? WHERE id=?',(version,timestamp,document_id))
        else:
            db.execute('INSERT INTO dc_documents VALUES(?,?,?,?,?,?,?,?,?)',(document_id,kind,target_state,version,version if target_state=='active' else None,1,actor,timestamp,timestamp))
        db.execute('INSERT INTO dc_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(document_id,version,section,title,normalize(title),description,filename,asset_sha,json.dumps(items,ensure_ascii=False) if items is not None else None,actor,timestamp))
        event(db,document_id,'version_added' if doc else 'created',actor,{'version':version,'section_id':section,'state':doc['state'] if doc else target_state,'pending_publication':bool(doc)})
        return {'id':document_id,'version':version,'revision':doc['revision']+1 if doc else 1}
    return transaction(store,actor,'version:'+str(doc_id),payload,write)


def set_state(store,doc_id,payload,actor):
    state=payload.get('state')
    if state not in STATES:raise DomainError('Estado no válido.')
    def write(db):
        doc=expect_document(db,doc_id,payload)
        live=doc['latest_version'] if state=='active' else doc['live_version']
        db.execute('UPDATE dc_documents SET state=?,live_version=?,revision=revision+1,updated_at=? WHERE id=?',(state,live,now(),doc_id))
        event(db,doc_id,'state_changed',actor,{'from':doc['state'],'to':state,'published_version':live})
        return {'id':doc_id,'revision':doc['revision']+1}
    return transaction(store,actor,'state:'+doc_id,payload,write)


def complete_checklist(store,doc_id,payload,actor):
    responsible=text(payload.get('responsible'),'Responsable',120)
    performed=text(payload.get('performed_on'),'Fecha',10)
    try:assert date.fromisoformat(performed).isoformat()==performed
    except (ValueError,AssertionError):raise DomainError('Fecha no válida.') from None
    notes=text(payload.get('notes',''),'Observaciones',2000,False,True)
    def write(db):
        doc=expect_document(db,doc_id,payload)
        if doc['kind']!='checklist' or doc['state']!='active':raise DomainError('Solo se registran checklists vigentes.',409)
        value=selected_version(db,doc,False)
        if payload.get('version')!=value['version']:raise DomainError('La versión vigente cambió. Recarga el checklist.',409)
        checked=payload.get('checked')
        if not isinstance(checked,list) or len(checked)!=len(value['items']) or any(v is not True for v in checked):raise DomainError('Marca todos los ítems antes de guardar el registro.')
        run_id=uuid.uuid4().hex;items=[{'text':item,'done':True} for item in value['items']]
        db.execute('INSERT INTO dc_runs VALUES(?,?,?,?,?,?,?,?,?)',(run_id,doc_id,value['version'],responsible,performed,json.dumps(items,ensure_ascii=False),notes,actor,now()))
        event(db,doc_id,'checklist_completed',actor,{'run_id':run_id,'version':value['version'],'responsible':responsible,'performed_on':performed})
        return {'id':run_id,'document_id':doc_id}
    return transaction(store,actor,'run:'+doc_id,payload,write)


def file_content(store,root,doc_id,manager,requested=None):
    with store.connect() as db:
        initialized(db);doc=row_document(db,doc_id);value=selected_version(db,doc,manager,requested)
        if not value['asset_sha']:raise DomainError('Este checklist no tiene archivo adjunto.',404)
        asset=db.execute('SELECT * FROM dc_assets WHERE sha256=?',(value['asset_sha'],)).fetchone()
        if asset['storage'] in ('legacy-salon','legacy-labels'):
            base=Path(root).resolve();path=base/(LEGACY_PATH if asset['storage']=='legacy-salon' else LABELS_PATH)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base):raise DomainError('Archivo no disponible.',404)
            content=path.read_bytes()
        else:content=asset['content']
        if len(content)!=asset['bytes'] or hashlib.sha256(content).hexdigest()!=asset['sha256']:raise DomainError('El archivo requiere revisión de integridad.',409)
        return content,asset['mime'],value['filename']


def route(handler,store,partners,root,parsed,user):
    prefix='/api/document-center';path=parsed.path
    from urllib.parse import parse_qs
    query=parse_qs(parsed.query)
    manager=user['role']=='partner'
    if handler.command=='GET':
        if path==prefix:return handler.send(200,listing(store,manager,query))
        match=re.fullmatch(prefix+r'/documents/([A-Za-z0-9_-]+)(/file)?',path)
        if match:
            version=query.get('version',[None])[0]
            if match[2]:
                content,mime,filename=file_content(store,root,match[1],manager,version)
                disposition='attachment' if mime==document_xlsx.MIME or query.get('download')==['1'] else 'inline'
                extension={'application/pdf':'.pdf','image/png':'.png','image/jpeg':'.jpg',document_xlsx.MIME:'.xlsx'}[mime]
                return handler.send(200,content,mime,{'Content-Disposition':f'{disposition}; filename="documento{extension}"; filename*=UTF-8\'\'{quote(filename,safe="")}', 'Content-Security-Policy':"default-src 'none'; frame-ancestors 'self'"})
            return handler.send(200,detail(store,match[1],manager,version))
    if handler.command=='POST':
        account=partners.require(handler.headers.get('Cookie'))
        # Only the two file-receiving routes get the larger JSON budget.
        upload_route=path==prefix+'/documents' or re.fullmatch(prefix+r'/documents/[A-Za-z0-9_-]+/versions',path)
        payload=handler.body(MAX_UPLOAD_JSON if upload_route else 64*1024)
        actor=account['username']
        if path==prefix+'/sections':return handler.send(201,create_section(store,payload,actor))
        if path==prefix+'/documents':return handler.send(201,save_version(store,payload,actor))
        match=re.fullmatch(prefix+r'/documents/([A-Za-z0-9_-]+)/(versions|state|runs)',path)
        if match:
            result={'versions':lambda:save_version(store,payload,actor,match[1]),'state':lambda:set_state(store,match[1],payload,actor),'runs':lambda:complete_checklist(store,match[1],payload,actor)}[match[2]]()
            return handler.send(200,result)
    raise DomainError('Ruta de documentos no encontrada.',404)
