"""Diagnóstico GET acotado, ejecutado solo con un helper ya autorizado por macOS."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from erp.toteat_comments_probe import inspect_sales, inspect_tables, original_comments, safe_comment_summary, identity, table_identity

ROOT = Path(__file__).resolve().parents[1]
BINARY = 'private/oveja-toteat-diagnostics'
MANIFEST = 'private/toteat-diagnostics-manifest.json'


class ProbeFailure(Exception):
    def __init__(self, code, http_status=None, osstatus=None):
        self.code, self.http_status, self.osstatus = code, http_status, osstatus
        super().__init__(code)

    def safe(self):
        return {'ok': False, 'error': self.code, 'http_status': self.http_status, 'osstatus': self.osstatus}


def validate_manifest(root):
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest.get('version') != 1 or manifest.get('binary') != BINARY:
        raise ProbeFailure('invalid_prepared_version')
    # Este handoff congela las dos fechas ya autorizadas, no acepta argumentos del usuario.
    if manifest.get('sales') != {'ini': '20261003', 'end': '20261004'} or manifest.get('tables') is not True:
        raise ProbeFailure('prepared_scope_changed')
    expected_files = {BINARY, 'native/ToteatReader.swift', 'erp/toteat_comments_probe.py',
                      'scripts/probe_toteat_comments.py', 'scripts/authorize_toteat_diagnostics.py'}
    hashes = manifest.get('sha256', {})
    if set(hashes) != expected_files:
        raise ProbeFailure('invalid_prepared_manifest')
    for name, digest in hashes.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ProbeFailure('prepared_version_changed')
    if not isinstance(manifest.get('target_source_key'), str):
        raise ProbeFailure('missing_target_order')
    return manifest


def local_context(root, manifest):
    with closing(sqlite3.connect((root / 'private/toteat-reader.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        saved = db.execute('SELECT payload_json FROM received_orders WHERE source_key=?',
                           (manifest['target_source_key'],)).fetchone()
    if not saved:
        raise ProbeFailure('target_order_not_found')
    current = json.loads(saved[0])
    with closing(sqlite3.connect((root / 'data/erp-demo.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        catalog = [json.loads(r[0])['source_product'] for r in
                   db.execute("SELECT payload_json FROM catalog_products WHERE source='toteat-manual'")]
    if not catalog:
        raise ProbeFailure('verified_catalog_missing')
    return current, catalog


def read_helper(binary, arguments, current):
    if Path(binary).name=='systemd-toteat':
        from erp.toteat_transport import fetch
        return fetch(arguments,current)['payload']
    try:
        process = subprocess.run([str(binary), *arguments], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=26)
        if len(process.stdout) > 4 * 1024 * 1024:
            raise ProbeFailure('helper_output_limit')
        result = json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        raise ProbeFailure('helper_timeout') from None
    except (OSError, ValueError, TypeError):
        raise ProbeFailure('invalid_helper_response') from None
    if not isinstance(result, dict):
        raise ProbeFailure('invalid_helper_response')
    if result.get('ok') is not True:
        allowed = {'keychain_access_required', 'keychain_unavailable', 'http_error', 'network_error',
                   'timeout', 'response_too_large', 'redirect_blocked', 'credential_reflection_blocked'}
        error = result.get('error')
        raise ProbeFailure(error if error in allowed else 'helper_error',
                           result.get('http_status') if type(result.get('http_status')) is int else None,
                           result.get('osstatus') if type(result.get('osstatus')) is int else None)
    scope = result.get('scope', {})
    if not isinstance(scope, dict) or any(str(scope.get(k)) != current[k] for k in ('restaurant_id', 'local_id')):
        raise ProbeFailure('scope_mismatch')
    payload = result.get('payload')
    if not isinstance(payload, dict) or payload.get('ok') is not True:
        raise ProbeFailure('provider_success_not_confirmed')
    return payload


def run_probe(root=ROOT, reader=read_helper):
    os.umask(0o077)
    manifest = validate_manifest(root)
    current, catalog = local_context(root, manifest)
    binary = root / BINARY
    report = {'ok': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'scope_verified': True, 'sales_range': manifest['sales'], 'stages': {},
              'imported_operational_orders': 0, 'automatic_scheduling': False, 'complete': False, 'phase': 'order'}
    private = {'order_id': current['order_id'], 'source_key': manifest['target_source_key']}
    directory = root / 'private/toteat-comment-diagnostics' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, mode=0o700)
    def checkpoint():
        for path, data in [(directory / 'matching-originals.json', private),
                           (directory / 'safe-report.json', report),
                           (root / 'private/toteat-comments-latest-safe-report.json', report)]:
            temporary = path.with_suffix('.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                json.dump(data, file, ensure_ascii=False, indent=2); file.write('\n')
            temporary.replace(path)
    checkpoint()
    detail = reader(binary, ['detail', current['order_id']], current).get('data')
    if not isinstance(detail, dict) or identity(detail.get('orderId')) != current['order_id']:
        raise ProbeFailure('target_order_mismatch')
    table_ids = detail.get('tableId', [])
    if not isinstance(table_ids, list):
        raise ProbeFailure('invalid_target_tables')
    try:
        table_ids = [table_identity(value) for value in table_ids]
    except ValueError:
        raise ProbeFailure('invalid_target_tables') from None
    order_comments = original_comments(detail, '$')
    report['stages']['order'] = {'ok': True, 'same_order': True, 'target_table_count': len(table_ids),
                                 'order_comments': safe_comment_summary(order_comments)}
    private['order_comments'] = order_comments
    private['target_table_ids'] = table_ids
    checkpoint()
    for name, arguments, projector in [
        ('sales', ['sales-range', manifest['sales']['ini'], manifest['sales']['end']],
         lambda payload: inspect_sales(payload, catalog, current['order_id'])),
        ('tables', ['tables'], lambda payload: inspect_tables(payload, table_ids))]:
        report['phase'] = name; checkpoint()
        try:
            payload = reader(binary, arguments, current)
            evidence, originals = projector(payload)
            report['stages'][name] = dict(ok=True, **evidence)
            private[name] = originals
        except (ProbeFailure, ValueError) as error:
            failure = error if isinstance(error, ProbeFailure) else ProbeFailure('provider_contract_changed')
            report['stages'][name] = failure.safe(); report['ok'] = False
            if failure.code.startswith('keychain_') or failure.http_status in (400, 401, 403, 429):
                checkpoint()
                break
        checkpoint()
    report['complete'] = all(name in report['stages'] for name in ('order', 'sales', 'tables'))
    report['phase'] = 'finished'; checkpoint()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(run_probe())
    except ProbeFailure as error:
        print(json.dumps(error.safe())); raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print(json.dumps({'ok': False, 'error': 'local_diagnostic_failed'})); raise SystemExit(1)
