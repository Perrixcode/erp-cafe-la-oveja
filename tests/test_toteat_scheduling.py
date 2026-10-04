from http_test_support import authenticated_opener
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from urllib.request import urlopen
from urllib.error import HTTPError

from app import make_server
from erp.catalog_import import load_catalog
from erp.store import Store
from erp.toteat_comments_probe import inspect_sales
from erp.toteat_inbox import Inbox, read_inbox
from erp.toteat_scheduling import parse_comment, settlement
from erp.toteat_receipts import download_receipt
from scripts.toteat_sales_worker import sales_cycle

COMMENT = 'Nombre y apellido: Cliente ficticio\nNúmero telefónico: 000000000\nFecha: lunes, 5 de octubre\nHorario: 18:00 hrs\nEstado: Pagado\nPlataforma: LOCAL'
SCOPE = {'restaurant_id':'DEMO','local_id':'1'}


class ParsingTests(unittest.TestCase):
    def test_afiche_fields_year_and_original(self):
        value=parse_comment(COMMENT,date(2026,10,4))
        self.assertEqual(value['customer'],'Cliente ficticio')
        self.assertEqual(value['customer_phone'],'000000000')
        self.assertEqual(value['pickup_at'],'2026-10-05T18:00')
        self.assertEqual(value['original'],COMMENT)
        self.assertTrue(value['year_inferred']);self.assertEqual(value['issues'],[])

    def test_unlabelled_name_missing_phone_and_explicit_past_date(self):
        original='Cliente ficticio\nFecha: 05/08/2026\nHorario: 17:00 hrs\nEstado: Pagado\nPlataforma: Local\nPrueba, no considerar'
        value=parse_comment(original,date(2026,10,4))
        self.assertEqual(value['pickup_at'],'2026-08-05T17:00')
        self.assertEqual(value['customer_phone'],'')
        self.assertIn('telefono_pendiente',value['warnings'])
        self.assertIn('fecha_pasada',value['warnings']);self.assertTrue(value['test_marker'])

    def test_ambiguous_invalid_and_missing_fields_require_review(self):
        for original in [COMMENT.replace('lunes','martes'),COMMENT.replace('18:00','25:00'),COMMENT+'\nFecha: 06/10/2026','Pagado',COMMENT.replace('5 de octubre','31/02/2026')]:
            self.assertTrue(parse_comment(original,date(2026,10,4))['issues'])

    def test_comment_payment_text_never_confirms_money(self):
        row={'dateClosed':'2026-10-04T18:00:00','total':100,'payed':0,'discounts':0,'difference':0,'paymentId':1,'comment':COMMENT}
        with self.assertRaises(ValueError):settlement(row)
        row.update(total=0,payed=0,discounts=-100)
        self.assertEqual(settlement(row)['state'],'discount_settled')
        row.update(total=100,payed=100)
        self.assertEqual(settlement(row)['state'],'paid')
        for patch in [{'fiscalType':'NC'},{'payed':-100},{'difference':10},{'total':True},{'total':'NaN'},{'dateClosed':''}]:
            with self.assertRaises(ValueError):settlement(dict(row,**patch))


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'data').mkdir();(self.root/'private').mkdir()
        self.path=self.root/'data/erp-demo.sqlite3';self.store=Store(self.path)
        catalog=load_catalog(Path(__file__).resolve().parents[1]/'fixtures/catalog_import_demo.json')
        catalog['source']='toteat-manual';self.store.import_catalog(catalog,'Fixture ficticio')
        with self.store.connect() as db:db.execute("INSERT INTO metadata VALUES('operating_mode','toteat-local')")
        self.product=self.store.catalog()[0]
        self.row={'orderId':9007199254740999,'paymentId':9007199254740111,'dateOpen':'2026-10-04T17:00:00',
                  'dateClosed':'2026-10-04T18:00:00','comment':COMMENT,'total':100,'payed':100,'discounts':-200,
                  'difference':0,'products':[{'id':self.product['source_product']['id'],'quantity':1,'lineId':1,'isExtra':False}],
                  'paymentForms':[]}
        self.candidate=self.project(self.row)
        self.inbox=Inbox(self.root/'private/toteat-reader.sqlite3')

    def project(self,row):
        _,candidates=inspect_sales({'ok':True,'data':[row]},[p['source_product'] for p in self.store.catalog()],str(row['orderId']))
        return candidates[0]

    def configure(self,automatic_shift=False):
        binary='private/oveja-toteat-automation' if automatic_shift else 'private/oveja-toteat-diagnostics'
        (self.root/binary).write_bytes(b'FAKE-NONEXECUTABLE')
        config={'version':1,'enabled':True,'binary':binary,'binary_sha256':hashlib.sha256(b'FAKE-NONEXECUTABLE').hexdigest(),
                'scope':SCOPE,'shift_start':'2026-10-02','start_after':'2026-10-04T17:30:00+00:00',
                'test_order_ids':[str(self.row['orderId'])],'automatic_shift_lookup':automatic_shift}
        (self.root/'private/toteat-sales-config.json').write_text(json.dumps(config))

    def test_schedule_discount_and_repeat_do_not_duplicate_or_mark_delivered(self):
        row=dict(self.row,total=0,payed=0,discounts=-300)
        candidate=self.project(row)
        first=self.store.import_toteat_schedule(SCOPE,row,candidate,is_test=True)
        again=self.store.import_toteat_schedule(SCOPE,row,candidate,is_test=True)
        self.assertEqual(first['id'],again['id']);self.assertEqual(first['version'],again['version'])
        self.assertEqual(first['status_label'],'Agendado');self.assertFalse(first['payment_confirmed'])
        self.assertEqual(first['toteat_schedule']['payment']['state'],'discount_settled')
        self.assertEqual(first['items'][0]['status'],'pendiente');self.assertTrue(first['is_simulation'])
        self.assertEqual(len(self.store.history(first['id'])),1)
        self.assertEqual(first['comments'],COMMENT);self.assertEqual(first['customer_phone'],'000000000')
        self.assertEqual(self.store.stock(),[])

    def test_closed_sale_moves_from_inbox_to_october_agenda_without_losing_source(self):
        import threading
        from urllib.request import urlopen
        from app import make_server
        inbox = Inbox(self.path.parent/'toteat-reader.sqlite3')
        row = dict(self.row, comment=COMMENT.replace('lunes, 5 de octubre','30/10/2026'))
        candidate = self.project(row)
        key = inbox.receive_sale(SCOPE,candidate,self.store.catalog())
        server = make_server(self.path,port=0,seed=False)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=authenticated_opener(server,self.path.parent)
        def fetch(path):
            with opener.open('http://127.0.0.1:'+str(server.server_address[1])+path,timeout=3) as response:
                return json.load(response)
        try:
            self.assertEqual(fetch('/api/toteat')['pending_orders'],1)
            self.assertEqual(fetch('/api/board?date=2026-10-01&period=month&scope=tests')['orders'],[])
            order = self.store.import_toteat_schedule(SCOPE,row,candidate,is_test=True)
            inbox.set_sales_state(key,{'status':'scheduled','order_id':order['id']})
            for _ in range(2):
                self.store.import_toteat_schedule(SCOPE,row,candidate,is_test=True)
                view = fetch('/api/toteat')
                self.assertEqual(view['orders'],[]);self.assertEqual(view['pending_orders'],0)
                self.assertEqual(view['scheduled_orders'],1)
                operations = fetch('/api/board?date=2026-10-01&period=month')
                self.assertEqual(operations['orders'],[]);self.assertEqual(operations['test_order_count'],1)
                agenda = fetch('/api/board?date=2026-10-01&period=month&scope=tests')
                self.assertEqual(len(agenda['orders']),1)
                self.assertEqual(agenda['orders'][0]['pickup_at'],'2026-10-30T18:00')
                self.assertEqual(agenda['orders'][0]['comments'],row['comment'])
            self.assertEqual(len(read_inbox(inbox.path)['orders']),1)
            self.assertEqual(Store(self.path).get(order['id'])['status_label'],'Agendado')
            self.assertEqual(len(self.store.history(order['id'])),1)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_paid_real_order_and_updates_preserve_audit_and_identity(self):
        first=self.store.import_toteat_schedule(SCOPE,self.row,self.candidate)
        self.assertFalse(first['is_demo']);self.assertFalse(first['is_simulation']);self.assertTrue(first['payment_confirmed'])
        changed=dict(self.row,comment=COMMENT.replace('18:00','19:00'))
        updated=self.store.import_toteat_schedule(SCOPE,changed,self.project(changed))
        self.assertEqual(updated['id'],first['id']);self.assertEqual(updated['pickup_at'],'2026-10-05T19:00')
        self.assertEqual(len(self.store.history(first['id'])),2)
        self.store.transition(updated['id'],updated['items'][0]['id'],'marcado_solicitado','QA','Prueba',updated['version'])
        with self.assertRaises(ValueError):self.store.import_toteat_schedule(SCOPE,self.row,self.candidate)

    def test_missing_data_or_unsettled_sale_never_creates_order(self):
        for patch in [{'comment':'Sin datos'},{'payed':0},{'fiscalType':'NC'},{'dateClosed':''}]:
            row=dict(self.row,**patch)
            with self.assertRaises(ValueError):self.store.import_toteat_schedule(SCOPE,row,self.project(row))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)

    def test_worker_catches_closed_order_not_seen_open_and_is_idempotent(self):
        self.configure(automatic_shift=True);calls=[]
        def reader(binary,args,current):
            calls.append(args)
            if args==['shift-status']:
                return {'ok':True,'data':{'restaurantId':'DEMO','localNumber':'1','date':'2026-10-02T09:00:00','status':'open'}}
            self.assertEqual(args,['sales-one-day','20261002'])
            return {'ok':True,'data':[self.row]}
        sales_cycle(self.inbox,self.root,reader,current_day=date(2026,10,2));sales_cycle(self.inbox,self.root,reader,current_day=date(2026,10,2))
        view=read_inbox(self.inbox.path)
        self.assertEqual(view['stored_orders'],1);self.assertTrue(view['automatic_scheduling'])
        self.assertEqual(view['orders'][0]['scheduling']['status'],'scheduled')
        self.assertEqual(view['orders'][0]['comments'][0]['text'],COMMENT)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0],1)
        self.assertEqual(len(calls),4)

    def test_response_timestamp_does_not_replace_verified_sales_day_and_new_day_is_discovered(self):
        self.configure(automatic_shift=True);calls=[];new_shift=False
        def reader(binary,args,current):
            calls.append(args)
            if args==['shift-status']:
                return {'ok':True,'data':{'restaurantId':'DEMO','localNumber':'1','date':'2026-10-04T19:51:44','status':'open'}}
            return {'ok':True,'data':[self.row] if args[1]=='20261002' or (new_shift and args[1]=='20261005') else []}
        sales_cycle(self.inbox,self.root,reader,current_day=date(2026,10,4))
        self.assertEqual(calls,[['shift-status'],['sales-one-day','20261002'],['sales-one-day','20261003'],['sales-one-day','20261004']])
        self.assertEqual(read_inbox(self.inbox.path)['sales_reader']['verified_sales_day'],'2026-10-02')
        calls.clear();sales_cycle(self.inbox,self.root,reader,current_day=date(2026,10,4))
        self.assertIn(['sales-one-day','20261002'],calls)
        self.assertEqual(read_inbox(self.inbox.path)['sales_reader']['transactions_returned'],1)
        new_shift=True;calls.clear();sales_cycle(self.inbox,self.root,reader,current_day=date(2026,10,5))
        self.assertEqual(read_inbox(self.inbox.path)['sales_reader']['verified_sales_day'],'2026-10-05')
        calls.clear();sales_cycle(self.inbox,self.root,reader,current_day=date(2026,10,5))
        self.assertEqual(calls,[['shift-status'],['sales-one-day','20261004'],['sales-one-day','20261005']])
        def wrong_local(binary,args,current):
            self.assertEqual(args,['shift-status'])
            return {'ok':True,'data':{'restaurantId':'DEMO','localNumber':'2','date':'2026-10-05T09:00:00'}}
        with self.assertRaisesRegex(ValueError,'shift_scope_mismatch'):
            sales_cycle(self.inbox,self.root,wrong_local)

    def test_worker_excludes_unrequested_history_and_reviews_invalid_comment(self):
        self.configure();older=dict(self.row,orderId=9007199254740001,dateClosed='2026-10-03T18:00:00')
        current=dict(self.row,comment='Comentario ficticio incompleto')
        sales_cycle(self.inbox,self.root,lambda *args:{'ok':True,'data':[older,current]})
        view=read_inbox(self.inbox.path)
        self.assertEqual(view['stored_orders'],1)
        self.assertEqual(view['orders'][0]['scheduling']['status'],'needs_review')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)

    def test_sale_without_comment_does_not_block_following_valid_order(self):
        self.configure()
        missing=dict(self.row,orderId=9007199254740002,paymentId=9007199254740003,comment='')
        sales_cycle(self.inbox,self.root,lambda *args:{'ok':True,'data':[missing,self.row]})
        view=read_inbox(self.inbox.path)
        self.assertEqual(view['sales_reader']['state'],'receiving')
        statuses={o['order_id']:o['scheduling']['status'] for o in view['orders']}
        self.assertEqual(statuses[str(missing['orderId'])],'needs_review')
        self.assertEqual(statuses[str(self.row['orderId'])],'scheduled')
        self.assertEqual(view['sales_reader']['reviewed'],1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)

    def test_worker_pdf_failure_does_not_block_schedule(self):
        self.configure();row=dict(self.row,fiscalId=1)
        def reader(binary,args,current):
            if args[0]=='sales-one-day':return {'ok':True,'data':[row]}
            return {'ok':True,'data':{'orderId':row['orderId'],'document':{'payments':[{'id':row['paymentId'],'amount':100,'amountPaid':100,'urlDTE':'https://toteatdte.appspot.com/ficticio'}]}}}
        def unavailable(*args):raise OSError('fictitious unavailable PDF')
        sales_cycle(self.inbox,self.root,reader,unavailable)
        view=read_inbox(self.inbox.path)['orders'][0]['scheduling']
        self.assertEqual(view['status'],'scheduled');self.assertEqual(view['receipt']['status'],'pending')

    def test_zero_fiscal_id_does_not_request_a_nonexistent_receipt(self):
        self.configure();row=dict(self.row,total=0,payed=0,discounts=-300,fiscalId=0)
        calls=[]
        def reader(binary,args,current):
            calls.append(args)
            self.assertEqual(args[0],'sales-one-day')
            return {'ok':True,'data':[row]}
        sales_cycle(self.inbox,self.root,reader)
        self.assertEqual(len(calls),1)
        self.assertEqual(read_inbox(self.inbox.path)['orders'][0]['scheduling']['payment_state'],'discount_settled')

    def test_pdf_api_integrity_download_and_no_document_requirement(self):
        # Base aislada usa receipts junto a la base según handler_for.
        pdf=b'%PDF-1.4\n% Archivo ficticio para prueba de transporte\n%%EOF'
        digest=hashlib.sha256(pdf).hexdigest();directory=self.path.parent/'receipts';directory.mkdir()
        (directory/(digest+'.pdf')).write_bytes(pdf)
        receipt={'filename':digest+'.pdf','sha256':digest,'bytes':len(pdf)}
        order=self.store.import_toteat_schedule(SCOPE,self.row,self.candidate,receipt=receipt)
        self.store.import_toteat_schedule(SCOPE,self.row,self.candidate,receipt=receipt)
        self.assertEqual(sum(h['action']=='boleta_adjunta' for h in self.store.history(order['id'])),1)
        server=make_server(self.path,port=0,seed=False);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=authenticated_opener(server,self.path.parent)
        try:
            url='http://127.0.0.1:'+str(server.server_address[1])+f'/api/orders/{order["id"]}'
            with opener.open(url) as response:self.assertEqual(json.load(response)['status_label'],'Agendado')
            with opener.open(url+'/receipt?download=1') as response:
                self.assertEqual(response.read(),pdf);self.assertTrue(response.headers['Content-Disposition'].startswith('attachment'))
            (directory/(digest+'.pdf')).write_bytes(pdf+b'tampered')
            with self.assertRaises(HTTPError) as error:opener.open(url+'/receipt')
            self.assertEqual(error.exception.code,409);error.exception.close()
        finally:server.shutdown();server.server_close();thread.join()


class ReceiptDownloadTests(unittest.TestCase):
    def test_unverified_hosts_and_html_are_rejected_before_attachment(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'<html>not a PDF</html>'
        class Opener:
            called=0
            def open(self,*args,**kwargs):self.called+=1;return Response()
        with tempfile.TemporaryDirectory() as directory:
            opener=Opener()
            for url in ['http://toteatdte.appspot.com/x','https://127.0.0.1/x','https://example.com/x','https://user@toteatdte.appspot.com/x']:
                with self.assertRaises(ValueError):download_receipt(url,directory,opener)
            self.assertEqual(opener.called,0)
            with self.assertRaises(ValueError):download_receipt('https://toteatdte.appspot.com/ficticio',directory,opener)
            self.assertEqual(list(Path(directory).iterdir()),[])

class MigrationTests(unittest.TestCase):
    def test_legacy_constraints_migrate_without_losing_orders_items_or_history(self):
        schema=Path(__file__).resolve().parents[1].joinpath('erp/schema.sql').read_text()
        legacy=schema.replace("('demo', 'demo-toteat', 'toteat', 'manual-sos')","('demo', 'demo-toteat')").replace('CHECK(payment_confirmed IN (0,1))','CHECK(payment_confirmed = 1)').replace('CHECK(is_demo IN (0,1))','CHECK(is_demo = 1)')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'legacy.sqlite3'
            db=sqlite3.connect(path)
            try:
                db.executescript(legacy)
                db.execute("INSERT INTO orders(id,source,source_id,channel,customer,pickup_at,fulfillment,payment_confirmed,created_at,updated_at) VALUES(7,'demo','DEMO-MIGRATION','Presencial','Cliente ficticio','2026-10-05T18:00','retiro',1,'2026-10-04','2026-10-04')")
                db.execute("INSERT INTO items(id,order_id,source_item_id,flavor,size,sku,quantity,kind,status) VALUES(8,7,'1','Ficticia','Entera','DEMO-FICTICIA',1,'torta','marcado')")
                db.execute("INSERT INTO history(order_id,item_id,action,actor,occurred_at,reason,after_json) VALUES(7,8,'estado','QA','2026-10-04','Fixture','{}')")
                db.execute("INSERT INTO order_scheduling VALUES(7,'scheduled','000000000')")
                db.execute("INSERT INTO documents(order_id,source,status) VALUES(7,'toteat','pending')")
                db.commit()
                names=['orders','items','history','order_scheduling','documents']
                before={name:db.execute('SELECT * FROM '+name).fetchall() for name in names}
            finally:db.close()
            store=Store(path);Store(path)
            with store.connect() as db:
                after={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in names}
                self.assertEqual(before,after)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(store.get(7)['items'][0]['status'],'marcado')


if __name__=='__main__':unittest.main()
