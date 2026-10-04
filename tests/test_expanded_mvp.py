"""Regresiones de requisitos: enteros, rangos, reservas, recetas y avisos."""
import copy
import tempfile
import unittest
from pathlib import Path
from erp.catalog import CATALOG, production_plan
from erp.domain import DomainError, date_range, stock_summary, summarize
from erp.store import Store
from test_erp import sample


class ExpandedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'demo.sqlite3')

    def order_with(self,sku,quantity=1,source_id='DEMO-CASE'):
        product=next(p for p in CATALOG if p['sku']==sku)
        order=sample(); order['source_id']=source_id
        order['items']=[dict(source_item_id='1',sku=sku,flavor=product['flavor'],size=product['size'],kind='torta',quantity=quantity,comments='')]
        return self.store.create(order,'Operador demo')

    def test_stock_reserved_x_zero_unknown_and_no_double_subtract(self):
        data={'flavor':'Chocolate','size':'20 personas','physical':7,'reserved':3}
        self.store.update_stock(data,'Esteban demo','Reservar tres',0)
        self.order_with('DEMO-CHO-20',2)
        self.assertEqual(stock_summary(self.store.stock())['available'],4)
        data['reserved']=0; self.store.update_stock(data,'Esteban demo','Liberar todas',1)
        self.assertEqual(stock_summary(self.store.stock())['available'],7)
        data.update(physical=None,reserved=2); self.store.update_stock(data,'Esteban demo','Conteo desconocido',2)
        self.assertIsNone(stock_summary(self.store.stock())['available'])
        self.assertEqual(len(self.store.stock_history()),3)
        self.assertEqual(Store(self.store.path).stock(),self.store.stock())

    def test_stock_limits_stale_and_no_write_on_invalid(self):
        for physical,reserved in [(1,2),(0,-1),(3,1.5),(True,0),(0,True)]:
            with self.assertRaises(DomainError): self.store.update_stock(dict(flavor='Chocolate',size='20 personas',physical=physical,reserved=reserved),'Demo','Prueba',0)
        valid=dict(flavor='Chocolate',size='20 personas',physical=3,reserved=1)
        self.store.update_stock(valid,'Demo','Prueba',0)
        with self.assertRaises(DomainError): self.store.update_stock(valid,'Demo','Duplicado',0)
        self.assertEqual(len(self.store.stock_history()),1)

    def test_calendar_month_leap_and_year_ranges(self):
        self.assertEqual(date_range('2028-02-14','month'),('2028-02-01','2028-02-29'))
        self.assertEqual(date_range('2026-12-31','week'),('2026-12-28','2027-01-03'))
        self.assertEqual(date_range('2027-01-01','biweekly'),('2026-12-21','2027-01-03'))
        self.assertEqual(date_range('2026-01-04','biweekly'),('2025-12-22','2026-01-04'))
        self.assertEqual(date_range('2026-12-31','custom','2027-01-02'),('2026-12-31','2027-01-02'))

    def test_custom_range_missing_reversed_and_empty_results(self):
        for start,end in [('', ''),('2026-10-01',''),('2026-10-02','2026-10-01')]:
            with self.assertRaises(DomainError): date_range(start,'custom',end)
        self.order_with('DEMO-CHO-10')
        self.assertEqual(self.store.list('2027-01-01','2027-01-31'),[])
        self.assertEqual(len(self.store.list('2026-10-05','2026-10-05')),1)

    def test_explicit_whole_catalog_excludes_unknown_and_wrong_sizes(self):
        for change in [{'sku':'DEMO-TROZO-1'},{'kind':'trozo'},{'sku':'UNKNOWN'}, {'size':'15 personas'}]:
            data=sample();data['items'][0].update(change)
            with self.assertRaises(DomainError):self.store.create(data,'Demo')
        self.assertTrue(all(p['whole'] for p in CATALOG))
        self.assertEqual({p['catalog_group'] for p in CATALOG},{'Tortas enteras','Dulces enteros'})
        for product in CATALOG:
            if product['category'] in {'hojarasca','mixta','zanahoria'}:self.assertEqual(product['size'],'20 personas')
        self.assertEqual({p['size'] for p in CATALOG if p['category']=='bizcocho'},{'10 personas','20 personas'})

    def test_whole_sweets_have_no_invented_recipe(self):
        for sku in ['DEMO-CHEESE-ENTERO','DEMO-PIE-ENTERO','DEMO-KUCHEN-ENTERO','DEMO-ZAN-20']:
            order=self.order_with(sku,source_id=sku)
            plan=production_plan([order],self.store.catalog())
            self.assertEqual(plan['totals'],[]);self.assertEqual(len(plan['missing']),1)

    def test_fractional_mixed_recipe_exact_edit_and_sizes_separate(self):
        mixed=self.order_with('DEMO-MIX-20',3)
        small=self.order_with('DEMO-CHO-10',2,'DEMO-SMALL')
        plan=production_plan([mixed,small],self.store.catalog())
        totals={(b['name'],b['size']):b['quantity'] for b in plan['totals']}
        self.assertEqual(totals[('Bizcocho chocolate','20 personas')],'1.5')
        self.assertEqual(totals[('Bizcocho chocolate','10 personas')],'2')
        self.assertEqual(totals[('Hojarasca','20 personas')],'18')
        self.store.update_recipe('DEMO-MIX-20',[{'name':'Bizcocho chocolate','quantity':'0.25'},{'name':'Hojarasca','quantity':'7'}],'Demo','Receta corregida',0)
        plan=production_plan([mixed],self.store.catalog())
        self.assertEqual(plan['totals'][0]['quantity'],'0.75')
        self.assertEqual(plan['totals'][1]['quantity'],'21')
        with self.assertRaises(DomainError): self.store.update_recipe('DEMO-MIX-20',None,'Demo','Versión anterior',0)
        self.assertEqual(Store(self.store.path).catalog(),self.store.catalog())

    def test_recipe_invalid_fractions_and_pending(self):
        for quantity in ['NaN','Infinity','0','-1','0.0001','text']:
            with self.assertRaises(DomainError):self.store.update_recipe('DEMO-MIX-20',[{'name':'Base','quantity':quantity}],'Demo','Prueba',0)
        self.store.update_recipe('DEMO-MIX-20',None,'Demo','Pendiente',0)
        self.assertIsNone(next(p for p in self.store.catalog() if p['sku']=='DEMO-MIX-20')['bases'])

    def test_request_mark_is_not_mark_unique_and_ack_resolves(self):
        order=self.order_with('DEMO-CHO-20',3)
        item=order['items'][0]['id']
        for status in ['marcado_solicitado']:
            order=self.store.transition(order['id'],item,status,'Jefa demo','Solicitar por ítem',order['version'])
        self.assertEqual(len(self.store.notifications()),1)
        self.assertEqual(self.store.notifications()[0]['quantity'],3)
        self.assertEqual(summarize([order])['metrics']['marked'],0)
        with self.assertRaises(DomainError):self.store.transition(order['id'],item,'marcado_solicitado','Jefa demo','Click repetido',order['version']-1)
        self.assertEqual(len(self.store.notifications()),1)
        order=self.store.transition(order['id'],item,'marcado','Esteban demo','Ya está marcado',order['version'])
        self.assertEqual(self.store.notifications(),[])
        self.assertEqual(summarize([order])['metrics']['marked'],3)
        self.assertEqual(self.store.analytics([order])['mark_samples'],1)

    def test_receipt_pending_no_fabricated_link_and_no_kpi_without_events(self):
        order=self.order_with('DEMO-CHO-20')
        self.assertEqual(order['receipt'],{'source':'toteat','status':'pending'})
        stats=self.store.analytics([order]);self.assertIsNone(stats['mark_minutes']);self.assertEqual(stats['on_time_total'],0)
        self.assertEqual(stats['orders'],1);self.assertEqual(stats['units'],1)

    def test_legacy_production_step_migrates_with_history_once(self):
        order=self.order_with('DEMO-CHO-20')
        item=order['items'][0]['id']
        with self.store.connect() as db:
            db.execute("UPDATE items SET status='solicitado' WHERE id=?",(item,))
        migrated=Store(self.store.path)
        self.assertEqual(migrated.get(order['id'])['items'][0]['status'],'pendiente')
        self.assertEqual(migrated.history(order['id'])[0]['before']['status'],'solicitado')
        self.assertEqual(migrated.notifications(),[])
        again=Store(self.store.path)
        self.assertEqual(len(again.history(order['id'])),2)


if __name__=='__main__':unittest.main()
