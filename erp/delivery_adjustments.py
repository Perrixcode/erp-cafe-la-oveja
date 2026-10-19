"""Partner-entered additions and deductions; originals and cancellations stay auditable."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo
from erp.domain import DomainError


def stamp():
    return datetime.now(ZoneInfo('America/Santiago')).isoformat(timespec='seconds')


def request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,100}', value):
        raise DomainError('Falta un identificador válido para evitar registros duplicados.')
    return value


def concept(value, minimum=3, maximum=200):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise DomainError(f'Escribe un concepto o motivo de {minimum} a {maximum} caracteres.')
    return value.strip()


def create(store, data, actor):
    identifier = request_id(data.get('request_id'))
    month, driver, kind, amount = (data.get(k) for k in ('month', 'driver', 'kind', 'amount'))
    if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise DomainError('Período no válido.')
    if not isinstance(driver, str) or not driver.strip():
        raise DomainError('Selecciona un repartidor confirmado.')
    if kind not in ('addition', 'deduction') or type(amount) is not int or not 1 <= amount <= 100_000_000:
        raise DomainError('Selecciona adicional o descuento e ingresa un importe entero positivo en CLP.')
    label = concept(data.get('concept'))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM delivery_adjustments WHERE id=?', (identifier,)).fetchone()
        if existing:
            if tuple(existing[k] for k in ('month', 'driver', 'kind', 'amount', 'concept', 'actor')) != (month, driver, kind, amount, label, actor):
                raise DomainError('La solicitud ya se utilizó con otros datos.', 409)
            return dict(existing)
        if db.execute('SELECT 1 FROM delivery_adjustments WHERE void_request_id=?', (identifier,)).fetchone():
            raise DomainError('La solicitud ya se utilizó.', 409)
        if not db.execute('SELECT 1 FROM delivery_chat_records r JOIN delivery_chat_batches b USING(source_sha256) WHERE b.month=? AND r.driver=?', (month, driver)).fetchone():
            raise DomainError('No hay una base auditada de ese repartidor en el período.', 404)
        db.execute('INSERT INTO delivery_adjustments(id,month,driver,kind,amount,concept,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',
                   (identifier, month, driver, kind, amount, label, actor, stamp()))
        return dict(db.execute('SELECT * FROM delivery_adjustments WHERE id=?', (identifier,)).fetchone())


def void(store, identifier, data, actor):
    operation = request_id(data.get('request_id')); identifier = request_id(identifier)
    reason = concept(data.get('reason'), minimum=5, maximum=500)
    if type(data.get('version')) is not int:
        raise DomainError('Falta la versión del ajuste.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT * FROM delivery_adjustments WHERE id=?', (identifier,)).fetchone()
        if not current:
            raise DomainError('Ajuste no encontrado.', 404)
        if current['void_request_id'] == operation:
            if current['void_actor'] != actor or current['void_reason'] != reason:
                raise DomainError('La solicitud ya se utilizó con otros datos.', 409)
            return dict(current)
        if current['void_at'] or current['version'] != data['version']:
            raise DomainError('El ajuste cambió. Actualiza la vista antes de continuar.', 409)
        if db.execute('SELECT 1 FROM delivery_adjustments WHERE id=? OR void_request_id=?', (operation, operation)).fetchone():
            raise DomainError('La solicitud ya se utilizó.', 409)
        db.execute('UPDATE delivery_adjustments SET void_request_id=?,void_actor=?,void_at=?,void_reason=?,version=version+1 WHERE id=?',
                   (operation, actor, stamp(), reason, identifier))
        return dict(db.execute('SELECT * FROM delivery_adjustments WHERE id=?', (identifier,)).fetchone())
