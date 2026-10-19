"""Editable local purchasing intentions; recurring availability never creates orders."""
import json
from erp import payables as ap, procurement as pc
from erp.domain import DomainError
from erp.store import now


def view(db, key):
    row=ap.record(db,'pc_purchase_agenda',key)
    row['lines']=json.loads(row.pop('lines_json'))
    row['history']=[dict(r) for r in db.execute("SELECT * FROM pc_events WHERE entity='agenda' AND entity_id=? ORDER BY id DESC",(key,))]
    return row


def overview(db, suppliers):
    return {'agenda':[view(db,r[0]) for r in db.execute('SELECT id FROM pc_purchase_agenda ORDER BY scheduled_on,id')],
            'agenda_availability':[{'supplier_id':s['id'],'days':s['profile']['order_days'],'cutoff_time':s['profile']['cutoff_time'],
                                    'channel':s['profile']['order_channel'],'contact':s['profile']['order_contact'],
                                    'delivery_days':s['profile']['delivery_days'],'note':s['profile']['schedule_note']} for s in suppliers]}


def clean_lines(db, rows):
    if not isinstance(rows,list) or len(rows)>100:raise DomainError('Agenda: hasta 100 productos por intención de compra.')
    clean=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise DomainError('Producto de agenda inválido.')
        description=ap.text(row.get('description'),'Producto',True,300)
        qty=pc.quantity(row.get('quantity'),'Cantidad planificada')
        item_id=row.get('item_id');unit_id=row.get('unit_id')
        if item_id is not None:
            item=ap.record(db,'pc_items',ap.integer(item_id,'Insumo'));unit=ap.record(db,'pc_units',ap.integer(unit_id,'Unidad'))
            if item_id in seen:raise DomainError('Agrupa el insumo en una sola línea.')
            seen.add(item_id);pc.in_unit(db,qty,unit_id)
            if unit_id!=item['base_unit_id'] and not db.execute('SELECT 1 FROM pc_equivalences WHERE item_id=? AND unit_id=?',(item_id,unit_id)).fetchone():raise DomainError('Configura la equivalencia explícita antes de planificar ese insumo.')
        elif unit_id is not None:raise DomainError('Asocia la unidad a un insumo del catálogo.')
        clean.append(dict(description=description,quantity=pc.decimal(qty),item_id=item_id,unit_id=unit_id))
    return clean


def mutate(db, action, data, actor, target):
    reason=ap.text(data.get('reason'),'Motivo del cambio',True,1000)
    old=view(db,target) if target else None
    if old:
        ap.version(data,old)
        if old['status']=='done':raise DomainError('La intención ya se marcó realizada; su historial se conserva.',409)
    if action=='agenda_save':
        supplier=ap.record(db,'ap_suppliers',ap.integer(data.get('supplier_id'),'Proveedor'))
        if old and (old['order_id'] or old['supplier_id']!=supplier['id']):raise DomainError('Una intención vinculada a pedido conserva sus datos. Crea otra intención para un cambio de proveedor.',409)
        scheduled=ap.day(data.get('scheduled_on'),'Día de pedido')
        expected=ap.day(data.get('expected_on',''),'Entrega esperada',False)
        if expected and expected<scheduled:raise DomainError('La entrega esperada no puede ser anterior al pedido planificado.')
        lines=clean_lines(db,data.get('lines',[]));notes=ap.text(data.get('notes',''),'Notas',maximum=2000)
        status='postponed' if old and scheduled>old['scheduled_on'] else 'pending'
        if old:
            db.execute('UPDATE pc_purchase_agenda SET scheduled_on=?,expected_on=?,notes=?,lines_json=?,status=?,updated_at=?,version=version+1 WHERE id=?',(scheduled,expected,notes,json.dumps(lines,ensure_ascii=False),status,now(),target))
        else:
            target=db.execute('INSERT INTO pc_purchase_agenda(supplier_id,origin_on,scheduled_on,expected_on,notes,lines_json,status,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(supplier['id'],scheduled,scheduled,expected,notes,json.dumps(lines,ensure_ascii=False),status,actor,now(),now())).lastrowid
    elif action=='agenda_order':
        if old['order_id']:raise DomainError('Esta intención ya tiene un pedido vinculado.',409)
        if not old['lines'] or any(r['item_id'] is None for r in old['lines']):raise DomainError('Relaciona todos los productos con insumos y unidades antes de preparar el pedido.')
        supplier=ap.record(db,'ap_suppliers',old['supplier_id']);ap.version({'version':data.get('supplier_version')},supplier)
        rows=[]
        versions=data.get('item_versions',{})
        if not isinstance(versions,dict):raise DomainError('Actualiza las versiones del catálogo.')
        for row in old['lines']:
            item=ap.record(db,'pc_items',row['item_id']);ap.version({'version':versions.get(str(item['id']))},item)
            rows.append(dict(item_id=item['id'],version=item['version'],unit_id=row['unit_id'],quantity=row['quantity']))
        order=pc.mutate(db,'order',dict(supplier_id=supplier['id'],supplier_version=supplier['version'],warehouse_id=data.get('warehouse_id'),ordered_on=ap.local_day(),expected_on=old['expected_on'],reference='',notes='Agenda '+str(target)+'. '+old['notes'],lines=rows,confirmed=data.get('confirmed')),actor,None)
        db.execute('UPDATE pc_purchase_agenda SET order_id=?,updated_at=?,version=version+1 WHERE id=?',(order['id'],now(),target))
    elif action=='agenda_done':
        reference=ap.text(data.get('external_reference',''),'Referencia de pedido realizado',maximum=300)
        completed=ap.day(data.get('completed_on'),'Fecha de realización')
        if completed>ap.local_day():raise DomainError('La realización no puede estar en el futuro.')
        if data.get('confirmed') is not True:raise DomainError('Confirma que realizaste el pedido por el canal del proveedor.')
        if not reference:raise DomainError('Anota una referencia verificable del pedido realizado; crear una OC local no confirma su envío.')
        if old['order_id'] and pc.order_view(db,old['order_id'])['state']=='cancelled':raise DomainError('El pedido vinculado está cancelado; revisa esta intención.',409)
        db.execute("UPDATE pc_purchase_agenda SET status='done',external_reference=?,completed_on=?,updated_at=?,version=version+1 WHERE id=?",(reference,completed,now(),target))
    elif action=='agenda_postpone':
        scheduled=ap.day(data.get('scheduled_on'),'Nueva fecha')
        expected=ap.day(data.get('expected_on',''),'Entrega esperada',False)
        if scheduled<=old['scheduled_on'] or (expected and expected<scheduled):raise DomainError('Elige una fecha posterior y revisa la entrega esperada.')
        db.execute("UPDATE pc_purchase_agenda SET scheduled_on=?,expected_on=?,status='postponed',updated_at=?,version=version+1 WHERE id=?",(scheduled,expected,now(),target))
    else:raise DomainError('Operación de agenda no encontrada.',404)
    result=view(db,target)
    pc.audit(db,'agenda',target,action,actor,reason,{k:v for k,v in (old or {}).items() if k!='history'},{k:v for k,v in result.items() if k!='history'})
    return view(db,target)
