from http_test_support import authenticated_opener
import json
import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from erp.toteat_inbox import Inbox,normalize,read_inbox
from scripts.toteat_worker import cycle,next_delay,cooldown_seconds
from datetime import datetime,timezone
import threading
from urllib.request import urlopen
from app import make_server

SCOPE={'restaurant_id':'DEMO','local_id':'1'}
def payload(channel='pos',comment=True):
    line={'lineNumber':1,'productCodeToteat':9001,'productName':'Torta ficticia','quantity':1,'isExtra':False}
    if comment:line['comment']='Nombre: Cliente ficticio\nFecha: lunes 5 de octubre\nHora: 18:00\nPlataforma: LOCAL'
    return {'ok':True,'data':[{'orderId':9007199254740999,'restaurantId':'DEMO','localNumber':'1','channel':channel,
                             'vendorName':'Proveedor ficticio','orderReference':'DEMO-EXT','document':{'line':[line]}}]}

class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'reader.sqlite3';self.inbox=Inbox(self.path)
    def test_all_channels_and_exact_int64_identity(self):
        for channel in ('pos','webstore','app','mercat','instagram'):
            result=normalize(payload(channel),SCOPE,{9001})[0]
            self.assertEqual(result['channel'],channel);self.assertEqual(result['order_id'],'9007199254740999')
            self.assertEqual(result['scheduling_status'],'needs_review')
    def test_idempotency_and_channel_change_preserve_single_identity(self):
        first=self.inbox.apply(payload(),SCOPE,{9001});self.assertEqual(first['created'],1)
        again=self.inbox.apply(payload(),SCOPE,{9001});self.assertEqual(again['created']+again['changed'],0)
        changed=self.inbox.apply(payload('webstore'),SCOPE,{9001});self.assertEqual(changed['changed'],1)
        view=read_inbox(self.path);self.assertEqual(view['stored_orders'],1);self.assertEqual(view['orders'][0]['version'],2)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM received_history').fetchone()[0],2)
    def test_unrelated_products_and_payment_comments_not_scheduling(self):
        self.assertEqual(normalize(payload(),SCOPE,{9002}),[])
        data=payload(comment=False);data['data'][0]['document']['payments']=[{'paymentForms':[{'comment':'Fecha: 5 de octubre'}]}]
        row=normalize(data,SCOPE,{9001})[0]
        self.assertEqual(row['comments'],[]);self.assertEqual(row['items'][0]['comments'],[])
        self.assertEqual(row['scheduling_status'],'needs_review')
    def test_mixed_order_keeps_exact_cake_and_sweet_ids_only(self):
        data=payload();cake=data['data'][0]['document']['line'][0]
        sweet=dict(cake,lineNumber=2,productCodeToteat=9004,productName='Dulce ficticio entero',comment='Comentario ficticio del dulce')
        unrelated=dict(cake,lineNumber=3,productCodeToteat=9999,productName=cake['productName'],comment='NO_RETENER_PRODUCTO_AJENO')
        extra=dict(cake,lineNumber=4,isExtra=True,comment='NO_RETENER_MODIFICADOR')
        data['data'][0]['document']['line']=[cake,sweet,unrelated,extra]
        self.inbox.apply(data,SCOPE,{9001,9004})
        result=read_inbox(self.path)
        self.assertEqual([i['productCodeToteat'] for i in result['orders'][0]['items']],[9001,9004])
        self.assertEqual(result['evidence']['matching_product_lines'],2)
        self.assertNotIn('NO_RETENER',json.dumps(result))
        self.inbox.apply(data,SCOPE,{9001,9004})
        self.assertEqual(read_inbox(self.path)['stored_orders'],1)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM received_history').fetchone()[0],1)
    def test_missing_extra_flag_and_string_ids_are_not_silently_classified(self):
        data=payload();del data['data'][0]['document']['line'][0]['isExtra']
        self.assertEqual(normalize(data,SCOPE,{9001}),[])
        data=payload();data['data'][0]['document']['line'][0]['productCodeToteat']='9001'
        self.assertEqual(normalize(data,SCOPE,{9001}),[])
    def test_scope_mismatch_invalid_flags_and_duplicate_leave_database_unchanged(self):
        self.inbox.apply(payload(),SCOPE,{9001});before=read_inbox(self.path)['orders']
        bad=payload();bad['data'][0]['localNumber']='2'
        duplicate=payload();duplicate['data']*=2
        for data in (bad,duplicate,{'ok':False,'data':[]}):
            with self.assertRaises(ValueError):self.inbox.apply(data,SCOPE,{9001})
            self.assertEqual(read_inbox(self.path)['orders'],before)
    def test_disappearing_order_is_not_deleted_or_assumed_delivered(self):
        self.inbox.apply(payload(),SCOPE,{9001})
        self.inbox.apply({'ok':True,'data':[]},SCOPE,{9001})
        data=read_inbox(self.path);self.assertEqual(data['stored_orders'],1)
        self.assertEqual(data['orders'][0]['scheduling_status'],'needs_review')
    def test_original_multiline_comment_and_safe_evidence(self):
        self.inbox.apply(payload(),SCOPE,{9001});view=read_inbox(self.path)
        self.assertEqual(view['orders'][0]['items'][0]['comments'][0]['text'],payload()['data'][0]['document']['line'][0]['comment'])
        report=read_inbox(self.path,False)
        self.assertNotIn('Cliente ficticio',json.dumps(report));self.assertNotIn('18:00',json.dumps(report))
        self.assertFalse(report['automatic_scheduling']);self.assertFalse(report['closed_orders_coverage'])
        data=payload();data['data'][0]['document']['line'][0]['comment']='Ficticio\n'*700
        self.inbox.apply(data,SCOPE,{9001})
        self.assertEqual(read_inbox(self.path)['orders'][0]['items'][0]['comments'][0]['text'],data['data'][0]['document']['line'][0]['comment'])
    def test_worker_single_get_proxy_and_auth_stop(self):
        called=[]
        def fetch():called.append(True);return {'ok':True,'scope':SCOPE,'payload':payload()}
        self.assertEqual(cycle(self.inbox,fetch,{9001}),(False,0));self.assertEqual(len(called),1)
        self.assertEqual(cycle(self.inbox,lambda:{'ok':False,'http_status':401,'error':'http_error'},{9001}),(True,0))
        self.assertEqual(read_inbox(self.path)['stored_orders'],1)
        self.assertEqual(read_inbox(self.path)['state'],'access_required')
    def test_throttle_backoff_and_retry_after(self):
        self.assertEqual(next_delay(1),60);self.assertEqual(next_delay(10),300);self.assertEqual(next_delay(1,600),600)
    def test_rate_limit_pauses_all_reader_stages_until_provider_window(self):
        now=datetime(2026,10,5,0,0,tzinfo=timezone.utc)
        self.assertEqual(cooldown_seconds({},now),0)
        limited={'http_status':429,'retry_at':'2026-10-05T00:05:00+00:00'}
        self.assertEqual(cooldown_seconds({'sales_reader':limited},now),300)
        self.assertEqual(cooldown_seconds({'cancellation_reader':limited},now),300)
        self.assertEqual(cooldown_seconds({'sales_reader':limited},datetime(2026,10,5,0,6,tzinfo=timezone.utc)),0)
        cycle(self.inbox,lambda:{'ok':False,'http_status':429,'error':'http_error','retry_after':600},{9001})
        self.assertGreater(cooldown_seconds(read_inbox(self.path,False)),590)

    def test_reader_never_touches_operational_tables(self):
        self.inbox.apply(payload(),SCOPE,{9001})
        with closing(sqlite3.connect(self.path)) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables,{'received_orders','received_history','reader_status','received_sales_comments','received_sales_state','received_state_history'})
    def test_http_inbox_isolated_from_main_instance_and_operational_orders(self):
        path=Path(self.temp.name)/'toteat-reader.sqlite3'
        Inbox(path).apply(payload(),SCOPE,{9001})
        server=make_server(Path(self.temp.name)/'orders.sqlite3',port=0,seed=False)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=authenticated_opener(server,Path(self.temp.name))
        try:
            url='http://127.0.0.1:'+str(server.server_address[1])
            with opener.open(url+'/api/toteat') as response:reader=json.load(response)
            self.assertEqual(reader['stored_orders'],1);self.assertTrue(reader['connected'])
            with opener.open(url+'/api/board') as response:board=json.load(response)
            self.assertEqual(board['orders'],[]);self.assertEqual(board['stock']['rows'],[])
            self.assertEqual(board['toteat']['stored_orders'],1)
            with opener.open(url+'/api/health') as response:health=json.load(response)
            self.assertTrue(health['authentication_required']);self.assertNotIn('toteat_connected',health)
        finally:server.shutdown();server.server_close();thread.join()

class SalesCommentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'toteat-reader.sqlite3'
        self.inbox=Inbox(self.path)
        self.inbox.apply(payload(comment=False),SCOPE,{9001})
        self.original='Nombre: Cliente ficticio\nTeléfono: 000000000\nFecha: 5 de octubre\nHora: 18:00\n<img src=x onerror=alert(1)>\n' * 30
        self.sale={'order_id':'9007199254740999','payment_id':'9007199254740111',
                   'date_closed':'2026-10-04T15:33:00',
                   'order_comments':[{'path':'$.comment','original':self.original}],
                   'products':[{'catalog_id':'9001'}]}

    def test_exact_original_identity_idempotency_and_no_scheduling(self):
        self.assertEqual(self.inbox.attach_sales_comments(SCOPE,self.sale)['changed'],1)
        self.assertEqual(self.inbox.attach_sales_comments(SCOPE,self.sale)['changed'],0)
        view=read_inbox(self.path);order=view['orders'][0]
        self.assertEqual(order['comments'],[{'path':'$.comment','text':self.original,'source':'sales'}])
        self.assertEqual(order['scheduling_status'],'needs_review')
        self.assertEqual(order['version'],1)
        self.assertNotIn('Cliente ficticio',json.dumps(read_inbox(self.path,False)))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM received_orders').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM received_history').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM received_sales_comments').fetchone()[0],1)

    def test_open_order_refresh_and_empty_listing_preserve_sale_comment(self):
        self.inbox.attach_sales_comments(SCOPE,self.sale)
        self.inbox.apply({'ok':True,'data':[]},SCOPE,{9001})
        self.inbox.apply(payload('webstore',comment=False),SCOPE,{9001})
        order=read_inbox(self.path)['orders'][0]
        self.assertEqual(order['comments'][0]['text'],self.original)
        self.assertEqual(order['channel'],'webstore')
        self.assertEqual(order['scheduling_status'],'needs_review')

    def test_comment_changes_and_reversions_append_history(self):
        self.inbox.attach_sales_comments(SCOPE,self.sale)
        revised=copy.deepcopy(self.sale);revised['order_comments'][0]['original']='Texto ficticio corregido'
        self.inbox.attach_sales_comments(SCOPE,revised)
        self.assertEqual(read_inbox(self.path)['orders'][0]['comments'][0]['text'],'Texto ficticio corregido')
        self.inbox.attach_sales_comments(SCOPE,self.sale)
        self.assertEqual(read_inbox(self.path)['orders'][0]['comments'][0]['text'],self.original)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM received_sales_comments').fetchone()[0],3)

    def test_unknown_order_wrong_scope_product_and_payment_comment_are_rejected(self):
        variants=[]
        for key,value in [('order_id','111'),('date_closed',''),('products',[{'catalog_id':'9002'}]),
                          ('order_comments',[{'path':'$.paymentForms[0].comment','original':'No es comentario de orden'}]),
                          ('order_comments',[{'path':'$.comment','original':{'text':'Formato no verificado'}}])]:
            sale=copy.deepcopy(self.sale);sale[key]=value;variants.append((SCOPE,sale))
        variants.append((dict(SCOPE,local_id='2'),self.sale))
        for scope,sale in variants:
            with self.assertRaises(ValueError):self.inbox.attach_sales_comments(scope,sale)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM received_sales_comments').fetchone()[0],0)
        self.assertEqual(read_inbox(self.path)['orders'][0]['comments'],[])

    def test_http_exposes_comment_without_creating_operational_order(self):
        self.inbox.attach_sales_comments(SCOPE,self.sale)
        server=make_server(Path(self.temp.name)/'orders.sqlite3',port=0,seed=False)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=authenticated_opener(server,Path(self.temp.name))
        try:
            url='http://127.0.0.1:'+str(server.server_address[1])
            with opener.open(url+'/api/toteat') as response:reader=json.load(response)
            self.assertEqual(reader['orders'][0]['comments'][0]['text'],self.original)
            with opener.open(url+'/api/board') as response:board=json.load(response)
            self.assertEqual(board['orders'],[])
            self.assertEqual(board['toteat']['orders'][0]['comments'][0]['text'],self.original)
            with opener.open(url+'/api/health') as response:health=json.load(response)
            self.assertNotIn('automatic_scheduling',health)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
