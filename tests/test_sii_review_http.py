"""Review endpoints keep partner/origin checks and support large monthly drafts."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app import make_server
from erp import sii_purchases as sii
from erp.store import Store
from http_test_support import authenticated_opener
from test_sii_review import csv,row

class SiiReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.server=make_server(self.root/'erp.sqlite3',0,seed=False);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.partner=authenticated_opener(self.server,self.root)
        self.store=Store(self.root/'erp.sqlite3');self.p=sii.upload(self.store,csv(*[row(n,n+100) for n in range(1,151)]),'qa.csv','QA')
        self.base='/api/suppliers/imports/'+self.p['id']
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,path,data,**headers):
        with self.partner.open(Request(self.url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1',**headers})) as response:return json.load(response)
    def test_large_review_and_protected_new_routes(self):
        draft=self.p['review']['draft'];draft['selected']=list(range(2,152));draft['details']={str(n):dict(note='Nota QA '+('x'*600),follow_up='',discard_reason='') for n in range(2,152)}
        data=dict(version=self.p['review']['version'],draft=draft);self.assertGreater(len(json.dumps(data)),65536)
        self.post(self.base+'/review',data)
        self.assertEqual(len(sii.preview(self.store,self.p['id'])['review']['draft']['selected']),150)
        cashier=authenticated_opener(self.server,self.root,'cashier','caja-qa')
        for opener,code in ((None,401),(cashier,403)):
            for path,body in (('/api/suppliers/imports',None),(self.base+'/review',json.dumps(data).encode()),(self.base+'/closed',b'{}'),(self.base+'/trashed',b'{}'),(self.base+'/replacement-preview?version=1',csv(row(200)))):
                with self.subTest(path=path),self.assertRaises(HTTPError) as err:(opener.open if opener else urlopen)(Request(self.url+path,data=body,headers={'Content-Type':'application/json','X-ERP-Local':'1'}))
                self.assertEqual(err.exception.code,code);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.post(self.base+'/review',data,Origin='https://foreign.invalid')
        self.assertEqual(err.exception.code,403);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.post('/api/suppliers/providers',{'unused':'x'*70000})
        self.assertEqual(err.exception.code,413);err.exception.close()
    def test_compare_and_replace_require_confirmation(self):
        raw=csv(row(1),row(200));headers={'X-ERP-Local':'1','X-File-Name':'nuevo.csv'}
        with self.partner.open(Request(self.url+self.base+'/replacement-preview?version=1',data=raw,headers=headers)) as response:c=json.load(response)
        suffix=f'/replace?version=1&token={c["token"]}'
        with self.assertRaises(HTTPError) as err:self.partner.open(Request(self.url+self.base+suffix,data=raw,headers=headers))
        self.assertEqual(err.exception.code,400);err.exception.close()
        with self.partner.open(Request(self.url+self.base+suffix+'&confirmed=1',data=raw,headers=headers)) as response:result=json.load(response)
        self.assertEqual(result['name'],'nuevo.csv')

    def test_trash_requires_confirmation_origin_and_restores(self):
        data=dict(version=1,trashed=True,confirmed=True)
        for extra,code in ((dict(Origin='https://foreign.invalid'),403),):
            with self.assertRaises(HTTPError) as err:self.post(self.base+'/trashed',data,**extra)
            self.assertEqual(err.exception.code,code);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.post(self.base+'/trashed',dict(data,confirmed=False))
        self.assertEqual(err.exception.code,400);err.exception.close()
        result=self.post(self.base+'/trashed',data);self.assertTrue(result['trashed'])
        with self.assertRaises(HTTPError) as err:self.partner.open(self.url+self.base)
        self.assertEqual(err.exception.code,409);err.exception.close()
        self.assertFalse(self.post(self.base+'/trashed',dict(data,trashed=False,version=result['version']))['trashed'])
        with self.partner.open(self.url+self.base) as response:self.assertEqual(json.load(response)['name'],'qa.csv')
