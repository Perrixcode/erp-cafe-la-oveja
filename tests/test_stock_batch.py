"""Batch counts must save atomically, with the original permissions and audit trail."""
from copy import deepcopy
import unittest
from urllib.error import HTTPError
from erp.domain import DomainError
import test_stock_real_catalog as stock_tests
import test_partner_customer as http_tests


class StockBatchTests(unittest.TestCase):
    setUp=stock_tests.RealCatalogStockTests.setUp

    def entries(self):
        return [{'stock':dict(flavor=p['flavor'],size=p['size'],physical=10+i,reserved=1),'version':0} for i,p in enumerate(self.store.catalog()[:2])]

    def test_multiple_counts_history_and_unknown_zero_preserved(self):
        entries=self.entries();entries[0]['stock'].update(physical=None,reserved=0);entries[1]['stock'].update(physical=0,reserved=0)
        result=self.store.update_stock_batch(entries,'socio-qa','Conteo ficticio completo')
        self.assertEqual(len(result),2);self.assertIsNone(result[0]['physical']);self.assertEqual(result[1]['physical'],0)
        history=self.store.stock_history();self.assertEqual(len(history),2)
        self.assertTrue(all(r['actor']=='socio-qa' and r['reason']=='Conteo ficticio completo' for r in history))

    def test_last_conflict_rolls_back_every_count_and_history(self):
        entries=self.entries();self.store.update_stock(entries[1]['stock'],'socio-qa','Conteo de otro socio',0)
        before=self.store.stock();history=self.store.stock_history()
        with self.assertRaises(DomainError) as error:self.store.update_stock_batch(entries,'socio-qa','Conteo atrasado')
        self.assertEqual(error.exception.status,409);self.assertEqual(self.store.stock(),before);self.assertEqual(self.store.stock_history(),history)

    def test_duplicate_invalid_and_oversized_batches_never_partially_save(self):
        valid=self.entries();bad=deepcopy(valid);bad[-1]['stock']['reserved']=999
        for entries in ([],None,[valid[0],valid[0]],valid*51,bad,valid+[None]):
            with self.subTest(entries=str(entries)[:80]),self.assertRaises(DomainError):self.store.update_stock_batch(entries,'socio-qa','Conteo ficticio')
        self.assertEqual(self.store.stock(),[]);self.assertEqual(self.store.stock_history(),[])


class StockBatchHTTPTests(unittest.TestCase):
    setUp=http_tests.PartnerCustomerTests.setUp
    close=http_tests.PartnerCustomerTests.close
    api=http_tests.PartnerCustomerTests.api
    login=http_tests.PartnerCustomerTests.login

    def test_permissions_origin_and_actor_from_session(self):
        p=self.store.catalog()[0]
        body={'entries':[{'stock':dict(flavor=p['flavor'],size=p['size'],physical=3,reserved=1),'version':0}],'reason':'Conteo ficticio HTTP','actor':'forged'}
        for role in ('anonymous','cashier','production'):
            if role!='anonymous':
                self.auth.create(role,role,'Clave-ficticia-QA-2026',role)
                self.api('/api/partner/login',dict(username=role,password='Clave-ficticia-QA-2026'))
            with self.assertRaises(HTTPError) as error:self.api('/api/stock/batch',body,'PUT')
            self.assertEqual(error.exception.code,401 if role=='anonymous' else 403);error.exception.close()
        self.login()
        with self.assertRaises(HTTPError) as error:self.api('/api/stock/batch',body,'PUT',{'Origin':'https://foreign.invalid'})
        self.assertEqual(error.exception.code,403);error.exception.close()
        self.assertEqual(len(self.api('/api/stock/batch',body,'PUT')[0]['rows']),1)
        self.assertEqual(self.store.stock_history()[0]['actor'],'socio-qa')
