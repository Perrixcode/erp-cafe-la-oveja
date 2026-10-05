"""Seguimiento de Ovejita por socket Unix privado; sin pagos ni envíos.

Solo agrega resoluciones y su clave de idempotencia. Conserva la tabla utilizada
por cierres.py y monitor.py, sin dar acceso a SQLite al proceso web del ERP.
"""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


class FollowupError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def source_reference(row):
    values = [row[key] for key in ('id', 'fecha', 'orden_id', 'huella')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def prepare(database):
    with closing(sqlite3.connect(database, timeout=5)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS erp_followup_requests (
            request_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL,
            resolution_id INTEGER NOT NULL UNIQUE REFERENCES resoluciones(id))''')


def current(db, revision_id):
    row = db.execute('SELECT id,fecha,orden_id,huella,compatible FROM revisiones WHERE id=?', (revision_id,)).fetchone()
    if not row:
        raise FollowupError('La revisión no existe.', 404)
    history = [dict(r) for r in db.execute('SELECT id,fecha,usuario,accion,motivo FROM resoluciones WHERE revision_id=? ORDER BY id', (revision_id,))]
    return {'id': revision_id, 'source_ref': source_reference(row), 'compatible': bool(row['compatible']),
            'followup_version': history[-1]['id'] if history else 0,
            'seguimiento': history[-1]['accion'] if history else 'pendiente', 'history': history}


def read_histories(database, ids):
    if not isinstance(ids, list) or len(ids) > 500 or any(type(v) is not int or v < 1 for v in ids):
        raise FollowupError('Selección de revisiones no válida.')
    with closing(sqlite3.connect(database, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return [current(db, rid) for rid in dict.fromkeys(ids)]


def resolve(database, payload):
    rid, version = payload.get('revision_id'), payload.get('version')
    actor, action, reason = payload.get('actor'), payload.get('action'), payload.get('reason')
    request_id, reference = payload.get('request_id'), payload.get('source_ref')
    if type(rid) is not int or rid < 1 or type(version) is not int or version < 0:
        raise FollowupError('Revisión o versión no válida.')
    if not isinstance(actor, str) or not 1 <= len(actor) <= 80 or any(ord(c) < 32 for c in actor):
        raise FollowupError('Socio no válido.')
    if action not in ('revisado', 'pendiente') or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000:
        raise FollowupError('Indica una acción y un motivo de entre 5 y 1000 caracteres.')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', request_id):
        raise FollowupError('Identificador de operación no válido.')
    if not isinstance(reference, str) or not re.fullmatch(r'[a-f0-9]{64}', reference):
        raise FollowupError('Referencia de origen no válida.')
    reason = reason.strip()
    digest = hashlib.sha256(json.dumps([rid, version, actor, action, reason, reference], ensure_ascii=False).encode()).hexdigest()
    with closing(sqlite3.connect(database, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT payload_sha256,resolution_id FROM erp_followup_requests WHERE request_id=?', (request_id,)).fetchone()
        if previous:
            if previous['payload_sha256'] != digest:
                raise FollowupError('La operación ya existe con otros datos.', 409)
            return dict(current(db, rid), saved_event_id=previous['resolution_id'], repeated=True)
        state = current(db, rid)
        if state['source_ref'] != reference or state['followup_version'] != version:
            raise FollowupError('El seguimiento cambió. Actualiza la revisión antes de guardar.', 409)
        if state['compatible']:
            raise FollowupError('Los comprobantes compatibles no requieren seguimiento manual.', 409)
        if state['seguimiento'] == action:
            raise FollowupError('La revisión ya tiene ese estado. Actualiza el detalle.', 409)
        event_id = db.execute('INSERT INTO resoluciones (revision_id,fecha,usuario,accion,motivo) VALUES (?,?,?,?,?)',
                             (rid, datetime.now(timezone.utc).isoformat(), actor, action, reason)).lastrowid
        db.execute('INSERT INTO erp_followup_requests VALUES (?,?,?)', (request_id, digest, event_id))
        return dict(current(db, rid), saved_event_id=event_id, repeated=False)
