"""Conteo interno de productos incorporados: catálogo ficticio con flujo real."""
from pathlib import Path
import tempfile
import unittest
from erp.catalog_import import load_catalog
from erp.domain import DomainError,stock_summary
from erp.store import Store

class RealCatalogStockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'erp.sqlite3')
        catalog=load_catalog(Path(__file__).resolve().parents[1]/'fixtures/catalog_import_demo.json')
        catalog['source']='toteat-manual';self.store.import_catalog(catalog,'Fuente ficticia para QA')
        with self.store.connect() as db:db.execute("INSERT INTO metadata VALUES('operating_mode','toteat-local')")
        product=self.store.catalog()[0]
        self.data={'flavor':product['flavor'],'size':product['size'],'physical':7,'reserved':2}

    def test_real_catalog_flow_same_stock_and_actor_date_reason_history(self):
        first=self.store.update_stock(self.data,'socio-ficticio','Conteo físico de prueba',0)
        self.assertEqual(stock_summary(self.store.stock())['available'],5)
        second=self.store.update_stock(dict(self.data,physical=8),'socio-ficticio','Segundo conteo de prueba',first['version'])
        self.assertEqual(len(self.store.stock()),1);self.assertEqual(second['version'],2)
        history=self.store.stock_history();self.assertEqual(len(history),2)
        self.assertTrue(history[0]['occurred_at']);self.assertEqual(history[0]['actor'],'socio-ficticio')
        self.assertEqual(history[0]['before']['physical'],7);self.assertEqual(history[0]['after']['physical'],8)
        self.assertEqual(Store(self.store.path).stock(),self.store.stock())

    def test_invalid_counts_unknown_zero_stale_and_unregistered_product(self):
        for changes in [{'physical':-1},{'physical':1.5},{'physical':True},{'reserved':-1},{'reserved':8},{'reserved':True},{'flavor':'Producto no incorporado'}]:
            with self.subTest(changes=changes),self.assertRaises(DomainError):self.store.update_stock(dict(self.data,**changes),'socio-ficticio','Validación ficticia',0)
        self.assertEqual(self.store.stock(),[]);self.assertEqual(self.store.stock_history(),[])
        row=self.store.update_stock(dict(self.data,physical=None),'socio-ficticio','Conteo desconocido',0)
        self.assertIsNone(stock_summary(self.store.stock())['available'])
        row=self.store.update_stock(dict(self.data,physical=0,reserved=0),'socio-ficticio','Confirmar cero unidades',row['version'])
        self.assertEqual(stock_summary(self.store.stock())['available'],0)
        with self.assertRaises(DomainError):self.store.update_stock(self.data,'socio-ficticio','Conflicto de versiones',1)
        self.assertEqual(self.store.stock()[0]['physical'],0);self.assertEqual(len(self.store.stock_history()),2)
