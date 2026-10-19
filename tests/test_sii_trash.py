"""Removal of a review file preserves financial records and duplicate evidence."""
from pathlib import Path
import json
import tempfile
import unittest
from uuid import uuid4
from erp.store import Store
from erp.domain import DomainError
from erp import sii_review as review, sii_purchases as sii, payables as ap
from test_sii_review import csv, row

class SiiTrashTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.store=Store(Path(tmp.name)/'qa.sqlite3')
        self.content=csv(row(),row(2));self.p=sii.upload(self.store,self.content,'qa.csv','QA')
    def file(self):
        with self.store.connect() as db:return next(b for b in review.listing(db) if b['id']==self.p['id'])
    def trash(self,trashed=True,version=None):
        return review.set_trashed(self.store,self.p['id'],dict(trashed=trashed,version=self.file()['version'] if version is None else version,confirmed=True),'QA')
    def test_confirmation_required_and_cancel_does_not_change_file(self):
        for confirmed in (False,None,'true'):
            with self.assertRaises(DomainError):review.set_trashed(self.store,self.p['id'],dict(trashed=True,version=1,confirmed=confirmed),'QA')
        self.assertIsNone(self.file()['deleted_at']);self.assertEqual(sii.preview(self.store,self.p['id']),self.p)
    def test_restore_preserves_draft_and_survives_new_session(self):
        d=self.p['review']['draft'];d.update(selected=[2],discarded=[3],details={'3':dict(note='Pagada antes',discard_reason='paid',follow_up='')})
        review.save(self.store,self.p['id'],dict(version=1,draft=d),'QA')
        self.trash();self.store=Store(self.store.path)
        self.assertIsNotNone(self.file()['deleted_at']);self.assertEqual(self.file()['deleted_by'],'QA')
        self.trash(False);self.assertEqual(sii.preview(self.store,self.p['id'])['review']['draft'],d)
        with self.store.connect() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT action FROM ap_events WHERE action IN ('sii_file_trashed','sii_file_restored') ORDER BY id")],['sii_file_trashed','sii_file_restored'])
    def test_trashed_file_rejects_preview_save_import_replace_close_and_reupload(self):
        comparison=review.compare(self.store,self.p['id'],csv(row(3)),'nuevo.csv',1);self.trash()
        actions=[lambda:sii.preview(self.store,self.p['id']),lambda:review.save(self.store,self.p['id'],dict(version=1,draft=self.p['review']['draft']),'QA'),lambda:sii.confirm(self.store,self.p['id'],dict(rows=[2],confirmed=True),'QA'),lambda:review.compare(self.store,self.p['id'],csv(row(3)),'nuevo.csv',1),lambda:review.replace(self.store,self.p['id'],csv(row(3)),'nuevo.csv','QA',1,comparison['token']),lambda:review.set_closed(self.store,self.p['id'],dict(version=1,closed=True,confirmed=True),'QA'),lambda:sii.upload(self.store,self.content,'otra.csv','QA')]
        for action in actions:
            with self.subTest(action=action),self.assertRaises(DomainError) as err:action()
            self.assertEqual(err.exception.status,409)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_import_batches').fetchone()[0],1)
    def test_financial_records_and_paid_duplicate_detection_are_preserved(self):
        sii.confirm(self.store,self.p['id'],dict(rows=[2],confirmed=True),'QA');inv=ap.view(self.store)['invoices'][0]
        ap.execute(self.store,'reconcile',dict(request_id=str(uuid4()),version=inv['version'],confirmed=True,previous_paid=1000,reason='Pagada antes'),'QA',inv['id'])
        ap.attach(self.store,inv['id'],None,b'%PDF-QA\n%%EOF','ejemplo.pdf','QA')
        def snapshot():
            with self.store.connect() as db:return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('ap_invoices','ap_payments','ap_suppliers','ap_files','ap_invoice_sources','ap_import_batches','ap_import_resolutions')}
        before=snapshot();self.trash();self.assertEqual(snapshot(),before)
        other=sii.upload(self.store,csv(row(),row(9)),'otro.csv','QA')
        self.assertEqual(other['rows'][0]['status'],'duplicate');self.assertEqual(other['rows'][0]['review_action'],'link')
        self.trash(False);self.assertEqual(sii.preview(self.store,self.p['id'])['review']['progress']['imported']['count'],1)
    def test_stale_actions_cannot_hide_new_progress_or_undo_a_later_removal(self):
        review.save(self.store,self.p['id'],dict(version=1,draft=dict(self.p['review']['draft'],selected=[2])),'QA')
        with self.assertRaises(DomainError):self.trash(version=1)
        v=self.file()['version'];result=self.trash(version=v);self.assertEqual(self.trash(version=v),result)
        self.trash(False);self.trash()
        with self.assertRaises(DomainError):self.trash(False,version=result['version'])
    def test_closed_replaced_and_legacy_reviews_can_be_restored(self):
        with self.store.connect() as db:db.execute('DELETE FROM ap_import_reviews')
        self.trash();self.trash(False)
        p=sii.preview(self.store,self.p['id']);d=p['review']['draft'];d.update(discarded=[2,3],details={str(n):dict(discard_reason='cash') for n in (2,3)})
        saved=review.save(self.store,p['id'],dict(version=p['review']['version'],draft=d),'QA')
        review.set_closed(self.store,p['id'],dict(version=saved['version'],closed=True,confirmed=True),'QA')
        self.trash();self.trash(False);p=sii.preview(self.store,p['id']);self.assertTrue(p['review']['closed_at'])
        review.set_closed(self.store,p['id'],dict(version=p['review']['version'],closed=False,confirmed=True),'QA')
        c=review.compare(self.store,p['id'],csv(row(3)),'nuevo.csv',self.file()['version']);new=review.replace(self.store,p['id'],csv(row(3)),'nuevo.csv','QA',c['version'],c['token'])
        self.trash();self.trash(False);self.assertEqual(sii.preview(self.store,p['id'])['review']['replaced_by'],new['id'])
        self.assertEqual(sii.preview(self.store,new['id'])['name'],'nuevo.csv')
