"""Persistent review and replacement never mutate existing invoices or payments."""
from pathlib import Path
import json
import tempfile
import threading
import unittest
from uuid import uuid4
from erp.store import Store
from erp.domain import DomainError
from erp import sii_purchases as sii, sii_review as review, payables as ap

HEAD='Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'
def csv(*rows):
    return (HEAD+'\n'.join(rows)+'\n').encode()
def row(folio=1,amount=1000,date='01/10/2026',rut='76111111-1'):
    return f'33;{rut};Proveedor QA;{folio};{date};{amount}'

class SiiReviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.path=Path(tmp.name)/'qa.sqlite3';self.store=Store(self.path)
        self.content=csv(row(),row(2,2000),row(3,3000));self.p=sii.upload(self.store,self.content,'octubre.csv','QA')
    def current(self):return sii.preview(self.store,self.p['id'])
    def save(self,**changes):
        p=self.current();draft=p['review']['draft'];draft.update(changes)
        return review.save(self.store,p['id'],dict(version=p['review']['version'],draft=draft),'QA')
    def confirm(self,rows):
        p=self.current()
        return sii.confirm(self.store,p['id'],dict(rows=rows,confirmed=True,review_version=p['review']['version']),'QA')
    def discard(self,*numbers):
        self.save(discarded=list(numbers),details={str(n):dict(discard_reason='cash',follow_up='',note='Compra de ejemplo') for n in numbers})
    def compare(self,content,name='actualizado.csv'):
        return review.compare(self.store,self.p['id'],content,name,self.current()['review']['version'])
    def replace(self,content,comparison=None):
        c=comparison or self.compare(content)
        return review.replace(self.store,self.p['id'],content,c['name'],'QA',c['version'],c['token'])
    def close(self,closed=True):
        return review.set_closed(self.store,self.p['id'],dict(version=self.current()['review']['version'],closed=closed,confirmed=True),'QA')

    def test_resume_from_another_store_preserves_selection_notes_conditions_and_filters(self):
        self.save(selected=[2,3],conditions={'2':'credit'},details={'3':dict(note='Consultar\nal proveedor',follow_up='supplier',discard_reason='')},view=dict(search='QA',supplier='761111111',filter='attention',page=2))
        p=sii.preview(Store(self.path),self.p['id']);d=p['review']['draft']
        self.assertEqual(d['selected'],[2,3]);self.assertEqual(d['conditions'],{'2':'credit'})
        self.assertEqual(d['view']['page'],2);self.assertEqual(d['details']['3']['note'],'Consultar\nal proveedor')
        self.assertEqual(p['rows'][1]['review_state'],'attention')
        self.assertEqual(ap.view(self.store)['invoices'],[])

    def test_partial_confirm_keeps_the_rest_and_retries_do_not_duplicate(self):
        self.save(selected=[2,3],conditions={'3':'cash'})
        p=self.current();data=dict(rows=[2],confirmed=True,review_version=p['review']['version'])
        self.assertEqual(sii.confirm(self.store,p['id'],data,'QA')['imported'],1)
        self.assertEqual(sii.confirm(self.store,p['id'],data,'QA')['already_imported'],1)
        p=self.current();self.assertEqual(p['review']['draft']['selected'],[3]);self.assertEqual(p['review']['progress']['pending']['count'],2)
        self.assertEqual(p['review']['progress']['imported']['amount'],1000)
        self.assertEqual(self.confirm([3])['imported'],1);self.assertEqual(len(ap.view(self.store)['invoices']),2)

    def test_discard_requires_reason_and_does_not_record_payment(self):
        for detail in ({},{'discard_reason':'other'}):
            with self.assertRaises(DomainError):self.save(discarded=[2],details={'2':detail})
        self.discard(2)
        with self.assertRaises(DomainError):self.confirm([2])
        self.assertEqual(ap.view(self.store)['invoices'],[])
        self.save(discarded=[]);self.assertEqual(self.confirm([2])['imported'],1)
        self.assertEqual(ap.view(self.store)['invoices'][0]['payments'],[])

    def test_invalid_bulk_draft_rolls_back_all_changes(self):
        before=self.current()['review']
        for change in ({'selected':[2,999]}, {'selected':[2,2]}, {'discarded':[2],'selected':[2]}, {'conditions':{'8':'cash'}}, {'details':{'2':{'note':'x'*1001}}}):
            with self.subTest(change=change),self.assertRaises(DomainError):self.save(**change)
        self.assertEqual(self.current()['review'],before)

    def test_two_sessions_cannot_overwrite_each_other(self):
        p=self.current();version=p['review']['version'];a=dict(p['review']['draft'],selected=[2]);b=dict(p['review']['draft'],selected=[3])
        review.save(self.store,p['id'],dict(version=version,draft=a),'A')
        with self.assertRaises(DomainError):review.save(self.store,p['id'],dict(version=version,draft=b),'B')
        self.assertEqual(self.current()['review']['draft']['selected'],[2])
        self.assertEqual(review.save(self.store,p['id'],dict(version=version,draft=a),'A')['version'],version+1)

    def test_stale_confirm_cannot_ignore_a_new_discard(self):
        p=self.current();self.discard(2)
        with self.assertRaises(DomainError):sii.confirm(self.store,p['id'],dict(rows=[2],confirmed=True,review_version=p['review']['version']),'QA')
        self.assertEqual(ap.view(self.store)['invoices'],[])

    def test_comparison_is_read_only_and_reports_changes_new_and_missing(self):
        content=csv(row(),row(2,2500),row(4,4000));c=self.compare(content)
        self.assertEqual(c['counts'],dict(same=1,changed=1,new=1,missing=1,ambiguous=0))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_import_batches').fetchone()[0],1)
        self.assertEqual(self.current()['review']['replaced_by'],None)

    def test_replace_preserves_paid_invoice_original_file_and_exact_discards_notes(self):
        self.confirm([2]);invoice=ap.view(self.store)['invoices'][0]
        ap.execute(self.store,'reconcile',dict(request_id=str(uuid4()),version=invoice['version'],confirmed=True,previous_paid=1000,reason='Pagada antes'),'QA',invoice['id'])
        before=ap.view(self.store)['invoices']
        self.discard(3);self.save(details={'3':dict(discard_reason='cash',note='Mantener descarte',follow_up=''),'4':dict(note='Revisar proveedor',follow_up='supplier',discard_reason='')})
        content=csv(row(3,3000),row(),row(2,2000),row(4,4000));c=self.compare(content);p=self.replace(content,c)
        self.assertEqual(ap.view(self.store)['invoices'],before)
        self.assertEqual(p['review']['draft']['discarded'],[4]);self.assertEqual(p['review']['draft']['details']['2']['note'],'Revisar proveedor')
        self.assertEqual(p['rows'][1]['review_state'],'registered')
        self.assertEqual(self.current()['review']['replaced_by'],p['id'])
        self.assertEqual(self.replace(content,c)['id'],p['id'])
        with self.assertRaises(DomainError):self.confirm([4])
        with self.store.connect() as db:self.assertEqual(bytes(review.source(db,self.p['id'])['content']),self.content)

    def test_changed_or_repeated_rows_do_not_inherit_discard(self):
        self.discard(2,3)
        p=self.replace(csv(row(1,1100),row(2,2000),row(2,2000)))
        self.assertEqual(p['review']['draft']['discarded'],[])
        self.assertTrue(all(r['review_state']=='pending' for r in p['rows']))

    def test_same_file_does_not_reset_progress(self):
        self.discard(2);before=self.current()['review'];p=self.replace(self.content)
        self.assertEqual(p['review'],before)

    def test_replacement_requires_matching_preview_and_current_version(self):
        content=csv(row(4,4000));c=self.compare(content)
        with self.assertRaises(DomainError):review.replace(self.store,self.p['id'],content,c['name'],'QA',c['version'],'fake')
        with self.assertRaises(DomainError):self.replace(csv(row(5,5000)),c)
        self.discard(2)
        with self.assertRaises(DomainError):self.replace(content,c)
        self.assertIsNone(self.current()['review']['replaced_by'])

    def test_file_already_in_library_cannot_replace_or_create_cycle(self):
        content=csv(row(4,4000));sii.upload(self.store,content,'otro.csv','QA')
        with self.assertRaises(DomainError):self.compare(content)
        self.assertIsNone(self.current()['review']['replaced_by'])

    def test_close_reopen_and_finished_state_are_separate_from_payment(self):
        with self.assertRaises(DomainError):self.close()
        self.discard(3,4);self.confirm([2]);self.close()
        p=self.current();self.assertTrue(p['review']['closed_at']);self.assertTrue(ap.view(self.store)['invoices'][0]['payment_review'])
        with self.assertRaises(DomainError):self.save(selected=[])
        with self.assertRaises(DomainError):self.compare(csv(row(4)))
        self.close(False);self.save(discarded=[4]);self.assertFalse(self.current()['review']['closed_at'])

    def test_unresolved_note_prevents_closure_even_after_import(self):
        self.save(details={'2':dict(note='Esperando NC',follow_up='credit_note',discard_reason='')})
        self.confirm([2,3,4])
        with self.assertRaises(DomainError):self.close()
        self.save(details={'2':dict(note='Resuelto',follow_up='',discard_reason='')});self.close()

    def test_saved_override_is_invalidated_when_payment_changes(self):
        self.confirm([2]);inv=ap.view(self.store)['invoices'][0]
        other=sii.upload(self.store,csv(row(),row(9,9999)),'otro.csv','QA');r=other['rows'][0]
        d=other['review']['draft'];d['selected']=[2];d['overrides']={'2':dict(action=r['review_action'],token=r['review_token'],reason='Verificado',confirmed=True)}
        review.save(self.store,other['id'],dict(version=other['review']['version'],draft=d),'QA')
        ap.execute(self.store,'reconcile',dict(request_id=str(uuid4()),version=inv['version'],confirmed=True,previous_paid=1000,reason='Anterior'),'QA',inv['id'])
        resumed=sii.preview(self.store,other['id']);self.assertEqual(resumed['review']['draft']['selected'],[]);self.assertEqual(resumed['review']['invalidated'],[2])

    def test_legacy_file_without_metadata_can_resume_and_save(self):
        with self.store.connect() as db:db.execute('DELETE FROM ap_import_reviews')
        self.assertEqual(self.current()['review']['version'],0)
        self.save(selected=[2]);self.assertEqual(self.current()['review']['version'],1)

    def test_concurrent_draft_saves_only_one_wins(self):
        p=self.current();barrier=threading.Barrier(2);results=[]
        def worker(n):
            barrier.wait()
            try:review.save(self.store,p['id'],dict(version=p['review']['version'],draft=dict(p['review']['draft'],selected=[n])),'QA');results.append('saved')
            except DomainError as err:results.append(err.status)
        threads=[threading.Thread(target=worker,args=(n,)) for n in (2,3)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,['saved',409])

    def test_invalid_amount_is_not_added_to_progress_total(self):
        p=sii.upload(self.store,csv(row(1,'invalid')),'invalid.csv','QA')
        self.assertEqual(p['review']['progress']['pending'],dict(count=1,amount=0,unknown_amount=1))
