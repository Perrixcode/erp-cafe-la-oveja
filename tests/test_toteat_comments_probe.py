from contextlib import closing, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from erp.toteat_comments_probe import inspect_sales, inspect_tables
from scripts import authorize_toteat_diagnostics as handoff
from scripts import probe_toteat_comments as runner

CATALOG = [{'id': 'DEMO-KEY', 'idToteat': 9001, 'localCode': 'DEMO-CAKE'}]
CURRENT = {'order_id': '9007199254740999', 'restaurant_id': 'DEMO', 'local_id': '1'}


def sales_payload():
    return {'ok': True, 'data': [{'orderId': 9007199254740998, 'paymentId': 9007199254740001,
        'dateOpen': '2026-10-03T12:00:00', 'dateClosed': '2026-10-03T12:05:00',
        'comment': 'Nombre: PRIVATE_FICTITIOUS_NAME\nFecha: 10/10/2026\nHora: 18:00\n' + 'Nota ficticia\n' * 700,
        'products': [{'id': 'DEMO-CAKE', 'quantity': 1, 'lineReference': 0, 'comment': 'PRODUCT_ONLY'},
                     {'id': 'DEMO-OTHER', 'comment': 'OUT_OF_SCOPE'}],
        'paymentForms': [{'comment': 'ONLY_PAYMENT Fecha: ficticia'}]}]}


class ProjectionTests(unittest.TestCase):
    def test_sales_preserves_full_original_and_separates_payment_and_product(self):
        payload = sales_payload()
        report, saved = inspect_sales(payload, CATALOG, CURRENT['order_id'])
        self.assertEqual(saved[0]['order_comments'][0]['original'], payload['data'][0]['comment'])
        self.assertEqual(saved[0]['products'][0]['comments'][0]['original'], 'PRODUCT_ONLY')
        self.assertEqual(report['matching_unique_orders'], 1)
        self.assertEqual(report['match_basis_counts'], {'localCode': 1})
        self.assertEqual(saved[0]['classification'], 'not_inferred')
        for value in ('PRIVATE_FICTITIOUS_NAME', 'PRODUCT_ONLY', 'ONLY_PAYMENT', 'OUT_OF_SCOPE'):
            self.assertNotIn(value, json.dumps(report))
        self.assertNotIn('ONLY_PAYMENT', json.dumps(saved))
        self.assertNotIn('OUT_OF_SCOPE', json.dumps(saved))
        self.assertEqual(report['matching_comment_evidence'][0]['payment_comments'][0]['path'], '$.paymentForms[0].comment')

    def test_multiple_payments_share_order_identity_without_inventing_new_order(self):
        payload = sales_payload(); other = deepcopy(payload['data'][0]); other['paymentId'] += 1
        payload['data'].append(other)
        report, saved = inspect_sales(payload, CATALOG, CURRENT['order_id'])
        self.assertEqual(report['matching_transactions'], 2)
        self.assertEqual(report['matching_unique_orders'], 1)
        self.assertEqual(saved[0]['order_id'], saved[1]['order_id'])
        self.assertNotEqual(saved[0]['payment_id'], saved[1]['payment_id'])

    def test_exact_ids_ambiguous_namespaces_and_extras_do_not_expand_scope(self):
        payload = sales_payload()
        payload['data'][0]['products'] = [{'id': 'DEMO-CAKE-extra'}, {'id': True},
            {'id': 'DEMO-CAKE', 'lineReference': 7}, {'id': 'DEMO-CAKE', 'isExtra': True}]
        report, saved = inspect_sales(payload, CATALOG, CURRENT['order_id'])
        self.assertEqual(saved, []); self.assertEqual(report['excluded_extra_matches'], 2)
        catalog = CATALOG + [{'id': 'OTHER-KEY', 'idToteat': 9002, 'localCode': 'DEMO-KEY'}]
        payload['data'][0]['products'] = [{'id': 'DEMO-KEY'}]
        report, saved = inspect_sales(payload, catalog, CURRENT['order_id'])
        self.assertEqual(saved, []); self.assertEqual(report['ambiguous_product_matches'], 1)

    def test_comment_history_array_is_retained_without_classification(self):
        payload = sales_payload()
        history = [{'text': 'Primera instrucción ficticia'}, {'text': 'Corrección ficticia'}]
        payload['data'][0]['comments'] = history
        report, saved = inspect_sales(payload, CATALOG, CURRENT['order_id'])
        self.assertEqual(saved[0]['order_comments'][1]['original'], history)
        self.assertNotIn('Corrección ficticia', json.dumps(report))

    def test_table_name_is_separate_and_unrelated_table_not_retained(self):
        payload = {'ok': True, 'data': [
            {'tableId': 1, 'tableName': 'PRIVATE_TABLE_CAPTION', 'comment': 'TABLE_COMMENT',
             'orderNotes': 'UNMAPPED_TABLE_COMMENT', 'available': False},
            {'tableId': 2, 'tableName': 'OUTSIDE_TABLE', 'comment': 'UNRELATED_TABLE'}]}
        report, saved = inspect_tables(payload, ['1'])
        self.assertEqual(report['tables_returned'], 2); self.assertEqual(report['matching_tables'], 1)
        self.assertEqual(saved[0]['display_fields']['tableName'], 'PRIVATE_TABLE_CAPTION')
        self.assertEqual(saved[0]['table_comments'][0]['original'], 'TABLE_COMMENT')
        self.assertEqual(saved[0]['unmapped_table_comment_fields']['orderNotes'], 'UNMAPPED_TABLE_COMMENT')
        self.assertFalse(report['table_name_is_order_comment'])
        self.assertNotIn('PRIVATE_TABLE_CAPTION', json.dumps(report))
        self.assertNotIn('UNRELATED_TABLE', json.dumps(saved))

    def test_empty_results_and_invalid_contract_do_not_claim_missing_comments(self):
        report, saved = inspect_sales({'ok': True, 'data': []}, CATALOG, CURRENT['order_id'])
        self.assertEqual(report['transactions_returned'], 0); self.assertEqual(saved, [])
        for payload in ({'ok': False, 'data': []}, {'ok': True, 'data': {}}, {'ok': True, 'data': [None]}):
            with self.assertRaises(ValueError): inspect_sales(payload, CATALOG, CURRENT['order_id'])
        with self.assertRaises(ValueError): inspect_tables({'ok': True, 'data': [{'tableId': True}]}, [1])

    def test_virtual_table_negative_id_keeps_sign_and_does_not_match_physical_table(self):
        payload = {'ok': True, 'data': [{'tableId': -401, 'tableName': 'Mesa virtual ficticia'},
                                      {'tableId': 401, 'tableName': 'Mesa física ficticia'}]}
        report, saved = inspect_tables(payload, [-401])
        self.assertEqual(report['matching_tables'], 1)
        self.assertEqual(saved[0]['table_id'], '-401')
        self.assertEqual(saved[0]['display_fields']['tableName'], 'Mesa virtual ficticia')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.key = json.dumps(['DEMO', '1', CURRENT['order_id']])
        names = {runner.BINARY, 'native/ToteatReader.swift', 'erp/toteat_comments_probe.py',
                 'scripts/probe_toteat_comments.py', 'scripts/authorize_toteat_diagnostics.py'}
        hashes = {}
        for name in names:
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('FICTITIOUS_NOT_EXECUTED')
            hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.manifest = {'version': 1, 'binary': runner.BINARY, 'sha256': hashes, 'sales': {'ini': '20261003', 'end': '20261004'},
                         'tables': True, 'target_source_key': self.key}
        (self.root / runner.MANIFEST).write_text(json.dumps(self.manifest))
        (self.root / 'data').mkdir()
        with closing(sqlite3.connect(self.root / 'private/toteat-reader.sqlite3')) as db, db:
            db.execute('CREATE TABLE received_orders(source_key TEXT,payload_json TEXT)')
            db.execute('INSERT INTO received_orders VALUES(?,?)', (self.key, json.dumps(CURRENT)))
        with closing(sqlite3.connect(self.root / 'data/erp-demo.sqlite3')) as db, db:
            db.execute('CREATE TABLE catalog_products(source TEXT,payload_json TEXT)')
            db.execute('INSERT INTO catalog_products VALUES(?,?)', ('toteat-manual', json.dumps({'source_product': CATALOG[0]})))

    def test_pipeline_fixed_range_and_target_table_preserves_private_original(self):
        calls = []
        def read(binary, args, current):
            calls.append(args)
            if args[0] == 'detail': return {'ok': True, 'data': {'orderId': int(CURRENT['order_id']), 'tableId': [1]}}
            if args[0] == 'sales-range': return sales_payload()
            return {'ok': True, 'data': [{'tableId': 1, 'tableName': 'PRIVATE_TABLE_CAPTION', 'comment': 'PRIVATE_TABLE_COMMENT'},
                                        {'tableId': 2, 'comment': 'UNRELATED_TABLE'}]}
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(runner.run_probe(self.root, read), 0)
        self.assertEqual(calls, [['detail', CURRENT['order_id']], ['sales-range', '20261003', '20261004'], ['tables']])
        self.assertNotIn('PRIVATE_', output.getvalue())
        files = list((self.root / 'private/toteat-comment-diagnostics').rglob('matching-originals.json'))
        self.assertEqual(len(files), 1); self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        stored = json.loads(files[0].read_text())
        self.assertEqual(stored['sales'][0]['order_comments'][0]['original'], sales_payload()['data'][0]['comment'])
        self.assertNotIn('UNRELATED_TABLE', files[0].read_text())
        with closing(sqlite3.connect(self.root / 'private/toteat-reader.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM received_orders').fetchone()[0], 1)

    def test_changed_scope_or_binary_stops_before_any_read(self):
        for field, value in [('sales', {'ini': '20261001', 'end': '20261004'}), ('binary', '/tmp/untrusted')]:
            changed = dict(self.manifest, **{field: value})
            (self.root / runner.MANIFEST).write_text(json.dumps(changed))
            with self.assertRaises(runner.ProbeFailure): runner.validate_manifest(self.root)
        (self.root / runner.MANIFEST).write_text(json.dumps(self.manifest))
        (self.root / runner.BINARY).write_text('CHANGED_BINARY')
        with self.assertRaises(runner.ProbeFailure): runner.validate_manifest(self.root)

    def test_virtual_table_detail_reaches_sales_and_tables_and_checkpoints_completed_stage(self):
        calls = []
        def read(binary, args, current):
            calls.append(args[0])
            if args[0] == 'detail': return {'ok': True, 'data': {'orderId': CURRENT['order_id'], 'tableId': [-401]}}
            checkpoint = json.loads((self.root / 'private/toteat-comments-latest-safe-report.json').read_text())
            self.assertIn('order', checkpoint['stages'])
            if args[0] == 'sales-range': return {'ok': True, 'data': []}
            self.assertIn('sales', checkpoint['stages'])
            return {'ok': True, 'data': [{'tableId': -401, 'tableName': 'Ficticia'}]}
        with redirect_stdout(io.StringIO()): self.assertEqual(runner.run_probe(self.root, read), 0)
        self.assertEqual(calls, ['detail', 'sales-range', 'tables'])
        report = json.loads((self.root / 'private/toteat-comments-latest-safe-report.json').read_text())
        self.assertTrue(report['complete']); self.assertEqual(report['stages']['tables']['matching_tables'], 1)

    def test_helper_scope_and_error_messages_are_safe(self):
        result = {'ok': True, 'scope': {'restaurant_id': 'OTHER', 'local_id': '1'}, 'payload': sales_payload()}
        with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(result).encode())):
            with self.assertRaisesRegex(runner.ProbeFailure, '^scope_mismatch$'):
                runner.read_helper(Path('/not-executed'), ['sales-range', '20261003', '20261004'], CURRENT)
        result = {'ok': False, 'error': 'UNKNOWN_PRIVATE_ERROR_TEXT', 'http_status': 429}
        with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(result).encode())):
            with self.assertRaises(runner.ProbeFailure) as caught:
                runner.read_helper(Path('/not-executed'), ['tables'], CURRENT)
        self.assertEqual(caught.exception.safe()['error'], 'helper_error')

    def test_rate_limit_stops_next_request_without_retries(self):
        calls = []
        def read(binary, args, current):
            calls.append(args[0])
            if args[0] == 'detail': return {'ok': True, 'data': {'orderId': CURRENT['order_id'], 'tableId': [1]}}
            raise runner.ProbeFailure('http_error', http_status=429)
        with redirect_stdout(io.StringIO()): self.assertEqual(runner.run_probe(self.root, read), 1)
        self.assertEqual(calls, ['detail', 'sales-range'])

    def test_handoff_requires_fresh_process_proof_before_probe(self):
        for result, permitted in [({'ok': True, 'version_authorized': True, 'token_exported': False}, False),
                                  ({'ok': True, 'persistent_access_verified': True, 'token_exported': False}, True)]:
            responses = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())]
            with patch.object(handoff.sys, 'platform', 'darwin'), patch.object(handoff.sys.stdin, 'isatty', return_value=True), \
                 patch.object(handoff.subprocess, 'run', side_effect=responses) as run, \
                 patch.object(handoff, 'run_probe', return_value=0) as probe, redirect_stdout(io.StringIO()):
                if permitted: self.assertEqual(handoff.main(self.root), 0)
                else:
                    with self.assertRaises(runner.ProbeFailure): handoff.main(self.root)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(probe.call_count, 1 if permitted else 0)

    def test_noninteractive_handoff_does_not_touch_keychain(self):
        with patch.object(handoff.sys.stdin, 'isatty', return_value=False), patch.object(handoff.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(handoff.main(self.root), 1)
        run.assert_not_called()
