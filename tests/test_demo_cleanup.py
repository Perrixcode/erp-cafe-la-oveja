import tempfile
import unittest
from contextlib import closing
import sqlite3
from pathlib import Path
from datetime import date
from erp.catalog_import import load_catalog
from erp.demo import seed_demo
from erp.domain import DomainError
from erp.maintenance import remove_demo_data, fingerprint
from erp.store import Store
from test_erp import sample


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data.sqlite3')
        payload=load_catalog(Path(__file__).resolve().parents[1]/'fixtures/catalog_import_demo.json')
        payload['source']='toteat-manual';self.store.import_catalog(payload,'Prueba ficticia')
        seed_demo(self.store,date(2026,10,4))

    def test_backup_verifies_and_only_real_catalog_remains_after_restart(self):
        real=[p for p in self.store.catalog() if p['source']=='toteat-manual']
        self.store.update_stock(dict(flavor='Chocolate',size='20 personas',physical=7,reserved=2),'Demo','Prueba',0)
        with self.store.connect() as db:before=fingerprint(db)
        result=remove_demo_data(self.store,self.root/'backups')
        with closing(sqlite3.connect(result['backup'])) as backup:self.assertEqual(fingerprint(backup),before)
        self.assertEqual(self.store.catalog(),real);self.assertEqual(self.store.stock(),[])
        reopened=Store(self.store.path);seed_demo(reopened,date(2026,10,5))
        self.assertEqual(reopened.list('2020-01-01','2030-12-31'),[])
        self.assertEqual(reopened.notifications(),[])
        self.assertEqual(reopened.operating_mode(),'toteat-local')
        self.assertEqual(remove_demo_data(reopened,self.root/'backups'),{'already_clean':True})
        with self.assertRaises(DomainError):reopened.create(sample(),'Demo')
        with reopened.connect() as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_ambiguous_customer_blocks_cleanup_without_changes(self):
        data=sample();data.update(source_id='DEMO-AMBIGUOUS',customer='Nombre sin clasificar')
        self.store.create(data,'Operador')
        with self.store.connect() as db:before=fingerprint(db)
        with self.assertRaises(DomainError):remove_demo_data(self.store,self.root/'backups')
        with self.store.connect() as db:self.assertEqual(fingerprint(db),before)

    def test_real_catalog_recipe_and_stock_are_preserved(self):
        p=next(p for p in self.store.catalog() if p['source']=='toteat-manual')
        self.store.update_recipe(p['sku'],None,'Demo','Verificación de conservación',0)
        self.store.update_stock(dict(flavor=p['flavor'],size=p['size'],physical=3,reserved=1),'Demo','Verificación de conservación',0)
        before=self.store.stock();recipe=next(r for r in self.store.catalog() if r['sku']==p['sku'])
        remove_demo_data(self.store,self.root/'backups')
        self.assertEqual(self.store.stock(),before)
        self.assertEqual(next(r for r in self.store.catalog() if r['sku']==p['sku']),recipe)
        self.assertEqual(len(self.store.stock_history()),1)


if __name__=='__main__':unittest.main()
