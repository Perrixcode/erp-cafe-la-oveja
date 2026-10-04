"""Retirada reversible de ejemplos; el respaldo conserva toda la base anterior."""
import hashlib
import json
import re
from contextlib import closing
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from erp.catalog import CATALOG
from erp.domain import DomainError


def fingerprint(db):
    result = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [tuple(row) for row in db.execute(f'SELECT * FROM {quoted} ORDER BY rowid')]
        result[name] = hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return result


def remove_demo_data(store, backup_dir):
    backup_dir = Path(backup_dir)
    real = [p for p in store.catalog() if p.get('source') == 'toteat-manual']
    if not real:
        raise DomainError('Se requiere un catálogo local verificado antes de retirar los ejemplos.')
    with store.connect() as db:
        mode = db.execute("SELECT value FROM metadata WHERE key='operating_mode'").fetchone()
        if mode and mode[0] == 'toteat-local':
            return {'already_clean':True}
        orders = [dict(row) for row in db.execute('SELECT id,source,source_id,customer,is_demo FROM orders')]
        demo_ids, ambiguous = [], []
        for order in orders:
            known = (order['is_demo'] == 1 and order['source'] in {'demo','demo-toteat'}
                     and order['source_id'].startswith('DEMO-')
                     and re.match(r'^cliente demo(?:\s|$)', order['customer'], re.IGNORECASE))
            (demo_ids if known else ambiguous).append(order['id'])
        if ambiguous:
            raise DomainError('Hay pedidos de origen ambiguo: se conservan todos. Revisa su clasificación antes de limpiar.')
        before = fingerprint(db)
        backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup = backup_dir / ('before-demo-cleanup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8] + '.sqlite3')
        with closing(sqlite3.connect(backup)) as dest:
            db.backup(dest)
            if fingerprint(dest) != before or dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise DomainError('El respaldo no se pudo verificar. No se retiraron ejemplos.')
        backup.chmod(0o600)
        db.execute('BEGIN IMMEDIATE')
        if fingerprint(db) != before:
            raise DomainError('La base cambió durante el respaldo. No se retiraron ejemplos; vuelve a revisar.')
        counts = {}
        for table in ('simulation_orders','order_scheduling','documents','history','items','orders'):
            column = 'id' if table == 'orders' else 'order_id'
            counts[table] = 0
            for order_id in demo_ids:
                counts[table] += db.execute(f'DELETE FROM {table} WHERE {column}=?',(order_id,)).rowcount
        demo_pairs = {(p['flavor'],p['size']) for p in CATALOG}
        real_pairs = {(p['flavor'],p['size']) for p in real}
        demo_pairs -= real_pairs
        counts['stock'] = sum(db.execute('DELETE FROM stock WHERE flavor=? AND size=?',pair).rowcount for pair in demo_pairs)
        counts['stock_history'] = 0
        for row in db.execute('SELECT id,before_json,after_json FROM stock_history').fetchall():
            old,new = json.loads(row['before_json'] or 'null'),json.loads(row['after_json'])
            if (new.get('flavor'),new.get('size')) in demo_pairs and (old is None or (old.get('flavor'),old.get('size')) in demo_pairs):
                counts['stock_history'] += db.execute('DELETE FROM stock_history WHERE id=?',(row['id'],)).rowcount
        demo_skus = {p['sku'] for p in CATALOG}
        demo_skus.update(row['sku'] for row in db.execute("SELECT sku FROM catalog_products WHERE source='manual-demo'"))
        for table in ('recipe_history','recipes'):
            counts[table] = sum(db.execute(f'DELETE FROM {table} WHERE sku=?',(sku,)).rowcount for sku in demo_skus)
        counts['catalog_products'] = db.execute("DELETE FROM catalog_products WHERE source='manual-demo'").rowcount
        db.execute("INSERT INTO metadata VALUES('operating_mode','toteat-local') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        db.execute("INSERT INTO metadata VALUES('demo_cleanup_backup',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(backup),))
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise DomainError('Relaciones inconsistentes: se revirtió la limpieza.')
    return {'backup':str(backup),'removed':counts,'real_products_preserved':len(real),'operating_mode':'toteat-local'}
