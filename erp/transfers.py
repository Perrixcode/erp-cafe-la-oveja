"""Vista de solo lectura de una proyección privada del bot, sin acceso a su base."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from erp.domain import DomainError


def read_transfers(path,params):
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
    if status not in ('all','pending','compatible','reviewed'):raise DomainError('Filtro no válido.')
    rows=[]
    for row in snapshot['rows']:
        day=datetime.fromisoformat(row['fecha']).astimezone(ZoneInfo('America/Santiago')).date()
        if not start<=day<=end:continue
        pending=not row['compatible'] and row['seguimiento']!='revisado'
        if status=='pending' and not pending or status=='compatible' and not row['compatible'] or status=='reviewed' and row['seguimiento']!='revisado':continue
        rows.append(dict(row,pending=pending))
    unique={r['orden_id']:r['monto_esperado'] or 0 for r in rows if r['orden_id']}
    return {'configured':True,'updated_at':snapshot['updated_at'],'stale':(datetime.now(timezone.utc)-updated).total_seconds()>180,
            'start':str(start),'end':str(end),'page':page,'page_size':50,'total':len(rows),'rows':rows[(page-1)*50:page*50],
            'summary':{'compatible':sum(bool(r['compatible']) for r in rows),'pending':sum(r['pending'] for r in rows),'sales':len(unique),'expected_amount':sum(unique.values())},
            'health':snapshot.get('health',[]),'alerts':snapshot.get('alerts',[]),'closures':snapshot.get('closures',[]),'uncertain_messages':snapshot.get('uncertain_messages',0),'source':'Ovejita','read_only':True}


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
