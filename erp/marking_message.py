"""Mensaje actualizado para copiar. Lectura local, sin envíos ni cambios de estado."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import re
import uuid
from erp.domain import DomainError, flatten, stock_summary, whatsapp, whatsapp_item_line

TZ=ZoneInfo('America/Santiago')


def date_window(current):
    """Hoy hasta el próximo viernes inclusive; los viernes, solo hoy."""
    return current.isoformat(), (current+timedelta(days=(4-current.weekday())%7)).isoformat()


def marked_detail(orders):
    lines=[]
    for order,item in flatten(orders):
        if item['status']=='marcado':
            lines.extend([whatsapp_item_line(order,item)] * item['quantity'])
    return '\nTORTAS MARCADAS\n\n'+'\n\n'.join(lines or ['Sin nuevas tortas marcadas desde tu última copia en este rango.'])


def view(store,current,scope='operations',username=''):
    if scope not in {'operations','tests','archived'}:raise DomainError('Vista de registros no válida.')
    start,end=date_window(current)
    with store.connect() as db:
        last=db.execute('SELECT through_event FROM marking_message_copies WHERE username=? AND scope=? ORDER BY id DESC LIMIT 1',(username,scope)).fetchone()
        cursor=last[0] if last else 0
        through=db.execute('SELECT COALESCE(MAX(id),0) FROM history').fetchone()[0]
    orders=store.list(start,end,include_archived=scope=='archived')
    if store.operating_mode()=='toteat-local' or scope!='operations':
        orders=[o for o in orders if (not o['is_simulation'] if scope=='operations' else o['is_simulation'] and o['simulation_archived']==(scope=='archived'))]
    stock=stock_summary(store.stock() if scope=='operations' else [])
    histories={o['id']:store.history(o['id']) for o in orders if any(i['kind']=='torta' and i['status']=='marcado' for i in o['items'])}
    recent=[]
    for order in orders:
        items=[]
        for item in order['items']:
            if item['status']!='marcado':continue
            event=next((h for h in histories.get(order['id'],[]) if h['item_id']==item['id'] and h['action']=='estado' and (h.get('after') or {}).get('status')=='marcado'),None)
            if (event and cursor<event['id']<=through) or (event is None and last is None):items.append(item)
        if items:recent.append(dict(order,items=items))
    text=whatsapp(orders,start,end,stock,include_weekends=True)+'\n'+marked_detail(recent)
    if scope!='operations':text='PRUEBAS · '+('ARCHIVADAS' if scope=='archived' else 'SIMULACIONES')+'\n\n'+text
    return {'text':text,'start':start,'end':end,'scope':scope,'timezone':str(TZ),
            'marked_quantity':sum(i['quantity'] for _,i in flatten(recent)),
            'generated_at':datetime.now(TZ).isoformat(),'marked_basis':'since_last_successful_copy_by_user_and_scope',
            'copy':{'scope':scope,'cursor':cursor,'through':through,'key':str(uuid.uuid4()),'sha256':hashlib.sha256(text.encode()).hexdigest()}}


def acknowledge(store,username,data):
    """Solo confirma la copia del navegador. No cambia pedidos, pagos o stock."""
    scope=data.get('scope');cursor=data.get('cursor');through=data.get('through')
    if not isinstance(scope,str) or scope not in {'operations','tests','archived'} or type(cursor) is not int or type(through) is not int or not 0<=cursor<=through:
        raise DomainError('Confirmación de copia no válida.')
    if not re.fullmatch(r'[a-f0-9-]{36}',str(data.get('key',''))) or not re.fullmatch(r'[a-f0-9]{64}',str(data.get('sha256',''))):
        raise DomainError('Referencia de copia no válida.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM marking_message_copies WHERE username=? AND scope=? AND copy_key=?',(username,scope,data['key'])).fetchone():return {'recorded':True,'duplicate':True}
        last=db.execute('SELECT through_event FROM marking_message_copies WHERE username=? AND scope=? ORDER BY id DESC LIMIT 1',(username,scope)).fetchone()
        actual=last[0] if last else 0
        if actual!=cursor:
            if actual>=through:return {'recorded':True,'superseded':True}
            raise DomainError('Ya se copió otra versión desde tu cuenta. Actualiza el mensaje.',409)
        if through>db.execute('SELECT COALESCE(MAX(id),0) FROM history').fetchone()[0]:raise DomainError('Referencia de historial no válida.')
        db.execute('INSERT INTO marking_message_copies(username,scope,previous_event,through_event,message_sha256,copy_key,copied_at) VALUES(?,?,?,?,?,?,?)',
                   (username,scope,cursor,through,data['sha256'],data['key'],datetime.now(TZ).isoformat()))
    return {'recorded':True}
