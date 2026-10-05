"""Consulta, filtros y exportación del bot. El seguimiento usa un puente privado."""
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from erp.domain import DomainError
from erp.transfer_followup import histories


def read_transfers(path,params,socket_path=None,all_rows=False):
    if not path or not Path(path).is_file():return {'configured':False,'rows':[],'message':'La lectura del bot todavía no está configurada.'}
    path=Path(path)
    if path.stat().st_size>32*1024*1024:raise DomainError('La copia de lectura requiere revisión.',503)
    try:
        snapshot=json.loads(path.read_text())
        if snapshot['version']!=1:raise ValueError()
        updated=datetime.fromisoformat(snapshot['updated_at'])
    except (OSError,ValueError,KeyError):raise DomainError('No se pudo leer la información del bot.',503) from None
    today=datetime.now(ZoneInfo('America/Santiago')).date()
    try:
        start=datetime.strptime(params.get('start',str(today-timedelta(days=29))),'%Y-%m-%d').date()
        end=datetime.strptime(params.get('end',str(today)),'%Y-%m-%d').date()
        if end<start or (end-start).days>366:raise ValueError()
        page=int(params.get('page',1))
        if page<1:raise ValueError()
    except (ValueError,TypeError):raise DomainError('Selecciona fechas válidas y un rango de hasta 366 días.') from None
    status=params.get('status','all')
    if status not in ('all','pending','compatible','reviewed','review'):raise DomainError('Filtro no válido.')
    cashier=params.get('cashier','')
    cashiers={r.get('cashier_id','unknown'):r['cajera'] for r in snapshot['rows']}
    if cashier and cashier not in cashiers:raise DomainError('Selecciona una cajera disponible.')
    selected=[]
    for row in snapshot['rows']:
        day=datetime.fromisoformat(row['fecha']).astimezone(ZoneInfo('America/Santiago')).date()
        if start<=day<=end and (not cashier or cashier==row.get('cashier_id','unknown')):selected.append(dict(row))
    live={};available=bool(socket_path)
    if socket_path:
        try:live=histories(socket_path,selected)
        except DomainError:available=False
    rows=[]
    for row in selected:
        current=live.get(row['id'])
        if current and current['source_ref']==row.get('source_ref'):
            row.update(current)
        elif socket_path:available=False
        pending=not row['compatible'] and row['seguimiento']!='revisado'
        if status=='pending' and not pending or status=='compatible' and not row['compatible'] or status=='reviewed' and (row['compatible'] or row['seguimiento']!='revisado') or status=='review' and row['compatible']:continue
        rows.append(dict(row,pending=pending))
    unique={r['orden_id']:r['monto_esperado'] or 0 for r in rows if r['orden_id']}
    reasons=Counter(k for r in rows for k,v in r['estados'].items() if v not in ('COINCIDE','PARCIAL'))
    days=Counter(datetime.fromisoformat(r['fecha']).astimezone(ZoneInfo('America/Santiago')).date().isoformat() for r in rows)
    health=[]
    for item in snapshot.get('health',[]):
        delayed=(datetime.now(timezone.utc)-datetime.fromisoformat(item['fecha'])).total_seconds()>(120 if item['componente']=='Procesador' else 7200)
        health.append(dict(item,delayed=delayed))
    return {'configured':True,'updated_at':snapshot['updated_at'],'stale':(datetime.now(timezone.utc)-updated).total_seconds()>180,
            'start':str(start),'end':str(end),'page':page,'page_size':50,'total':len(rows),'rows':rows if all_rows else rows[(page-1)*50:page*50],
            'cashiers':[{'id':key,'name':value} for key,value in sorted(cashiers.items(),key=lambda pair:pair[1])],
            'breakdown':{'reasons':reasons.most_common(),'cashiers':Counter(r['cajera'] for r in rows).most_common(),'days':sorted(days.items())},
            'summary':{'compatible':sum(bool(r['compatible']) for r in rows),'pending':sum(r['pending'] for r in rows),'sales':len(unique),'expected_amount':sum(unique.values())},
            'health':health,'alerts':snapshot.get('alerts',[]),'closures':snapshot.get('closures',[]),'uncertain_messages':snapshot.get('uncertain_messages',0),'source':'Ovejita','read_only':not available,'followup_available':available}


def export_csv(path, params, store, actor, request_id, socket_path=None):
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9-]{36}',request_id):raise DomainError('Identificador de exportación no válido.')
    if not isinstance(params,dict):raise DomainError('Filtros no válidos.')
    filters={key:params[key] for key in ('start','end','status','cashier') if key in params}
    data=read_transfers(path,filters,socket_path,all_rows=True)
    if not data['configured']:raise DomainError('La lectura del bot todavía no está configurada.',503)
    # Conservar un archivo antiguo no debe presentarlo como seguimiento actual.
    if socket_path and not data['followup_available']:raise DomainError('Espera a que se recupere el seguimiento antes de exportar.',503)
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['Revision','Fecha Chile','Venta','Cajera','Monto esperado CLP','Resultado','Seguimiento'])
    def safe(value):
        text='' if value is None else str(value)
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text
    for row in data['rows']:
        when=datetime.fromisoformat(row['fecha']).astimezone(ZoneInfo('America/Santiago')).strftime('%d/%m/%Y %H:%M')
        writer.writerow([safe(v) for v in (row['id'],when,row['orden_id'],row['cajera'],row['monto_esperado'],
                         'Datos visibles compatibles' if row['compatible'] else 'Requiere revisión',
                         'No aplica' if row['compatible'] else row['seguimiento'])])
    content=('\ufeff'+output.getvalue()).encode('utf-8')
    filters.update(start=data['start'],end=data['end'])
    encoded=json.dumps(filters,sort_keys=True);digest=hashlib.sha256(content).hexdigest()
    with store.connect() as db:
        previous=db.execute('SELECT actor,filters_json,content_sha256 FROM transfer_exports WHERE request_id=?',(request_id,)).fetchone()
        if previous and tuple(previous)!=(actor,encoded,digest):raise DomainError('La exportación cambió. Inicia una nueva descarga.',409)
        db.execute('INSERT OR IGNORE INTO transfer_exports VALUES(?,?,?,?,?,?)',(request_id,datetime.now(timezone.utc).isoformat(),actor,encoded,data['total'],digest))
    return content


def read_photo(snapshot_path,revision_id):
    import hashlib
    import re
    if not snapshot_path:raise DomainError('Comprobante no disponible.',404)
    path=Path(snapshot_path)
    snapshot=json.loads(path.read_text())
    row=next((r for r in snapshot['rows'] if r['id']==revision_id),None)
    photo=row.get('photo') if row else None
    if not photo or not re.fullmatch(r'[a-f0-9]{64}\.(jpg|png|webp)',photo['filename']):raise DomainError('Comprobante no archivado para esta revisión.',404)
    file=path.parent/'photos'/photo['filename']
    if file.is_symlink() or not file.is_file() or file.stat().st_size>8*1024*1024:raise DomainError('Comprobante no disponible.',404)
    content=file.read_bytes()
    if hashlib.sha256(content).hexdigest()!=photo['sha256']:raise DomainError('El comprobante requiere revisión de integridad.',409)
    return content,photo['type'],photo['filename'].rsplit('.',1)[1]
