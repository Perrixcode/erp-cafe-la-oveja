"""Local-only PDF/image intake. OCR is evidence for explicit human review, never booking."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote
from erp import payables as ap
from erp.domain import DomainError
from erp.store import now


def advanced_available():
    return Path('/opt/oveja-erp/ocr-runtime/rapidocr-1.4.4/rapidocr_onnxruntime').is_dir()


_reader_slot=threading.BoundedSemaphore(1)


def extract(content,mime):
    # Do not launch multiple decoders while the single web process serves requests.
    if not _reader_slot.acquire(blocking=False):
        return '', 'busy','El lector local está ocupado. Conservamos el original; usa Volver a leer original en unos momentos.'
    try:return _extract(content,mime)
    finally:_reader_slot.release()


def _extract(content,mime):
    """No network, shell, credentials or third-party service. Timeout and page cap."""
    with tempfile.TemporaryDirectory(prefix='oveja-document-') as folder:
        path=Path(folder)/('source.pdf' if mime=='application/pdf' else 'source.png' if mime=='image/png' else 'source.jpg');path.write_bytes(content)
        if advanced_available():
            command=[sys.executable,str(Path(__file__).with_name('local_invoice_reader.py')),str(path),mime]
        elif sys.platform=='darwin' and shutil.which('swift'):
            command=[shutil.which('swift'),'-module-cache-path',str(Path(folder)/'modules'),str(Path(__file__).with_name('local_document_ocr.swift')),str(path)]
        elif mime=='application/pdf' and shutil.which('pdftotext'):
            command=[shutil.which('pdftotext'),'-layout','-f','1','-l','20',str(path),'-']
        elif mime!='application/pdf' and shutil.which('tesseract'):
            command=[shutil.which('tesseract'),str(path),'stdout']
        else:return '', 'unavailable','No hay lector local disponible. Revisa el original e ingresa los campos manualmente.'
        try:
            # A private output file avoids unbounded captured stdout in memory.
            with (Path(folder)/'output.txt').open('w+b') as output:
                result=subprocess.run(command,stdout=output,stderr=subprocess.DEVNULL,timeout=90,check=False)
                if result.returncode==3:return '', 'busy','El lector local está ocupado. Conservamos el original; usa Volver a leer original en unos momentos.'
                if result.returncode:return '', 'failed','La lectura local no pudo completarse dentro de sus límites. El original se conserva; puedes volver a leerlo o completar su revisión manual.'
                output.seek(0);raw=output.read(200001)
            if len(raw)>200000:return '', 'failed','El texto supera el límite; revisa el original manualmente.'
            text=raw.decode('utf-8',errors='replace')
            from erp.invoice_ocr import reading
            has_text=bool(reading(text)['text'].strip())
            return text,'extracted' if has_text else 'empty',('Revisa todos los datos contra el original. Hasta 20 páginas con texto o 5 escaneadas, sujeto al límite de 90 segundos. Las descripciones y cifras pueden tener errores.' if has_text else 'No se detectó texto legible. Conservamos el original; revisa su orientación y nitidez antes de volver a leerlo.')
        except (OSError,subprocess.TimeoutExpired):return '', 'failed','La lectura local no terminó. El original se conserva para revisión manual.'


def suggestions(text):
    from erp.invoice_ocr import suggest
    return suggest(text)


def draft_view(db,key,full=True):
    row=ap.record(db,'ap_document_drafts',key);row.pop('content')
    row['suggested']=suggestions(row['extracted_text']);row['state']='registered' if row['invoice_id'] else 'discarded' if row['discarded_at'] else 'pending'
    from erp.invoice_ocr import reading
    row['reading']=reading(row['extracted_text'])
    review=db.execute('SELECT payload_json FROM ap_document_reviews WHERE draft_id=?',(key,)).fetchone()
    row['review']=json.loads(review[0]) if review else None
    from erp import document_lines
    row['lines']=document_lines.list_lines(db,key)
    row['same_files']=[dict(r) for r in db.execute('SELECT DISTINCT invoice_id FROM ap_files WHERE sha256=? AND payment_id IS NULL',(row['sha256'],))]
    if not full:row.pop('extracted_text');row.pop('reading')
    return row


def upload(store,content,name,actor):
    if not 0<len(content)<=ap.LIMIT:raise DomainError('Adjunta hasta 8 MB.',413)
    mime=ap.file_type(content);name=ap.text(name,'Nombre del archivo',True,160);digest=hashlib.sha256(content).hexdigest()
    with store.connect() as db:
        row=db.execute('SELECT id FROM ap_document_drafts WHERE sha256=?',(digest,)).fetchone()
        if row:return draft_view(db,row[0])
    text,status,note=extract(content,mime)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT id FROM ap_document_drafts WHERE sha256=?',(digest,)).fetchone()
        if row:return draft_view(db,row[0])
        key=db.execute('INSERT INTO ap_document_drafts(sha256,name,mime,bytes,content,extracted_text,extraction_status,extraction_note,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(digest,name,mime,len(content),content,text,status,note,actor,now())).lastrowid
        ap.event(db,'document_uploaded',actor,after={'draft_id':key,'sha256':digest,'status':status})
        return draft_view(db,key)


def mutate(db,action,data,actor,target):
    if action=='document_map':
        from erp import document_lines
        return document_lines.map_lines(db,target,data,actor)
    row=ap.record(db,'ap_document_drafts',target);ap.version(data,row)
    if row['invoice_id'] or row['discarded_at']:raise DomainError('Este documento ya se revisó o descartó.',409)
    review=data.get('review')
    if isinstance(review,dict) and review.get('draft_id',target)!=target:
        raise DomainError('La revisión pertenece a otro documento. Vuelve a abrir el original.',409)
    reason=ap.text(data.get('reason'),'Antecedente de revisión',True,1000)
    if action=='document_save':
        payload=data.get('review')
        if not isinstance(payload,dict) or len(json.dumps(payload))>100000:raise DomainError('Revisión demasiado extensa o inválida.')
        db.execute('INSERT INTO ap_document_reviews VALUES(?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET payload_json=excluded.payload_json,actor=excluded.actor,updated_at=excluded.updated_at',(target,json.dumps(payload,ensure_ascii=False),actor,now()))
        db.execute('UPDATE ap_document_drafts SET version=version+1 WHERE id=?',(target,))
        ap.event(db,'document_review_saved',actor,reason,after={'draft_id':target})
        return {'id':target}
    if action=='document_apply_reading':
        if data.get('confirmed') is not True:raise DomainError('Confirma la comparación antes de aplicar la lectura.')
        candidate=db.execute('SELECT response_json FROM ap_requests WHERE request_id=?',(data.get('candidate_id'),)).fetchone()
        candidate=json.loads(candidate[0]) if candidate else {}
        if candidate.get('draft_id')!=target or candidate.get('actor')!=actor or candidate.get('base_version')!=row['version'] or candidate.get('sha256')!=row['sha256']:
            raise DomainError('La comparación cambió o pertenece a otra revisión. Vuelve a leer el original.',409)
        if candidate.get('extraction_status')!='extracted':raise DomainError('La nueva lectura no produjo texto utilizable.')
        mode=data.get('mode')
        if mode not in ('preserve','replace'):raise DomainError('Elige cómo conservar tu revisión.')
        if mode=='replace' and data.get('replace_confirmed') is not True:raise DomainError('Confirma explícitamente el reemplazo de la revisión.')
        prior=db.execute('SELECT payload_json FROM ap_document_reviews WHERE draft_id=?',(target,)).fetchone()
        before={'draft_id':target,'extracted_text':row['extracted_text'],'review':json.loads(prior[0]) if prior else None}
        payload=data.get('review')
        if payload is not None and (not isinstance(payload,dict) or len(json.dumps(payload))>100000):raise DomainError('Revisión inválida o demasiado extensa.')
        before['unsaved_review']=payload
        if mode=='replace':db.execute('DELETE FROM ap_document_reviews WHERE draft_id=?',(target,))
        elif payload is not None:
            db.execute('INSERT INTO ap_document_reviews VALUES(?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET payload_json=excluded.payload_json,actor=excluded.actor,updated_at=excluded.updated_at',(target,json.dumps(payload,ensure_ascii=False),actor,now()))
        text,status,note=candidate['extracted_text'],candidate['extraction_status'],candidate['extraction_note']
        db.execute('UPDATE ap_document_drafts SET extracted_text=?,extraction_status=?,extraction_note=?,version=version+1 WHERE id=?',(text,status,note,target))
        ap.event(db,'document_reading_applied',actor,reason,before=before,after={'draft_id':target,'status':status,'mode':mode,'candidate_id':data['candidate_id']})
        return {'id':target}
    if action=='document_discard':
        db.execute('UPDATE ap_document_drafts SET discarded_at=?,discard_reason=?,reviewed_by=?,version=version+1 WHERE id=?',(now(),reason,actor,target))
        ap.event(db,'document_discarded',actor,reason,after={'draft_id':target});return {'id':target}
    if data.get('confirmed') is not True:raise DomainError('Confirma que cotejaste proveedor, tipo, folio, fechas y total con el original.')
    if data.get('existing_invoice_id') is not None:
        invoice=ap.record(db,'ap_invoices',ap.integer(data['existing_invoice_id'],'Factura existente'));ap.version({'version':data.get('invoice_version')},invoice)
        if invoice['voided_at']:raise DomainError('Selecciona una factura vigente.')
    else:
        fields=data.get('invoice')
        if not isinstance(fields,dict):raise DomainError('Completa los campos revisados de la factura.')
        ap.day(fields.get('issued_on'),'Emisión revisada')
        invoice=ap.mutate(db,'invoice',fields,actor,None)
    if suggestions(row['extracted_text']).get('lines') and not data.get('review',{}).get('lines') and data.get('skip_lines_confirmed') is not True:raise DomainError('Confirma si guardarás solo la cabecera sin los productos detectados.')
    from erp import document_lines
    document_lines.register(db,row,invoice,data.get('review',{}),actor,reason)
    others=db.execute('SELECT invoice_id FROM ap_files WHERE sha256=? AND payment_id IS NULL AND invoice_id<>?',(row['sha256'],invoice['id'])).fetchone()
    if others:raise DomainError('El mismo archivo ya está vinculado a otra factura. Revisa ese registro antes de duplicarlo.',409)
    attached=db.execute('SELECT id FROM ap_files WHERE sha256=? AND invoice_id=? AND payment_id IS NULL',(row['sha256'],invoice['id'])).fetchone()
    if not attached:db.execute('INSERT INTO ap_files(invoice_id,name,mime,sha256,bytes,content,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',(invoice['id'],row['name'],row['mime'],row['sha256'],row['bytes'],row['content'],actor,now()))
    db.execute('UPDATE ap_document_drafts SET invoice_id=?,reviewed_by=?,reviewed_at=?,version=version+1 WHERE id=?',(invoice['id'],actor,now(),target))
    ap.event(db,'document_reviewed',actor,reason,after={'draft_id':target,'invoice_id':invoice['id'],'sha256':row['sha256'],'suggested':suggestions(row['extracted_text'])},supplier=invoice['supplier_id'],invoice=invoice['id'])
    return {'id':target,'invoice_id':invoice['id']}


def handle(handler,store,path,query,actor):
    tail=path[len('/api/suppliers/documents'):]
    if handler.command=='GET':
        with store.connect() as db:
            if tail in ('','/'):return handler.send(200,{'documents':[draft_view(db,r[0],False) for r in db.execute('SELECT id FROM ap_document_drafts ORDER BY id DESC')]})
            match=re.fullmatch(r'/(\d+)(/file)?',tail)
            if match:
                if not match[2]:return handler.send(200,draft_view(db,int(match[1])))
                row=ap.record(db,'ap_document_drafts',int(match[1]))
                if hashlib.sha256(row['content']).hexdigest()!=row['sha256']:raise DomainError('Falló la verificación del original.',409)
                return handler.send(200,row['content'],row['mime'],{'Content-Disposition':'inline; filename="documento-revisar"','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox"})
    if handler.command=='POST':
        if tail in ('','/'):
            try:length=int(handler.headers.get('Content-Length','0'))
            except ValueError:raise DomainError('Tamaño inválido.') from None
            if not 0<length<=ap.LIMIT:raise DomainError('Adjunta hasta 8 MB.',413)
            content=handler.rfile.read(length)
            if len(content)!=length:raise DomainError('Archivo incompleto.')
            return handler.send(201,upload(store,content,unquote(handler.headers.get('X-File-Name','documento')),actor))
        match=re.fullmatch(r'/(\d+)/(confirm|discard|save|reextract|map|apply-reading)',tail)
        if match:
            data=handler.body();key=int(match[1])
            if match[2]=='reextract':return handler.send(201,reextract(store,key,data,actor))
            return handler.send(201,ap.execute(store,'document_'+match[2].replace('-','_'),data,actor,key))
    raise DomainError('Ruta de documentos no encontrada.',404)


def reextract(store,key,data,actor):
    request=ap.text(data.get('request_id'),'Identificador de operación',True,100)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request):raise DomainError('Identificador de operación inválido.')
    signature=hashlib.sha256(json.dumps(['document_reextract',key,actor,data],sort_keys=True).encode()).hexdigest()
    with store.connect() as db:
        prior=db.execute('SELECT * FROM ap_requests WHERE request_id=?',(request,)).fetchone()
        if prior:
            if prior['signature']!=signature:raise DomainError('La solicitud ya se usó con otros datos.',409)
            return json.loads(prior['response_json'])
        row=ap.record(db,'ap_document_drafts',key);ap.version(data,row)
        if row['invoice_id'] or row['discarded_at']:raise DomainError('Solo se releen documentos pendientes.',409)
    extraction=extract(row['content'],row['mime'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT * FROM ap_requests WHERE request_id=?',(request,)).fetchone()
        if prior:
            if prior['signature']!=signature:raise DomainError('La solicitud ya se usó con otros datos.',409)
            return json.loads(prior['response_json'])
        latest=ap.record(db,'ap_document_drafts',key);ap.version(data,latest)
        if latest['invoice_id'] or latest['discarded_at']:raise DomainError('El documento ya no está pendiente.',409)
        from erp.invoice_ocr import reading
        text,status,note=extraction
        result=dict(id=key,draft_id=key,actor=actor,base_version=latest['version'],sha256=latest['sha256'],candidate_id=request,extracted_text=text,extraction_status=status,extraction_note=note,suggested=suggestions(text),reading=reading(text))
        db.execute('INSERT INTO ap_requests VALUES(?,?,?)',(request,signature,json.dumps(result)))
        return result
