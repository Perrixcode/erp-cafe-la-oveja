"""Fictional folio corrections; temporary DB only, no business operations."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4
from erp import payables as ap, sii_purchases as sii
from erp.domain import DomainError
from erp.payable_duplicates import HistoricalIdentityWarning
from erp.store import Store

class HistoricalFolioTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'fictional.sqlite3')
        self.supplier=self.do('supplier',{'name':'Proveedor histórico ficticio','tax_id':'76111111-1'})
        self.fields=dict(supplier_id=self.supplier['id'],document_type='invoice',folio='00042',amount=10000,issued_on='2026-10-01',due_on='',condition='credit')
        self.original=self.do('invoice',self.fields)
        self.corrected=self.do('invoice',dict(self.fields,folio='99',version=1,reason='Corregir folio ficticio'),self.original['id'])
    def do(self,action,body,target=None,actor='Socio QA'):
        return ap.execute(self.store,action,dict(body,request_id=str(uuid4())),actor,target)
    def snapshot(self):
        with self.store.connect() as db:
            return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in ('ap_suppliers','ap_invoices','ap_events','ap_requests','ap_invoice_sources','ap_import_resolutions','ap_payments','ap_account_entries')}
    def warning(self,fields=None,target=None,actor='Socio QA'):
        before=self.snapshot()
        with self.assertRaises(HistoricalIdentityWarning) as error:self.do('invoice',fields or self.fields,target,actor)
        self.assertEqual(before,self.snapshot())
        return error.exception.historical_duplicate
    def confirmation(self,warning):return dict(confirmed=True,token=warning['token'],reason='Original ficticio cotejado: folio anterior era un error')
    def upload(self,folio='00042'):
        content=f'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n33;76111111-1;Proveedor histórico ficticio;{folio};01/10/2026;10000\n'
        return sii.upload(self.store,content.encode(),'ficticio.csv','Socio QA')
    def test_a_to_b_new_a_requires_confirmation_and_cancel_changes_nothing(self):
        warning=self.warning();self.assertEqual(warning['matches'][0]['current_folio'],'99')
        self.assertTrue(all(m['historical_only'] for m in warning['matches']))
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_confirm_preserves_history_original_and_audits_once(self):
        before=self.snapshot();warning=self.warning()
        body=dict(self.fields,historical_confirmation=self.confirmation(warning),request_id=str(uuid4()))
        result=ap.execute(self.store,'invoice',body,'Socio QA')
        self.assertEqual(result['folio'],'00042');self.assertNotEqual(result['id'],self.original['id'])
        after=self.snapshot();self.assertEqual(result,ap.execute(self.store,'invoice',body,'Socio QA'));self.assertEqual(after,self.snapshot())
        with self.store.connect() as db:
            self.assertEqual(ap.record(db,'ap_invoices',self.original['id']),self.corrected)
            event=dict(db.execute("SELECT * FROM ap_events WHERE action='historical_identity_confirmed'").fetchone())
            self.assertEqual(event['actor'],'Socio QA');self.assertEqual(event['invoice_id'],result['id']);self.assertTrue(event['reason'])
            proof=json.loads(event['before_json']);self.assertEqual(proof['matches'][0]['current_folio'],'99')
            self.assertEqual(json.loads(event['after_json'])['review_token'],warning['token'])
        self.assertEqual(self.snapshot()['ap_events'][:len(before['ap_events'])],before['ap_events'])
    def test_empty_reason_false_flag_and_forged_token_do_not_create(self):
        warning=self.warning();before=self.snapshot()
        for change in ({'reason':''},{'confirmed':False},{'confirmed':'true'},{'token':'forged'}):
            with self.subTest(change=change),self.assertRaises(DomainError):
                self.do('invoice',dict(self.fields,historical_confirmation=dict(self.confirmation(warning),**change)))
            self.assertEqual(before,self.snapshot())
    def test_token_is_bound_to_candidate_actor_and_current_invoice_version(self):
        warning=self.warning();confirmation=self.confirmation(warning)
        for change in ({'amount':12000},{'folio':'42'},{'due_on':'2026-10-20'}):
            with self.subTest(change=change),self.assertRaises(HistoricalIdentityWarning):self.do('invoice',dict(self.fields,**change,historical_confirmation=confirmation))
        with self.assertRaises(HistoricalIdentityWarning):self.do('invoice',dict(self.fields,historical_confirmation=confirmation),actor='Otro socio QA')
        self.do('invoice',dict(self.corrected,folio='100',reason='Segunda corrección ficticia'),self.corrected['id'])
        with self.assertRaises(HistoricalIdentityWarning):self.do('invoice',dict(self.fields,historical_confirmation=confirmation))
    def test_current_duplicate_blocks_even_with_confirmation_and_client_bypass_flag(self):
        warning=self.warning();confirmation=self.confirmation(warning)
        self.do('invoice',dict(self.fields,historical_confirmation=confirmation));before=self.snapshot()
        for folio in ('42','00042','000042'):
            with self.subTest(folio=folio),self.assertRaises(DomainError) as error:
                self.do('invoice',dict(self.fields,folio=folio,historical_confirmation=confirmation,check_duplicates=False))
            self.assertNotIsInstance(error.exception,HistoricalIdentityWarning);self.assertEqual(before,self.snapshot())
    def test_paid_or_void_current_identity_still_blocks(self):
        for state in ('paid','void'):
            with self.subTest(state=state):
                fields=dict(self.fields,folio='101' if state=='paid' else '102')
                invoice=self.do('invoice',fields)
                if state=='paid':self.do('payment',dict(version=1,amount=10000,paid_on='2026-10-02',method='cash',confirmed=True),invoice['id'])
                else:self.do('void_invoice',dict(version=1,reason='Anulación ficticia'),invoice['id'])
                with self.assertRaises(DomainError) as error:self.do('invoice',dict(fields,historical_confirmation={'confirmed':True,'token':'fake','reason':'QA'}))
                self.assertNotIsInstance(error.exception,HistoricalIdentityWarning)
    def test_two_confirmations_race_creates_only_one(self):
        confirmation=self.confirmation(self.warning());barrier=threading.Barrier(2);results=[]
        def run():
            barrier.wait()
            try:self.do('invoice',dict(self.fields,historical_confirmation=confirmation));results.append('created')
            except DomainError as error:results.append(error.status)
        workers=[threading.Thread(target=run) for _ in range(2)]
        for worker in workers:worker.start()
        for worker in workers:worker.join()
        self.assertCountEqual(results,['created',409]);self.assertEqual(len(ap.view(self.store)['invoices']),2)
    def test_edit_can_confirm_replaced_folio_but_keeps_current_duplicate_guard(self):
        other=self.do('invoice',dict(self.fields,folio='200'))
        fields=dict(other,folio='42',reason='Corregir otro documento ficticio')
        warning=self.warning(fields,other['id'])
        result=self.do('invoice',dict(fields,historical_confirmation=self.confirmation(warning)),other['id'])
        self.assertEqual(result['folio'],'42')
        with self.assertRaises(DomainError):self.do('invoice',dict(self.corrected,folio='00042',reason='QA'),self.corrected['id'])
    def test_sii_history_only_is_explicit_create_not_automatic_link(self):
        preview=self.upload();row=preview['rows'][0]
        self.assertEqual((row['status'],row['review_action'],row['historical_only']),('conflict','create',True))
        with self.assertRaises(DomainError):sii.confirm(self.store,preview['id'],dict(rows=[2],confirmed=True),'Socio QA')
        decision=dict(confirmed=True,action='create',token=row['review_token'],reason='Otro documento ficticio revisado')
        body=dict(rows=[2],confirmed=True,overrides={'2':decision})
        result=sii.confirm(self.store,preview['id'],body,'Socio QA');self.assertEqual((result['imported'],result['linked']),(1,0))
        self.assertEqual(sii.confirm(self.store,preview['id'],body,'Socio QA')['already_imported'],1)
        current=ap.view(self.store)['invoices'];new=next(i for i in current if i['id']!=self.original['id'])
        self.assertTrue(new['payment_review']);self.assertEqual(new['paid'],0)
        with self.store.connect() as db:self.assertEqual(ap.record(db,'ap_invoices',self.original['id']),self.corrected)
    def test_sii_rechecks_current_duplicate_before_confirming_old_warning(self):
        preview=self.upload();row=preview['rows'][0]
        self.do('invoice',dict(self.fields,historical_confirmation=self.confirmation(self.warning())))
        before=self.snapshot()
        with self.assertRaises(DomainError):sii.confirm(self.store,preview['id'],dict(rows=[2],confirmed=True,overrides={'2':dict(confirmed=True,action='create',token=row['review_token'],reason='QA')}),'Socio QA')
        self.assertEqual(before,self.snapshot());self.assertEqual(sii.preview(self.store,preview['id'])['rows'][0]['review_action'],'link')
    def test_prior_import_identity_remains_separate_blocking_evidence(self):
        p=self.upload('500');sii.confirm(self.store,p['id'],dict(rows=[2],confirmed=True),'QA')
        source=next(i for i in ap.view(self.store)['invoices'] if i['folio']=='500')
        self.do('invoice',dict(source,folio='501',reason='Corrección ficticia de importada'),source['id'])
        with self.assertRaises(DomainError) as error:self.do('invoice',dict(self.fields,folio='500',historical_confirmation={'confirmed':True,'token':'fake','reason':'QA'}))
        self.assertNotIsInstance(error.exception,HistoricalIdentityWarning)
