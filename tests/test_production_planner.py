import copy
from pathlib import Path
import tempfile
import unittest
from erp.catalog import CATALOG,production_plan
from erp.domain import DomainError
from erp.store import Store

def item(sku,quantity,status='pendiente'):
    p=next(p for p in CATALOG if p['sku']==sku)
    return dict(sku=sku,flavor=p['flavor'],size=p['size'],quantity=quantity,status=status,kind='torta')
def orders(*items):return [{'delivery_timing':'scheduled','items':list(items)}]

class ProductionPlannerTests(unittest.TestCase):
    def test_cake_totals_group_across_orders_and_count_marked_once(self):
        data=orders(item('DEMO-CHO-10',2),item('DEMO-CHO-10',3,'marcado'),item('DEMO-CHO-20',4,'marcado_solicitado'),item('DEMO-CHO-10',90,'entregado'),item('DEMO-CHO-20',90,'cancelado'))
        data+=orders(item('DEMO-CHO-10',1))
        plan=production_plan(data,CATALOG)
        self.assertEqual([(r['size'],r['quantity']) for r in plan['cakes']],[('10 personas',6),('20 personas',4)])
        self.assertEqual(plan['cake_quantity'],10);self.assertIsNone(plan['net_to_make'])
    def test_missing_recipe_is_aggregated_without_invented_bases(self):
        plan=production_plan(orders(item('DEMO-FRA-10',2),item('DEMO-FRA-10',3)),CATALOG)
        self.assertEqual(plan['cakes'][0]['quantity'],5);self.assertFalse(plan['cakes'][0]['recipe_defined'])
        self.assertEqual(len(plan['missing']),1);self.assertEqual(plan['missing'][0]['quantity'],5)
        self.assertEqual(plan['totals'],[])
    def test_legacy_bases_keep_person_sizes_without_invented_diameter_or_units(self):
        plan=production_plan(orders(item('DEMO-CHO-10',2),item('DEMO-CHO-20',3)),CATALOG)
        self.assertEqual(len(plan['totals']),2)
        for row in plan['totals']:
            self.assertIsNone(row['diameter_cm']);self.assertIsNone(row['unit']);self.assertFalse(row['format_confirmed'])
    def test_explicit_same_diameter_and_unit_share_totals_without_rounding(self):
        catalog=copy.deepcopy(CATALOG)
        for p in catalog:
            if p['sku'] in {'DEMO-CHO-10','DEMO-CHO-20'}:
                p['bases'][0].update(unit='whole_sponge',diameter_cm='18',quantity='0.5')
        plan=production_plan(orders(item('DEMO-CHO-10',1),item('DEMO-CHO-20',2)),catalog)
        self.assertEqual(len(plan['totals']),1);row=plan['totals'][0]
        self.assertEqual(row['quantity'],'1.5');self.assertEqual(row['diameter_cm'],'18');self.assertTrue(row['format_confirmed'])
    def test_discs_whole_bases_and_different_diameters_never_merge(self):
        catalog=copy.deepcopy(CATALOG)
        p=next(p for p in catalog if p['sku']=='DEMO-CHO-10')
        p['bases']=[dict(name='Base de prueba',size='10 personas',quantity='1',unit=u,diameter_cm=d) for u,d in [('whole_sponge','18'),('sponge_disc','18'),('whole_sponge','20')]]
        self.assertEqual(len(production_plan(orders(item('DEMO-CHO-10',1)),catalog)['totals']),3)
    def test_recipe_format_persists_and_invalid_change_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'demo.sqlite3';store=Store(path)
            base=dict(name='Base de prueba',quantity='0.5',unit='whole_sponge',diameter_cm='18.00')
            result=store.update_recipe('DEMO-CHO-10',[base],'QA ficticio','Formato ficticio',0)
            self.assertEqual(result['bases'][0]['diameter_cm'],'18')
            self.assertEqual(next(p for p in Store(path).catalog() if p['sku']=='DEMO-CHO-10')['bases'],result['bases'])
            for patch in ({'unit':['whole_sponge']},{'unit':'guess'},{'diameter_cm':'NaN'},{'diameter_cm':'0'},{'diameter_cm':'18.123'}):
                with self.assertRaises(DomainError):store.update_recipe('DEMO-CHO-10',[dict(base,**patch)],'QA ficticio','Inválido',1)
            with store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM recipe_history').fetchone()[0],1)

if __name__=='__main__':unittest.main()
