from contextlib import closing
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from erp.backups import create_backup,verify_backup,restore_backup,BackupRunner
from erp.attention import attention_summary
from erp.partner_auth import PartnerAuth
from erp.store import Store
from test_erp import sample


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'installation';(self.root/'data').mkdir(parents=True)
        self.store=Store(self.root/'data/erp-demo.sqlite3')
        self.order=self.store.create(sample(),'Fixture ficticio')
        self.auth=PartnerAuth(self.root/'private/partner-auth.sqlite3');self.auth.create('qa','Socio ficticio','Clave-ficticia-de-respaldo')
        pdf=b'%PDF-1.4\n% BOLETA FICTICIA\n%%EOF';self.sha=hashlib.sha256(pdf).hexdigest()
        self.pdf=self.root/'private/receipts'/(self.sha+'.pdf');self.pdf.parent.mkdir();self.pdf.write_bytes(pdf)
        with self.store.connect() as db:
            db.execute('INSERT INTO document_attachments VALUES(?,?,?,?,?,?)',(self.order['id'],self.sha+'.pdf',self.sha,len(pdf),'DEMO-PAY','2026-10-04T00:00:00Z'))
        self.order=self.store.get(self.order['id'])
        (self.root/'private/token-no-incluir.txt').write_text('NO_ES_UN_TOKEN_REAL')

    def test_online_snapshot_restores_order_history_auth_and_pdf_without_overwrite(self):
        backup=create_backup(self.root);manifest=verify_backup(backup)
        self.assertFalse(manifest['toteat_credentials_included'])
        self.assertTrue(manifest['account_password_hashes_included'])
        self.assertNotIn('private/token-no-incluir.txt',manifest['files'])
        self.store.transition(self.order['id'],self.order['items'][0]['id'],'marcado_solicitado','QA','Cambio posterior',self.order['version'])
        target=Path(self.tmp.name)/'recovered';restore_backup(backup,target)
        restored=Store(target/'data/erp-demo.sqlite3')
        self.assertEqual(restored.get(self.order['id']),self.order)
        self.assertEqual(len(restored.history(self.order['id'])),1)
        self.assertEqual((target/'private/receipts'/(self.sha+'.pdf')).read_bytes(),self.pdf.read_bytes())
        auth=PartnerAuth(target/'private/partner-auth.sqlite3');self.assertTrue(auth.login('qa','Clave-ficticia-de-respaldo'))
        with self.assertRaises(ValueError):restore_backup(backup,target)
        self.assertFalse((target/'private/toteat-reader-config.json').exists())

    def test_missing_pdf_corruption_and_traversal_fail_closed(self):
        backup=create_backup(self.root)
        file=backup/'private/receipts'/(self.sha+'.pdf');file.write_bytes(b'corrupted')
        with self.assertRaises(ValueError):verify_backup(backup)
        self.pdf.unlink()
        with self.assertRaises(ValueError):create_backup(self.root)
        self.assertEqual(list((self.root/'private/backups').glob('.incomplete-*')),[])
        manifest=json.loads((backup/'manifest.json').read_text())
        manifest['files']={'data/erp-demo.sqlite3':manifest['files']['data/erp-demo.sqlite3'],'../../outside':{'bytes':0,'sha256':'0'*64}}
        (backup/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):restore_backup(backup,Path(self.tmp.name)/'bad-restore')
        self.assertFalse((Path(self.tmp.name)/'bad-restore').exists())

    def test_automatic_backup_once_per_day_and_failure_visible(self):
        runner=BackupRunner(self.root);runner.tick();first=runner.status();self.assertEqual(first['state'],'ready')
        runner.tick();self.assertEqual(runner.status(),first)
        self.assertEqual(len(list((self.root/'private/backups').glob('oveja-*'))),1)
        status=dict(first,last_success='2020-01-01T00:00:00+00:00');runner.status_path.write_text(json.dumps(status))
        with patch('erp.backups.create_backup',side_effect=OSError('private message must not leak')):runner.tick()
        self.assertEqual(runner.status()['state'],'error');self.assertNotIn('private message',runner.status_path.read_text())


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'orders.sqlite3')

    def create(self,identifier,day,phone='000000000',immediate=False):
        data=sample();data.update(source_id='DEMO-'+identifier,pickup_at=day+'T18:00',customer_phone=phone)
        if immediate:data['delivery_timing']='immediate'
        return self.store.create(data,'QA',simulation=True)

    def test_test_scope_does_not_leak_to_operations_and_finished_orders_do_not_count(self):
        self.create('upcoming','2026-10-05');old=self.create('overdue','2026-10-01');self.create('immediate','2026-10-04',immediate=True)
        with self.store.connect() as db:db.execute("UPDATE order_scheduling SET customer_phone='' WHERE order_id=?",(old['id'],))
        current=date(2026,10,4)
        self.assertEqual(attention_summary(self.store,{},'operations',current)['orders'],[])
        result=attention_summary(self.store,{},'tests',current)
        self.assertEqual(result['counts'],{'upcoming':1,'overdue':1,'unmarked':2,'unpaid':0,'incomplete':1})
        self.assertEqual(result['orders'][0]['id'],old['id'])
        self.store.transition(old['id'],old['items'][0]['id'],'cancelado','QA','Prueba',old['version'])
        self.assertEqual(attention_summary(self.store,{},'tests',current)['counts']['overdue'],0)
        self.assertEqual(attention_summary(self.store,{},'archived',current)['orders'],[])

    def test_reader_staleness_considers_open_and_closed_sales_independently(self):
        with self.store.connect() as db:db.execute("INSERT INTO metadata VALUES('operating_mode','toteat-local')")
        now=datetime(2026,10,4,20,0,tzinfo=timezone.utc)
        reader={'state':'receiving','last_success':'2026-10-04T19:59:30+00:00','sales_reader':{'state':'receiving','last_success':'2026-10-04T19:50:00+00:00'}}
        result=attention_summary(self.store,reader,'operations',date(2026,10,4),now)
        self.assertEqual(len(result['reader_alerts']),1);self.assertIn('ventas cerradas',result['reader_alerts'][0])
        reader['sales_reader']['last_success']='2026-10-04T19:59:00+00:00'
        self.assertEqual(attention_summary(self.store,reader,'operations',date(2026,10,4),now)['reader_alerts'],[])
        reader['last_success']=None
        self.assertEqual(len(attention_summary(self.store,reader,'operations',date(2026,10,4),now)['reader_alerts']),1)
