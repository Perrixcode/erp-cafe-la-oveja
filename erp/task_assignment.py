"""Manual task lists. No inventory, integrations, scheduling or worker accounts."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from html import escape
import json
import re
import uuid

from erp.domain import DomainError
from erp.store import now

AREAS = {'kitchen': 'Cocina', 'floor': 'Salón'}
STATUSES = {'pending': 'Pendiente', 'progress': 'En curso', 'done': 'Completada', 'blocked': 'Bloqueada'}
PRIORITIES = {'normal': 'Normal', 'urgent': 'Urgente'}


def text(value, label, maximum=160, required=True):
    if not isinstance(value, str):
        raise DomainError(f'{label}: se espera texto.')
    value = value.strip()
    if len(value) > maximum or (required and not value) or any(ord(c) < 32 and c != '\n' for c in value):
        raise DomainError(f'{label}: completa el campo (máximo {maximum} caracteres).')
    return value


def area(value):
    if not isinstance(value, str) or value not in AREAS:
        raise DomainError('Selecciona Cocina o Salón.')
    return value


def day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (ValueError, TypeError):
        raise DomainError('Selecciona una fecha válida.') from None
    return value


def integer(value, label, minimum=1, maximum=1000000000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError(f'{label}: número no válido.')
    return value


def fields(data):
    raw = str(data.get('quantity', '')).replace(',', '.')
    if not re.fullmatch(r'\d{1,6}(?:\.\d{1,3})?', raw):
        raise DomainError('Cantidad: usa un número positivo con hasta tres decimales.')
    try:
        quantity = Decimal(raw)
        if not 0 < quantity <= 999999:
            raise InvalidOperation
    except InvalidOperation:
        raise DomainError('Cantidad fuera de rango.') from None
    priority = data.get('priority', 'normal')
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise DomainError('Prioridad no válida.')
    return dict(quantity=format(quantity, 'f'), unit=text(data.get('unit'), 'Unidad', 30),
                priority=priority, order=integer(data.get('order'), 'Orden', maximum=9999),
                instructions=text(data.get('instructions', ''), 'Instrucciones', 1000, False))


def get(db, table, identity):
    # Table names are internal constants, never supplied by the client.
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise DomainError('Registro no encontrado.', 404)
    result = dict(row)
    if table == 'task_lists':
        result['items'] = json.loads(result.pop('items_json'))
    return result


def check_version(record, data):
    if type(data.get('version')) is not int or record['version'] != data['version']:
        raise DomainError('La información cambió. Actualiza la vista antes de guardar.', 409)


def audit(db, entity, identity, before, after, actor):
    db.execute('INSERT INTO task_events(entity,entity_id,before_json,after_json,actor,created_at) VALUES(?,?,?,?,?,?)',
               (entity, identity, json.dumps(before) if before is not None else None, json.dumps(after), actor, now()))


def mutate(store, action, data, actor, operation):
    request = text(data.get('request_id'), 'Identificador de operación', 100)
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}', request):
        raise DomainError('Identificador de operación no válido.')
    fingerprint = hashlib.sha256(json.dumps([actor, action, data], sort_keys=True).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM task_requests WHERE id=?', (request,)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise DomainError('La operación ya se usó con otros datos.', 409)
            return json.loads(previous['result_json'])
        result = operation(db)
        db.execute('INSERT INTO task_requests VALUES(?,?,?)', (request, fingerprint, json.dumps(result)))
        return result


def save_entity(store, kind, data, actor, identity=None):
    table = {'catalog': 'task_catalog', 'workers': 'task_workers'}[kind]
    def operation(db):
        before = get(db, table, identity) if identity else None
        if before:
            check_version(before, data)
        selected_area = area(data.get('area'))
        if before and selected_area != before['area']:
            raise DomainError('No se puede cambiar el área de un registro existente.')
        values = {'area': selected_area}
        if kind == 'catalog':
            values.update(title=text(data.get('title'), 'Nombre de tarea'), category=text(data.get('category'), 'Categoría', 80),
                          description=text(data.get('description', ''), 'Descripción', 1000, False),
                          unit=text(data.get('unit', 'unidades'), 'Unidad habitual', 30))
        else:
            values['name'] = text(data.get('name'), 'Nombre del trabajador', 100)
        if before:
            db.execute(f'UPDATE {table} SET '+','.join(f'{key}=?' for key in values)+',version=version+1 WHERE id=?', (*values.values(), identity))
            result_id = identity
        else:
            result_id = db.execute(f'INSERT INTO {table} ('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')', tuple(values.values())).lastrowid
        result = get(db, table, result_id)
        audit(db, kind, result_id, before, result, actor)
        return result
    return mutate(store, f'{kind}:{identity}', data, actor, operation)


def view(store, selected_area, work_date):
    selected_area, work_date = area(selected_area), day(work_date)
    with store.connect() as db:
        lists = [get(db, 'task_lists', row[0]) for row in db.execute(
            'SELECT id FROM task_lists WHERE area=? AND work_date=? ORDER BY worker_name,id', (selected_area, work_date)).fetchall()]
        return dict(area=selected_area, date=work_date, statuses=STATUSES, priorities=PRIORITIES,
                    catalog=[dict(r) for r in db.execute('SELECT * FROM task_catalog WHERE area=? ORDER BY category,title,id', (selected_area,))],
                    workers=[dict(r) for r in db.execute('SELECT * FROM task_workers WHERE area=? ORDER BY name,id', (selected_area,))],
                    dates=[r[0] for r in db.execute('SELECT DISTINCT work_date FROM task_lists WHERE area=? ORDER BY work_date DESC', (selected_area,))], lists=lists)


def assign(store, data, actor):
    def operation(db):
        selected_area, work_date = area(data.get('area')), day(data.get('date'))
        workers, rows = data.get('worker_ids'), data.get('items')
        if not isinstance(workers, list) or not 1 <= len(workers) <= 30 or any(type(i) is not int for i in workers) or len(set(workers)) != len(workers):
            raise DomainError('Selecciona entre 1 y 30 trabajadores distintos.')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
            raise DomainError('Selecciona entre 1 y 50 tareas.')
        versions = data.get('versions')
        if not isinstance(versions, dict):
            raise DomainError('Actualiza las listas antes de asignar.')
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                raise DomainError('Tarea no válida.')
            task = get(db, 'task_catalog', integer(row.get('task_id'), 'Tarea'))
            if task['area'] != selected_area:
                raise DomainError('La tarea pertenece a otra área.')
            check_version(task, row)
            prepared.append(dict(task_id=task['id'], title=task['title'], category=task['category'],
                                 description=task['description'], status='pending', **fields(row)))
        result = []
        for worker_id in workers:
            worker = get(db, 'task_workers', worker_id)
            if worker['area'] != selected_area:
                raise DomainError('El trabajador pertenece a otra área.')
            row = db.execute('SELECT id FROM task_lists WHERE area=? AND work_date=? AND worker_id=?', (selected_area, work_date, worker_id)).fetchone()
            before = get(db, 'task_lists', row[0]) if row else None
            expected = versions.get(str(worker_id))
            if type(expected) is not int or expected != (before['version'] if before else 0):
                raise DomainError('La lista cambió. Actualiza antes de agregar tareas.', 409)
            items = (before['items'] if before else []) + [dict(item, id=uuid.uuid4().hex) for item in prepared]
            if len(items) > 100:
                raise DomainError('Cada lista admite hasta 100 tareas.')
            if before:
                identity = before['id']
                db.execute('UPDATE task_lists SET items_json=?,version=version+1,actor=?,updated_at=? WHERE id=?', (json.dumps(items), actor, now(), identity))
            else:
                identity = db.execute('INSERT INTO task_lists(area,work_date,worker_id,worker_name,items_json,updated_at,actor) VALUES(?,?,?,?,?,?,?)',
                                      (selected_area, work_date, worker_id, worker['name'], json.dumps(items), now(), actor)).lastrowid
            after = get(db, 'task_lists', identity)
            audit(db, 'lists', identity, before, after, actor)
            result.append(after)
        return {'lists': result}
    return mutate(store, 'assign', data, actor, operation)


def update_list(store, identity, data, actor):
    def operation(db):
        before = get(db, 'task_lists', identity)
        check_version(before, data)
        rows = data.get('items')
        if not isinstance(rows, list) or len(rows) != len(before['items']) or any(not isinstance(r, dict) for r in rows):
            raise DomainError('Conserva todas las tareas de la lista.')
        ids = [r.get('id') for r in rows]
        if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or set(ids) != {r['id'] for r in before['items']}:
            raise DomainError('Las tareas de la lista no coinciden.')
        originals = {r['id']: r for r in before['items']}
        items = []
        for row in rows:
            status = row.get('status')
            if not isinstance(status, str) or status not in STATUSES:
                raise DomainError('Estado no válido.')
            items.append(dict(originals[row['id']], **fields(row), status=status))
        db.execute('UPDATE task_lists SET items_json=?,version=version+1,actor=?,updated_at=? WHERE id=?', (json.dumps(items), actor, now(), identity))
        after = get(db, 'task_lists', identity)
        audit(db, 'lists', identity, before, after, actor)
        return after
    return mutate(store, f'list:{identity}', data, actor, operation)


def copy_list(store, identity, data, actor):
    def operation(db):
        source = get(db, 'task_lists', identity)
        check_version(source, data)
        work_date = day(data.get('date'))
        if work_date == source['work_date']:
            raise DomainError('Elige una fecha diferente de la lista original.')
        worker = get(db, 'task_workers', integer(data.get('worker_id'), 'Trabajador'))
        if worker['area'] != source['area']:
            raise DomainError('El trabajador pertenece a otra área.')
        if db.execute('SELECT 1 FROM task_lists WHERE area=? AND work_date=? AND worker_id=?', (source['area'], work_date, worker['id'])).fetchone():
            raise DomainError('Ya existe una lista para esa persona y fecha. No se reemplazó.', 409)
        items = [dict(item, id=uuid.uuid4().hex, status='pending') for item in source['items']]
        target = db.execute('INSERT INTO task_lists(area,work_date,worker_id,worker_name,items_json,copied_from,updated_at,actor) VALUES(?,?,?,?,?,?,?,?)',
                            (source['area'], work_date, worker['id'], worker['name'], json.dumps(items), identity, now(), actor)).lastrowid
        after = get(db, 'task_lists', target)
        audit(db, 'lists', target, None, after, actor)
        return after
    return mutate(store, f'copy:{identity}', data, actor, operation)


def printable(store, identity):
    with store.connect() as db:
        value = get(db, 'task_lists', identity)
    e = lambda v: escape(str(v), quote=True)
    rows = ''.join(f'<tr><td class="check">{"☑" if r["status"] == "done" else "☐"}</td><td>{r["order"]}</td><td><strong>{e(r["title"])}</strong><small>{e(r["category"])}</small><p>{e(r["description"])}</p><p>{e(r["instructions"])}</p></td><td>{e(r["quantity"])} {e(r["unit"])}</td><td>{e(PRIORITIES[r["priority"]])}<small>{e(STATUSES[r["status"]])}</small></td></tr>'
                   for r in sorted(value['items'], key=lambda r: (r['order'], r['id'])))
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pauta · {e(value['worker_name'])} · {value['work_date']}</title><link rel="stylesheet" href="/tasks-print.css"><script src="/tasks-print.js" defer></script></head><body><nav><button id="print-tasks">Imprimir / guardar PDF</button><p>En impresión, selecciona «Guardar como PDF» si necesitas un archivo.</p></nav><header><div>OVEJA · COCINA Y CAFÉ</div><h1>{e(value['worker_name'])}</h1><p>{AREAS[value['area']]} · {value['work_date']}</p></header><table><thead><tr><th>Hecho</th><th>Orden</th><th>Tarea e instrucciones</th><th>Cantidad</th><th>Prioridad / estado</th></tr></thead><tbody>{rows}</tbody></table><footer>Casillas para revisión en papel. Marcar esta copia no modifica el ERP.<br>Lista v{value['version']} · Actualizada {e(value['updated_at'])} · Emitida {e(now())}</footer></body></html>'''


def handle(handler, store, path, query, actor):
    if handler.command == 'GET' and path == '/api/tasks':
        return handler.send(200, view(store, query.get('area', [''])[0], query.get('date', [''])[0]))
    match = re.fullmatch(r'/api/tasks/(catalog|workers)(?:/(\d+))?', path)
    if match and handler.command == ('PUT' if match[2] else 'POST'):
        return handler.send(200, save_entity(store, match[1], handler.body(), actor, int(match[2]) if match[2] else None))
    if path == '/api/tasks/assign' and handler.command == 'POST':
        return handler.send(200, assign(store, handler.body(), actor))
    match = re.fullmatch(r'/api/tasks/lists/(\d+)(?:/(copy|print))?', path)
    if match:
        identity = int(match[1])
        if match[2] == 'print' and handler.command == 'GET':
            return handler.send(200, printable(store, identity), 'text/html; charset=utf-8')
        if match[2] == 'copy' and handler.command == 'POST':
            return handler.send(200, copy_list(store, identity, handler.body(), actor))
        if not match[2] and handler.command == 'PUT':
            return handler.send(200, update_list(store, identity, handler.body(), actor))
    raise DomainError('Ruta no encontrada.', 404)
