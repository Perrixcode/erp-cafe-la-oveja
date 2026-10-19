"""Import issued label manifests atomically. No network or bag UUID reassignment."""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from erp.cash_bags import BAG_ID, canonical, project, row_for, stamp, text
from erp.domain import DomainError

ORIGIN = 'https://erp.example.test'
# Append only the exact manifests issued by the label producer, never replacement IDs.
# PUBLIC DISTRIBUTION: synthetic labels only; never import into production.
ISSUED = {1: {'last': 20, 'manifest_sha256': '22f9e80b6a7c16d769e2171f04184221d80b65b9fe315a5e6f94ab40e1666825', 'pdf_sha256': 'b19e0a784cbe99264f55b0f7c921291e4bb210faf3d332413d8f374382659ca9'}, 21: {'last': 40, 'manifest_sha256': '226d664083e147a2fdc08f06a8089361fe4b54d98459e1d4c9ab5fb5fb9e372a', 'pdf_sha256': '4dff9a3671c6a19d795dd1828f7e91df8474f67591bcf55c856dffff5d093970'}}


def spec_for(first):
    if type(first) is not int or first not in ISSUED:
        raise DomainError('Falta incorporar el manifiesto definitivo de este lote. No se generaron identificadores.', 409)
    spec = ISSUED[first]
    return dict(spec, first=first, quantity=spec['last']-first+1,
                import_key=f'preprinted-{first:06d}-{spec["last"]:06d}-v1',
                basename=f'cash-bags-preprinted-{first:06d}-{spec["last"]:06d}')


def validate_manifest(manifest):
    if not isinstance(manifest, list) or not manifest or not isinstance(manifest[0],dict):
        raise DomainError('El manifiesto debe contener las etiquetas del lote emitido.')
    spec = spec_for(manifest[0].get('sequence'))
    if len(manifest) != spec['quantity']:
        raise DomainError('El manifiesto está incompleto o contiene etiquetas adicionales.')
    ids = set()
    for number, item in enumerate(manifest, spec['first']):
        if not isinstance(item, dict) or set(item) != {'numero_visible', 'sequence', 'public_id', 'url'}:
            raise DomainError('Estructura del manifiesto no válida.')
        key = item['public_id']
        if (type(item['sequence']) is not int or item['sequence'] != number or
            item['numero_visible'] != f'BOL-{number:06d}' or not isinstance(key, str) or
            not re.fullmatch(BAG_ID, key) or key in ids or
            item['url'] != ORIGIN+'/?module=cash&bag='+key):
            raise DomainError('Número, UUIDv4 o URL incompatible con las etiquetas emitidas.')
        ids.add(key)
    fingerprint = hashlib.sha256(canonical(manifest).encode()).hexdigest()
    if fingerprint != spec['manifest_sha256']:
        raise DomainError('El manifiesto difiere del lote definitivo. No se reasignan etiquetas.', 409)
    return fingerprint


def plan_import(db, manifest):
    """Read-only preflight, including on an ERP database without the new tables."""
    fingerprint = validate_manifest(manifest)
    spec = spec_for(manifest[0]['sequence'])
    first, last, quantity = spec['first'], spec['last'], spec['quantity']
    summary = {'first_number':first, 'quantity':quantity, 'next_number':last+1, 'manifest_sha256':fingerprint}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'cash_bags', 'cash_bag_batches', 'cash_bag_sequence', 'cash_bag_imports', 'cash_bag_events'}
    present = required & tables
    if not present:
        return dict(summary,status='ready_after_schema' if first==1 else 'waiting_previous_batch')
    if present != required:
        raise DomainError('Esquema de bolsas incompleto. Detén la importación para revisión.', 409)
    existing = db.execute('SELECT * FROM cash_bag_imports WHERE import_key=?', (spec['import_key'],)).fetchone()
    sequence = db.execute('SELECT next_number FROM cash_bag_sequence WHERE singleton=1').fetchone()
    if not sequence:
        raise DomainError('Secuencia ausente. Revisión requerida.', 409)
    if existing:
        batch = db.execute('SELECT * FROM cash_bag_batches WHERE id=?', (existing['batch_id'],)).fetchone()
        rows = list(db.execute('SELECT id,number FROM cash_bags WHERE batch_id=? ORDER BY number', (existing['batch_id'],)))
        expected = [(item['public_id'], item['sequence']) for item in manifest]
        if (existing['manifest_sha256'] != fingerprint or not batch or batch['link_origin'] != ORIGIN or
            batch['first_number'] != first or batch['quantity'] != quantity or batch['is_test'] or
            [(r['id'], r['number']) for r in rows] != expected or sequence[0] <= last):
            raise DomainError('El lote importado no coincide con el manifiesto. Detén la operación.', 409)
        return dict(summary,status='already_imported',batch_id=existing['batch_id'],next_number=sequence[0])
    ids = [item['public_id'] for item in manifest]
    rows = list(db.execute('SELECT number,id FROM cash_bags WHERE number BETWEEN ? AND ? OR id IN ('+','.join('?'*quantity)+')', [first,last,*ids]))
    if rows:
        labels = ', '.join(f'BOL-{row["number"]:06d}' for row in rows)
        raise DomainError('Colisión con registros existentes: '+labels+'. No se importó ni reasignó ninguna etiqueta.', 409)
    for previous in sorted(n for n in ISSUED if n<first):
        prior = spec_for(previous)
        prior_manifest = json.loads(Path(__file__).with_name('data').joinpath(prior['basename']+'.json').read_text())
        if plan_import(db, prior_manifest)['status'] != 'already_imported':
            raise DomainError('Importa primero el lote anterior; no se adelantó la secuencia.',409)
    if sequence[0] != first or db.execute('SELECT COUNT(*) FROM cash_bags').fetchone()[0] != first-1:
        raise DomainError('La numeración no coincide con este lote. Detén la importación para revisar números utilizados.',409)
    return dict(summary,status='ready')


def import_manifest(db, manifest, actor):
    """Caller owns connection; transaction is all-or-nothing. No new bag UUIDs."""
    actor = text(actor, 'Responsable de importación')
    if db.in_transaction:
        raise DomainError('La importación necesita una conexión sin transacción pendiente.', 409)
    with db:
        db.execute('BEGIN IMMEDIATE')
        plan = plan_import(db, manifest)
        if plan['status'] == 'already_imported':
            return plan
        if plan['status'] != 'ready':
            raise DomainError('Primero integra el esquema y los lotes anteriores; no se modificó la base.',409)
        spec = spec_for(plan['first_number'])
        batch_id, now = uuid4().hex, stamp()
        db.execute('INSERT INTO cash_bag_batches VALUES(?,?,?,?,?,?,?)', (batch_id,spec['first'],spec['quantity'],ORIGIN,0,actor,now))
        for item in manifest:
            key = item['public_id']
            db.execute('INSERT INTO cash_bags(id,number,batch_id,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (key,item['sequence'],batch_id,now,now))
            db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?,?)',
                       (key,batch_id,'imported',actor,canonical(project(row_for(db,key))),now))
        db.execute('UPDATE cash_bag_sequence SET next_number=? WHERE singleton=1',(spec['last']+1,))
        db.execute('INSERT INTO cash_bag_imports VALUES(?,?,?,?,?)',(spec['import_key'],plan['manifest_sha256'],batch_id,actor,now))
        return dict(plan,status='imported',batch_id=batch_id)
