"""Issued manifest tested only against disposable synthetic databases."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from erp import cash_bags as bags
from erp.cash_bags_import import import_manifest, plan_import, validate_manifest, ORIGIN
from erp.cash_bags_pdf import labels_pdf
from erp.domain import DomainError
from erp.store import Store

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT/'erp/data/cash-bags-preprinted-000001-000020.json').read_text())
SECOND = json.loads((ROOT/'erp/data/cash-bags-preprinted-000021-000040.json').read_text())
PARTNER = {'username': 'socio-ficticio', 'role': 'partner'}


class CashBagImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'synthetic.sqlite3'
        self.store = Store(self.path); bags.initialize(self.store)

    def run_import(self,manifest=MANIFEST):
        with self.store.connect() as db:
            return import_manifest(db, manifest, 'Importación ficticia QA')

    def dump(self):
        with self.store.connect() as db: return list(db.iterdump())

    def test_pinned_manifest_and_permanent_urls(self):
        validate_manifest(MANIFEST)
        result = self.run_import(); batch = bags.batch_view(self.store, result['batch_id'])
        self.assertEqual([b['id'] for b in batch['bags']], [m['public_id'] for m in MANIFEST])
        self.assertEqual([ORIGIN+b['path'] for b in batch['bags']], [m['url'] for m in MANIFEST])
        self.assertTrue(all(b['status']=='blank' and b['counted'] is None for b in batch['bags']))
        self.assertEqual(result['next_number'], 21)
        before = self.dump(); replay = self.run_import()
        self.assertEqual(replay['status'], 'already_imported'); self.assertEqual(before, self.dump())
        self.assertEqual(replay['batch_id'], result['batch_id'])

    def test_production_creation_blocked_until_manifest_import(self):
        body = dict(request_id=uuid4().hex, confirmed=True, quantity=1)
        before = self.dump()
        with self.assertRaises(DomainError): bags.execute(self.store, 'create', body, PARTNER, ORIGIN)
        self.assertEqual(before, self.dump())
        self.run_import()
        before=self.dump()
        with self.assertRaises(DomainError): bags.execute(self.store, 'create', body, PARTNER, ORIGIN)
        self.assertEqual(before,self.dump())
        self.run_import(SECOND)
        result = bags.execute(self.store, 'create', body, PARTNER, ORIGIN)
        self.assertEqual(result['first_label'], 'BOL-000041')
        self.assertEqual(self.run_import()['next_number'], 42)
        self.assertEqual(self.run_import(SECOND)['next_number'], 42)

    def test_listing_blocks_new_lots_until_issued_manifest_is_imported(self):
        class ReadHandler:
            command='GET'
            def send(self,status,payload): return payload
        before=self.dump()
        result=bags.handle(ReadHandler(),self.store,'/api/cash-bags',{},PARTNER,ORIGIN)
        self.assertTrue(result['import_pending']);self.assertFalse(result['can_create'])
        self.assertEqual(before,self.dump())
        self.run_import()
        result=bags.handle(ReadHandler(),self.store,'/api/cash-bags',{},PARTNER,ORIGIN)
        self.assertTrue(result['import_pending']);self.assertFalse(result['can_create'])
        self.run_import(SECOND)
        result=bags.handle(ReadHandler(),self.store,'/api/cash-bags',{},PARTNER,ORIGIN)
        self.assertFalse(result['import_pending']);self.assertTrue(result['can_create'])
        self.assertEqual(result['next_number'],41)

    def test_second_requires_first_then_both_reimport_in_any_order(self):
        before=self.dump()
        with self.assertRaises(DomainError):self.run_import(SECOND)
        self.assertEqual(before,self.dump())
        first=self.run_import();second=self.run_import(SECOND)
        self.assertEqual(second['next_number'],41)
        self.assertNotEqual(first['batch_id'],second['batch_id'])
        self.assertEqual(len({r['public_id'] for r in MANIFEST+SECOND}),40)
        before=self.dump()
        for manifest in [SECOND,MANIFEST,SECOND,MANIFEST]:
            result=self.run_import(manifest)
            self.assertEqual(result['status'],'already_imported');self.assertEqual(result['next_number'],41)
        self.assertEqual(before,self.dump())

    def test_second_number_collision_rolls_back_without_touching_first(self):
        first=self.run_import()
        with self.store.connect() as db:
            db.execute('INSERT INTO cash_bags(id,number,batch_id,created_at,updated_at) VALUES(?,?,?,?,?)',
                       ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',21,first['batch_id'],'QA','QA'))
        before=self.dump()
        with self.assertRaises(DomainError) as error:self.run_import(SECOND)
        self.assertIn('BOL-000021',str(error.exception));self.assertEqual(before,self.dump())

    def test_second_uuid_collision_at_another_number_rolls_back(self):
        first=self.run_import()
        with self.store.connect() as db:
            db.execute('INSERT INTO cash_bags(id,number,batch_id,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (SECOND[0]['public_id'],99,first['batch_id'],'QA','QA'))
        before=self.dump()
        with self.assertRaises(DomainError) as error:self.run_import(SECOND)
        self.assertIn('BOL-000099',str(error.exception));self.assertEqual(before,self.dump())

    def test_second_concurrent_import_and_original_pdf(self):
        self.run_import()
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:self.run_import(SECOND),range(4)))
        self.assertEqual(sum(r['status']=='imported' for r in results),1)
        self.assertEqual({r['next_number'] for r in results},{41})
        batch=bags.batch_view(self.store,results[0]['batch_id'])
        self.assertEqual([ORIGIN+b['path'] for b in batch['bags']],[m['url'] for m in SECOND])
        self.assertEqual(labels_pdf(batch),(ROOT/'erp/data/cash-bags-preprinted-000021-000040.pdf').read_bytes())
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cash_bags').fetchone()[0],40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cash_bag_events').fetchone()[0],40)

    def test_modified_uuid_number_url_or_duplicate_rejected_atomically(self):
        variants = []
        for field, value in [('sequence', True), ('numero_visible','BOL-000021'), ('public_id',str(uuid4())), ('url','https://foreign.invalid/')]:
            m = deepcopy(MANIFEST); m[0][field] = value; variants.append(m)
        m = deepcopy(MANIFEST); m[1]['public_id']=m[0]['public_id']; variants.append(m)
        m = deepcopy(MANIFEST); m[0]['public_id']=str(uuid4()); m[0]['url']=ORIGIN+'/?module=cash&bag='+m[0]['public_id']; variants.append(m)
        for manifest in [*variants, MANIFEST[:-1]]:
            before = self.dump()
            with self.store.connect() as db:
                with self.assertRaises(DomainError): import_manifest(db, manifest, 'QA')
            self.assertEqual(before, self.dump())

    def test_number_collision_blocks_entire_import(self):
        bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),PARTNER,'http://localhost:8766')
        before = self.dump()
        with self.assertRaises(DomainError) as error: self.run_import()
        self.assertIn('Colisión', str(error.exception)); self.assertEqual(before, self.dump())

    def test_uuid_collision_at_different_number_blocks_entire_import(self):
        batch = bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),PARTNER,'http://localhost:8766')
        with self.store.connect() as db:
            db.execute('INSERT INTO cash_bags(id,number,batch_id,created_at,updated_at) VALUES(?,?,?,?,?)', (MANIFEST[0]['public_id'],99,batch['batch_id'],'QA','QA'))
        before=self.dump()
        with self.assertRaises(DomainError) as error: self.run_import()
        self.assertIn('BOL-000099',str(error.exception));self.assertEqual(before,self.dump())

    def test_sequence_advance_without_records_requires_review(self):
        with self.store.connect() as db: db.execute('UPDATE cash_bag_sequence SET next_number=21')
        before=self.dump()
        with self.assertRaises(DomainError): self.run_import()
        self.assertEqual(before,self.dump())

    def test_concurrent_imports_insert_exactly_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:self.run_import(),range(4)))
        self.assertEqual(sum(r['status']=='imported' for r in results),1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cash_bags').fetchone()[0],20)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cash_bag_events').fetchone()[0],20)

    def test_cli_default_is_read_only_and_does_not_initialize_schema(self):
        path=Path(self.temp.name)/'empty-existing.sqlite3'
        sqlite3.connect(path).close(); before=path.read_bytes()
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/import_cash_bags.py'),'--db',str(path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'ready_after_schema')
        self.assertFalse(json.loads(result.stdout)['applied']);self.assertEqual(before,path.read_bytes())
        self.assertEqual(list(path.parent.glob('empty-existing.sqlite3*')),[path])

    def test_reimport_after_completion_keeps_states_and_reprint_bytes(self):
        from pypdf import PdfReader
        result=self.run_import(); batch=bags.batch_view(self.store,result['batch_id']); pdf=labels_pdf(batch)
        self.assertEqual(pdf,(ROOT/'erp/data/cash-bags-preprinted-000001-000020.pdf').read_bytes())
        b=batch['bags'][0]
        bags.execute(self.store,'complete',dict(request_id=uuid4().hex,confirmed=True,version=1,business_date='2026-10-16',register_name='CAJA QA',shift_name='QA',responsible='Persona QA',counted=100,retained=0),PARTNER,ORIGIN,b['id'])
        before=self.dump();self.run_import();self.assertEqual(before,self.dump())
        self.assertEqual(labels_pdf(bags.batch_view(self.store,batch['id'])),pdf)
        reader=PdfReader(BytesIO(pdf)); self.assertEqual(len(reader.pages),5)
        text=''.join(page.extract_text() for page in reader.pages)
        for row in MANIFEST: self.assertEqual(text.count(row['numero_visible']),1)

    def test_void_issued_label_blocks_full_reprint_without_reassigning(self):
        result=self.run_import(); batch=bags.batch_view(self.store,result['batch_id']); b=batch['bags'][0]
        bags.execute(self.store,'void',dict(request_id=uuid4().hex,confirmed=True,version=1,reason='Prueba de anulación'),PARTNER,ORIGIN,b['id'])
        before=self.dump()
        with self.assertRaises(DomainError): labels_pdf(bags.batch_view(self.store,batch['id']))
        self.run_import();self.assertEqual(before,self.dump())

    def test_reprint_validates_issued_pdf_bytes(self):
        from unittest.mock import patch
        result=self.run_import(); batch=bags.batch_view(self.store,result['batch_id'])
        with patch('pathlib.Path.read_bytes',return_value=b'pdf incorrecto'):
            with self.assertRaises(DomainError): labels_pdf(batch)


if __name__=='__main__':unittest.main()
