"""Importación local: solo datos inventados y bases temporales; ninguna red."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from erp.catalog import CATALOG, production_plan
from erp.catalog_import import load_catalog, validate_catalog
from erp.domain import DomainError, stock_summary
from erp.store import Store
from test_erp import sample

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/catalog_import_demo.json'


class CatalogImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'local.sqlite3')
        self.payload = load_catalog(FIXTURE)

    def snapshot(self, tables):
        with self.store.connect() as db:
            return {name: [tuple(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')] for name in tables}

    def imported(self):
        return [p for p in self.store.catalog() if p['source'] == 'manual-demo']

    def test_additive_preserves_orders_history_stock_and_recipes(self):
        order = self.store.create(sample(), 'Demo')
        self.store.transition(order['id'], order['items'][0]['id'], 'marcado_solicitado', 'Demo', 'Prueba', order['version'])
        self.store.update_stock(dict(flavor='Chocolate',size='20 personas',physical=7,reserved=2),'Demo','Prueba',0)
        self.store.update_recipe('DEMO-MIX-20',None,'Demo','Prueba',0)
        tables = ('orders','items','history','stock','stock_history','recipes','recipe_history','documents','metadata')
        before = self.snapshot(tables)
        result = self.store.import_catalog(self.payload, 'Demo')
        self.assertEqual(result['added'],4)
        self.assertEqual(before,self.snapshot(tables))
        self.assertEqual(len(self.store.catalog()),len(CATALOG)+4)
        self.assertEqual(self.store.catalog(),Store(self.store.path).catalog())

    def test_repeated_reordered_import_no_duplicates_or_new_audit(self):
        self.store.import_catalog(self.payload,'Demo')
        before = self.snapshot(('catalog_products','catalog_imports'))
        self.payload['products'].reverse()
        result = self.store.import_catalog(self.payload,'Segundo operador demo')
        self.assertEqual((result['added'],result['unchanged']),(0,4))
        self.assertEqual(before,self.snapshot(('catalog_products','catalog_imports')))

    def test_conflict_rolls_back_new_products_and_entire_audit(self):
        last = sorted(validate_catalog(self.payload),key=lambda p:p['sku'])[-1]
        row = next(r for r in self.payload['products'] if r['id']==last['source_product']['id'])
        first = copy.deepcopy(self.payload);first['products']=[copy.deepcopy(row)]
        self.store.import_catalog(first,'Demo')
        before = self.snapshot(('catalog_products','catalog_imports'))
        row['name'] += ' cambiado'
        with self.assertRaises(DomainError): self.store.import_catalog(self.payload,'Demo')
        self.assertEqual(before,self.snapshot(('catalog_products','catalog_imports')))

    def test_exact_ids_and_origin_preserved_not_mapped_to_demo_sku(self):
        self.payload['products'][0].update(id='0000123',localCode='0000456',idToteat=9007199254740991)
        self.store.import_catalog(self.payload,'Demo')
        p = next(p for p in self.imported() if p['source_product']['id']=='0000123')
        self.assertEqual(p['source_product']['localCode'],'0000456')
        self.assertEqual(p['source_product']['idToteat'],9007199254740991)
        self.assertEqual(p['source_reference'],self.payload['source_reference'])
        self.assertTrue(p['sku'].startswith('LOCAL-'))

    def test_unknown_stock_is_not_zero_import_never_inserts_stock(self):
        self.store.import_catalog(self.payload,'Demo')
        self.assertEqual(self.store.stock(),[])
        self.assertIsNone(stock_summary(self.store.stock())['available'])
        p = self.imported()[0]
        self.store.update_stock(dict(flavor=p['flavor'],size=p['size'],physical=0,reserved=0),'Demo','Cero contado',0)
        self.assertEqual(stock_summary(self.store.stock())['available'],0)
        before = self.snapshot(('stock','stock_history'))
        self.store.import_catalog(self.payload,'Demo')
        self.assertEqual(before,self.snapshot(('stock','stock_history')))

    def test_local_products_work_in_order_edit_and_plan_without_overriding_recipe(self):
        self.store.import_catalog(self.payload,'Demo')
        p = next(p for p in self.imported() if p['category']=='mixta')
        data=sample();data['items']=[dict(source_item_id='1',sku=p['sku'],flavor=p['flavor'],size=p['size'],quantity=3,kind='torta',comments='')]
        order=self.store.create(data,'Demo')
        self.assertEqual(production_plan([order],self.store.catalog())['totals'][0]['quantity'],'1.5')
        data['items'][0]['quantity']=2
        updated=self.store.update(order['id'],data,'Demo','Corrección prueba',order['version'])
        self.assertEqual(updated['items'][0]['quantity'],2)
        self.store.update_recipe(p['sku'],[dict(name='Bizcocho prueba',quantity='0.25')],'Demo','Edición independiente',0)
        self.store.import_catalog(self.payload,'Demo')
        p=next(row for row in self.imported() if row['sku']==p['sku'])
        self.assertEqual(p['version'],1);self.assertEqual(p['bases'][0]['quantity'],'0.25')
        self.assertEqual(p['recipe_status'],'edited')

    def test_missing_recipe_and_sweets_no_people_size(self):
        self.store.import_catalog(self.payload,'Demo')
        for product in self.imported():
            if product['category']=='pie':
                self.assertIsNone(product['size_people']);self.assertIn('no confirmado',product['size']);self.assertIsNone(product['bases'])
            if product['category']=='bizcocho': self.assertIsNone(product['bases'])

    def test_payload_rejects_unknown_fields_duplicate_ids_and_invented_sizes(self):
        changes=[{'price':1000},{'physical':0},{'id':' X '},{'idToteat':True},{'idToteat':9007199254740992},
                 {'size_people':True},{'size_people':15},{'modifier_options':1},{'recipe_status':'confirmed'},
                 {'name':'Con\nsalto'},{'catalog_group':'Bebidas'},{'product_type':'trozo'}, {'name':'Trozo de prueba'}]
        for change in changes:
            with self.subTest(change=change):
                payload=copy.deepcopy(self.payload);payload['products'][0].update(change)
                with self.assertRaises(DomainError):self.store.import_catalog(payload,'Demo')
        payload=copy.deepcopy(self.payload);payload['products'].append(copy.deepcopy(payload['products'][0]))
        with self.assertRaises(DomainError): self.store.import_catalog(payload,'Demo')
        self.assertEqual(self.imported(),[])

    def test_category_conflict_and_external_identity_reuse_rejected(self):
        first=copy.deepcopy(self.payload);first['products']=first['products'][:1]
        self.store.import_catalog(first,'Demo')
        for change in ({'category':'Otro nombre'}, {'idToteat':9001}, {'localCode':'DEMO-CODE-001'}):
            second=copy.deepcopy(self.payload);second['products']=[second['products'][1]];second['products'][0].update(change)
            with self.assertRaises(DomainError):self.store.import_catalog(second,'Demo')
        self.assertEqual(len(self.imported()),1)

    def test_existing_demo_product_pair_cannot_merge_stock(self):
        self.payload['products'][0].update(name='Chocolate',size_people=20)
        with self.assertRaises(DomainError):self.store.import_catalog(self.payload,'Demo')
        self.assertEqual(self.imported(),[])

    def test_loader_blocks_duplicate_json_keys_and_oversized_payload(self):
        path=Path(self.temp.name)/'input.json'
        path.write_text('{"source":"x","source":"y"}')
        with self.assertRaises(DomainError):load_catalog(path)
        path.write_bytes(b' '* (256*1024+1))
        with self.assertRaises(DomainError):load_catalog(path)

    def test_invalid_types_and_base_quantities_are_domain_errors(self):
        for source in ([],{},None,True):
            payload=copy.deepcopy(self.payload);payload['source']=source
            with self.assertRaises(DomainError):validate_catalog(payload)
        for quantity in ('NaN','Infinity','0','-1','0.0001',True):
            payload=copy.deepcopy(self.payload);payload['products'][2]['bases'][0]['quantity']=quantity
            with self.assertRaises(DomainError):validate_catalog(payload)
        payload=copy.deepcopy(self.payload);payload['products'][3]['size_people']=20
        with self.assertRaises(DomainError):validate_catalog(payload)


if __name__ == '__main__': unittest.main()
