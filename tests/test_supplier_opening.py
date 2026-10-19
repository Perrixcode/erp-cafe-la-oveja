"""Synthetic opening evidence: preserve unknowns, audit conditions and enforce access."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_server
from erp import supplier_opening as opening
from erp.domain import DomainError
from erp.store import Store
from http_test_support import authenticated_opener


def fixture():
    def row(n, name, folio, amount, paid, balance):
        return dict(row=n, name=name, folio=folio, amount=amount, paid=paid, balance=balance, due=None, status='Pagado' if balance is not None and balance <= 0 else 'Pendiente')
    return {'file': {'id': 'synthetic_sheet_0001', 'title': 'Planilla ficticia QA', 'modified_time': '2026-10-01T12:00:00Z'},
            'ledger': [row(4, 'Proveedor A', 1001, 10000, 2000, 8000), row(5, 'Proveedor B', 1002, 5000, 5200, -200),
                       row(6, 'Proveedor C', 1003, 1000, 1000, 0), row(7, 'Proveedor C', 1003, 1000, 1000, 0),
                       row(8, 'Proveedor D', 1004, 3000, 3000, None), row(9, 'Proveedor A', None, 7000, None, 7000),
                       row(10, 'Proveedor E', 0, 1500, 0, 1500), row(11, 'Proveedor E', 0, 1700, 0, 1700),
                       row(12, 'Comercial Proveedor A', 1005, 1000, 0, 1000)],
            'form': [dict(row=2, name='Proveedor A', folio=1001, amount=11000),
                     dict(row=3, name='Proveedor C', folio=1003, amount=1000), dict(row=4, name='Proveedor C', folio=1003, amount=1000),
                     dict(row=5, name='Proveedor E', folio=0, amount=1500), dict(row=6, name='Proveedor E', folio=0, amount=1700),
                     dict(row=7, name='Proveedor nuevo', folio=4000, amount=9000),
                     dict(row=8, name='Proveedor E', folio=0, amount=1700, due=46317)]}


def package(path, data=None):
    path.write_text(json.dumps(data or fixture(), ensure_ascii=False))
    return path


class OpeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Store(self.root/'erp.sqlite3');self.path=package(self.root/'source.json')
        self.result=opening.import_snapshot(self.store,self.path,'Importación QA')

    def data(self, **changes):
        result=dict(request_id='synthetic-opening-0001',batch_id=self.result['batch_id'],target_kind='row',target='4',condition='cash',note='Acuerdo ficticio contra entrega',version=0)
        result.update(changes);return result

    def test_evidence_totals_unknown_and_duplicates_not_removed(self):
        value=opening.view(self.store);self.assertEqual(value['totals']['rows'],9)
        self.assertEqual(value['totals']['positive'],19200);self.assertEqual(value['totals']['negative'],-200)
        self.assertEqual(value['totals']['net_recorded'],19000);self.assertEqual(value['totals']['blank_balances'],1)
        self.assertFalse(value['payment_executed']);self.assertTrue(all(r['condition']=='unknown' for r in value['rows']))
        rows={r['row']:r for r in value['rows']}
        self.assertIsNone(rows[8]['balance']);self.assertEqual(rows[8]['computed_balance'],0);self.assertEqual(rows[8]['payment_state'],'review')
        self.assertIsNone(rows[9]['paid']);self.assertEqual(rows[4]['payment_state'],'partial');self.assertEqual(rows[5]['payment_state'],'review')
        self.assertEqual(value['totals']['short_folio'],2);self.assertTrue(rows[10]['folio_review']);self.assertFalse(rows[4]['folio_review'])
        issues=value['issues'];self.assertEqual(next(i for i in issues if i['kind']=='duplicate')['rows'],[6,7])
        differences=[i for i in issues if i['kind']=='amount_difference'];self.assertEqual(len(differences),1);self.assertEqual(differences[0]['rows'],[4])
        self.assertEqual(next(i for i in issues if i['kind']=='form_unmatched')['form_rows'],[7])
        self.assertEqual(next(i for i in issues if i['kind']=='due_difference')['rows'],[11]);self.assertIsNone(rows[11]['due'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT original FROM supplier_opening_batches').fetchone()[0],self.path.read_bytes())

    def test_import_idempotent_conflicting_snapshot_rejected(self):
        self.assertTrue(opening.import_snapshot(self.store,self.path,'Importación QA')['already_imported'])
        changed=fixture();changed['ledger'][0]['amount']+=1
        with self.assertRaises(DomainError) as error:opening.import_snapshot(self.store,package(self.root/'changed.json',changed),'QA socio')
        self.assertEqual(error.exception.status,409)
        changed['file']['id']='synthetic_second_source'
        with self.assertRaises(DomainError):opening.import_snapshot(self.store,package(self.root/'changed.json',changed),'QA socio')
        self.assertEqual(len(opening.view(self.store)['rows']),9)

    def test_conditions_defaults_overrides_reset_and_history_preserve_amounts(self):
        before=opening.view(self.store)
        d=self.data(target_kind='supplier',target='Proveedor A',condition='credit')
        opening.annotate(self.store,d,'socio-qa')
        opening.annotate(self.store,self.data(request_id='synthetic-opening-0002'),'socio-qa')
        rows={r['row']:r for r in opening.view(self.store)['rows']}
        self.assertEqual(rows[4]['condition'],'cash');self.assertEqual(rows[9]['condition'],'credit');self.assertEqual(rows[12]['condition'],'unknown')
        self.assertEqual(rows[4]['payment_state'],'partial')
        opening.annotate(self.store,self.data(request_id='synthetic-opening-0003',version=1,condition='inherit'),'socio-qa')
        after=opening.view(Store(self.store.path));self.assertEqual(after['rows'][0]['condition'],'credit');self.assertEqual(len(after['history']),3)
        self.assertEqual(before['totals'],after['totals']);self.assertEqual(before['issues'],after['issues'])
        self.assertTrue(all(r['due'] is None for r in after['rows']))
        self.assertEqual(after['history'][0]['actor'],'socio-qa')

    def test_annotations_retries_stale_version_actor_and_concurrent_edit(self):
        first=opening.annotate(self.store,self.data(),'socio-qa');self.assertEqual(first,opening.annotate(self.store,self.data(),'socio-qa'))
        for data,actor in [(self.data(condition='credit'),'socio-qa'),(self.data(),'other'),(self.data(request_id='synthetic-opening-0002'),'socio-qa')]:
            with self.assertRaises(DomainError) as error:opening.annotate(self.store,data,actor)
            self.assertEqual(error.exception.status,409)
        results=[]
        def update(n):
            try:results.append(opening.annotate(self.store,self.data(request_id=f'synthetic-concurrent-{n}',version=1),'socio-qa')['version'])
            except DomainError as error:results.append(error.status)
        threads=[threading.Thread(target=update,args=(n,)) for n in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,[2,409])

    def test_invalid_annotations_are_atomic(self):
        for change in ({'condition':[]},{'condition':'paid'},{'target':'999'},{'target':'04'},{'target_kind':'supplier','target':'Unknown'},
                       {'target_kind':'supplier','target':'Proveedor A','condition':'inherit'},{'version':True},{'version':-1},{'note':'x'},
                       {'batch_id':'x'},{'request_id':'x'}):
            with self.subTest(change=change),self.assertRaises(DomainError):opening.annotate(self.store,self.data(**change),'socio-qa')
        self.assertEqual(opening.view(self.store)['history'],[])

    def test_invalid_source_rejected_without_partial_insert(self):
        for change in ({'amount':True},{'amount':1.5},{'paid':-1},{'row':5},{'name':''},{'due':-5}):
            data=fixture();data['ledger'][0].update(change)
            with self.subTest(change=change),self.assertRaises(DomainError):opening.import_snapshot(self.store,package(self.root/'bad.json',data),'socio-qa')
        self.assertEqual(len(opening.view(self.store)['rows']),9)


class OpeningHTTPTests(unittest.TestCase):
    def test_private_read_write_actor_origin_and_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);server=make_server(root/'erp.sqlite3',0,seed=False)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                result=opening.import_snapshot(Store(root/'erp.sqlite3'),package(root/'source.json'),'Importación QA')
                url='http://127.0.0.1:'+str(server.server_address[1])
                data=dict(request_id='synthetic-http-0001',batch_id=result['batch_id'],target_kind='row',target='4',condition='cash',note='Antecedente ficticio',version=0,actor='spoofed')
                partner=authenticated_opener(server,root)
                for role,opener,expected in [('anonymous',None,401),('cashier',authenticated_opener(server,root,'cashier','caja-qa'),403),('production',authenticated_opener(server,root,'production','produccion-qa'),403)]:
                    for path,body in [('/api/suppliers/opening',None),('/api/suppliers/opening/annotations',json.dumps(data).encode())]:
                        with self.subTest(role=role,path=path),self.assertRaises(HTTPError) as error:
                            request=Request(url+path,data=body,headers={'Content-Type':'application/json','X-ERP-Local':'1'})
                            (opener.open if opener else urlopen)(request)
                        self.assertEqual(error.exception.code,expected);error.exception.close()
                with partner.open(url+'/api/suppliers/opening') as response:self.assertTrue(json.load(response)['available'])
                with self.assertRaises(HTTPError) as error:partner.open(Request(url+'/api/suppliers/opening/annotations',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://foreign.invalid'}))
                self.assertEqual(error.exception.code,403);error.exception.close()
                with partner.open(Request(url+'/api/suppliers/opening/annotations',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'})) as response:self.assertEqual(json.load(response)['actor'],'socio-http-qa')
                with urlopen(url+'/supplier-opening.js') as response:self.assertEqual(response.status,200)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
