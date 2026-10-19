"""Cash bag invariants against a fresh synthetic database; never reads real data."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

from erp import cash_bags as bags
from erp.cash_bags_pdf import labels_pdf
from erp.domain import DomainError
from erp.store import Store

PARTNER={'username':'socio-ficticio','role':'partner'}
CASHIER={'username':'caja-ficticia','role':'cashier'}
ORIGIN='http://127.0.0.1:8766'


class CashBagsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'synthetic.sqlite3');bags.initialize(self.store)

    def create(self,n=1,**extra):
        body={'request_id':uuid4().hex,'confirmed':True,'quantity':n,**extra}
        result=bags.execute(self.store,'create',body,PARTNER,ORIGIN)
        return bags.batch_view(self.store,result['batch_id']),body

    def complete(self,bag,**extra):
        data=dict(request_id=uuid4().hex,confirmed=True,version=bag['version'],business_date='2026-10-15',
                  register_name='Caja ficticia 1',shift_name='Turno de prueba',responsible='Responsable ficticia',counted=67300,retained=36800)
        data.update(extra)
        return bags.execute(self.store,'complete',data,CASHIER,ORIGIN,bag['id'])

    def dump(self):
        with self.store.connect() as db:return list(db.iterdump())

    def test_blank_identity_and_idempotent_batch_reservation(self):
        batch,body=self.create(4)
        self.assertEqual([b['label'] for b in batch['bags']],['BOL-000001','BOL-000002','BOL-000003','BOL-000004'])
        self.assertEqual(len({b['id'] for b in batch['bags']}),4)
        for b in batch['bags']:
            self.assertRegex(b['id'],'^'+bags.BAG_ID+'$');self.assertEqual(b['status'],'blank')
            for key in bags.FIELDS+('bag_amount','verified_amount'):self.assertIsNone(b[key])
        replay=bags.execute(self.store,'create',body,PARTNER,ORIGIN)
        self.assertEqual(replay['batch_id'],batch['id'])
        with self.assertRaises(DomainError):bags.execute(self.store,'create',{**body,'quantity':2},PARTNER,ORIGIN)
        self.assertEqual(bags.listing(self.store,{})['total'],4)

    def test_concurrent_batches_are_disjoint_and_monotonic(self):
        def reserve(_):return self.create(3)[0]
        with ThreadPoolExecutor(max_workers=6) as pool:batch=list(pool.map(reserve,range(8)))
        self.assertEqual(sorted(b['number'] for x in batch for b in x['bags']),list(range(1,25)))
        self.assertEqual(self.create()[0]['first_number'],25)

    def test_void_never_reuses_number_and_restart_keeps_sequence(self):
        batch,_=self.create();b=batch['bags'][0]
        result=bags.execute(self.store,'void',dict(request_id=uuid4().hex,confirmed=True,version=1,reason='Impresión ficticia descartada'),PARTNER,ORIGIN,b['id'])
        self.assertEqual(result['status'],'void')
        bags.initialize(self.store)
        self.assertEqual(self.create()[0]['first_number'],2)
        with self.assertRaises(DomainError):self.complete(result)

    def test_read_and_reprint_do_not_change_any_business_data(self):
        batch,_=self.create(4);before=self.dump()
        for _ in range(2):
            bags.listing(self.store,{})
            for b in batch['bags']:bags.bag_view(self.store,b['id'])
            labels_pdf(bags.batch_view(self.store,batch['id']))
        self.assertEqual(before,self.dump())

    def test_pdf_failure_does_not_allocate_another_batch(self):
        batch,body=self.create(4)
        with patch('reportlab.pdfgen.canvas.Canvas',side_effect=RuntimeError('Fallo ficticio')):
            with self.assertRaises(RuntimeError):labels_pdf(batch)
        replay=bags.execute(self.store,'create',body,PARTNER,ORIGIN)
        self.assertEqual(replay['batch_id'],batch['id'])
        self.assertEqual(self.create()[0]['first_number'],5)

    def test_manual_delivery_then_owner_count_preserves_declaration(self):
        b=self.create()[0]['bags'][0];declared=self.complete(b)
        self.assertEqual(declared['status'],'pending');self.assertEqual(declared['bag_amount'],30500)
        self.assertIsNone(declared['verified_amount']);self.assertEqual(declared['declared_by'],CASHIER['username'])
        body=dict(request_id=uuid4().hex,confirmed=True,version=declared['version'],verified_amount=30400,note='Faltan cien pesos, prueba')
        result=bags.execute(self.store,'verify',body,PARTNER,ORIGIN,b['id'])
        self.assertEqual(result['status'],'difference');self.assertEqual(result['verified_difference'],-100)
        self.assertEqual(result['counted'],67300);self.assertEqual(result['bag_amount'],30500)
        self.assertEqual(bags.execute(self.store,'verify',body,PARTNER,ORIGIN,b['id']),result)
        with self.assertRaises(DomainError):self.complete(result,counted=67400)
        self.assertEqual(len(bags.bag_view(self.store,b['id'])['history']),3)

    def test_exact_owner_count_and_zero_are_explicit(self):
        b=self.create()[0]['bags'][0];declared=self.complete(b,counted=0,retained=0)
        result=bags.execute(self.store,'verify',dict(request_id=uuid4().hex,confirmed=True,version=2,verified_amount=0,note=''),PARTNER,ORIGIN,b['id'])
        self.assertEqual(result['status'],'verified');self.assertEqual(result['verified_difference'],0)

    def test_confirmation_roles_and_observation_are_required(self):
        b=self.create()[0]['bags'][0]
        for overrides in [{'confirmed':False},{'counted':None},{'retained':70000},{'counted':True},{'business_date':'2026-02-30'}]:
            before=self.dump()
            with self.assertRaises(DomainError):self.complete(b,**overrides)
            self.assertEqual(before,self.dump())
        with self.assertRaises(DomainError):
            bags.execute(self.store,'verify',dict(request_id=uuid4().hex,confirmed=True,version=1,verified_amount=0),PARTNER,ORIGIN,b['id'])
        declared=self.complete(b)
        body=dict(request_id=uuid4().hex,confirmed=True,version=2,verified_amount=30400,note='')
        for user in [PARTNER,CASHIER,dict(username='produccion',role='production')]:
            with self.assertRaises(DomainError):bags.execute(self.store,'verify',body,user,ORIGIN,b['id'])
        self.assertEqual(bags.bag_view(self.store,b['id'])['status'],'pending')

    def test_concurrent_edit_version_conflict_and_actor_is_not_forgeable(self):
        b=self.create()[0]['bags'][0];self.complete(b)
        with self.assertRaises(DomainError) as error:self.complete(b,counted=70000)
        self.assertEqual(error.exception.status,409)
        with self.assertRaises(DomainError):self.complete(b,actor='otra-persona')

    def test_quantity_validation_never_consumes_numbers(self):
        for n in [0,-1,101,1.5,True,'4']:
            with self.assertRaises(DomainError):self.create(n)
        self.assertEqual(self.create()[0]['first_number'],1)

    def test_pagination_and_batch_progress_persist(self):
        batch,_=self.create(100);self.create(1)
        first=bags.listing(self.store,{});second=bags.listing(self.store,{'before':[str(first['next_before'])]})
        third=bags.listing(self.store,{'before':[str(second['next_before'])]})
        rows=first['bags']+second['bags']+third['bags']
        self.assertEqual(len(rows),101);self.assertEqual(len({b['id'] for b in rows}),101)
        self.complete(batch['bags'][0])
        progress=next(x for x in bags.listing(self.store,{})['batches'] if x['id']==batch['id'])
        self.assertEqual(progress['pending_count'],1);self.assertEqual(progress['blank_count'],99)

    def test_no_payment_stock_or_toteat_side_effects(self):
        b=self.create()[0]['bags'][0];self.complete(b)
        with self.store.connect() as db:
            for name in ['ap_payments','ap_account_entries','orders']:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0],0)

    def test_origin_is_server_controlled_and_not_a_secret_url(self):
        for origin in ['http://example.com','https://example.com/a','https://user:pass@example.com','https://example.com?token=1']:
            with self.assertRaises(DomainError):bags.checked_origin(origin)
        self.assertEqual(bags.checked_origin('https://erp.example.test'),'https://erp.example.test')

    def test_pdf_letter_geometry_and_preprinted_blank_fields(self):
        from pypdf import PdfReader
        batch,_=self.create(5);first=batch['bags'][0]
        self.complete(first)
        latest=bags.batch_view(self.store,batch['id'])
        pdf=labels_pdf(latest);reader=PdfReader(BytesIO(pdf))
        self.assertEqual(len(reader.pages),2)
        self.assertEqual(tuple(float(x) for x in reader.pages[0].mediabox),(0,0,612,792))
        text=reader.pages[0].extract_text()
        for number in range(1,5):self.assertIn(f'BOL-{number:06d}',text)
        self.assertNotIn('BOL-000005',text);self.assertIn('BOL-000005',reader.pages[1].extract_text())
        self.assertNotIn('67300',text);self.assertNotIn('Responsable ficticia',text)
        self.assertIn('PRUEBA LOCAL',text)
        commands=reader.pages[0].get_contents().get_data().decode('latin1')
        self.assertEqual(commands.count('198.4252 340.1575 re'),4)


if __name__=='__main__':unittest.main()
