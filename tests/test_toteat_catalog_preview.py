"""Catálogo exclusivamente ficticio para validar previsualización sin red ni disco."""
import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch
from scripts.toteat_catalog_preview import (DiagnosticError, choose_ids, inspect_catalog, main,
                                         select_preview, show_preview)
from scripts.toteat_diagnostic import query
from test_toteat_diagnostic import FAKE, IDS, Response


def product(product_id='DEMO-P1',category='Tortas enteras',category_id='DEMO-C1',**changes):
    return dict({'id':product_id,'category':category,'categoryId':category_id,'idToteat':101,
                 'localCode':'DEMO-ENT-20','name':'Torta ejemplo 20 personas','isModifier':False,
                 'modifiers':[],'price':99999,'description':'DESCRIPCION_NO_MOSTRAR',
                 'images':['https://example.invalid/imagen-no-mostrar'],'alcohol':False},**changes)


def payload(*rows):return {'ok':True,'data':list(rows),'msg':{'texto':'MENSAJE_NO_MOSTRAR'}}


class CatalogPreviewTests(unittest.TestCase):
    def test_exact_names_real_ids_in_response_and_explicit_selection(self):
        snapshot=inspect_catalog(payload(product(),product('DEMO-P2',' DULCES  ENTEROS ','DEMO-C2',name='Kuchen ejemplo'),product('DEMO-P3','Tortas enteras premium','DEMO-OTHER'),product('DEMO-P4','Bebidas','DEMO-DRINK')))
        self.assertEqual([c['id'] for c in snapshot['categories']],['DEMO-C1','DEMO-C2'])
        selected=choose_ids(snapshot,'1, 2')
        report=select_preview(snapshot,selected)
        self.assertEqual([p['id'] for p in report['products']],['DEMO-P1','DEMO-P2'])
        self.assertEqual(report['excluded_counts'],{'outside_scope':2})
        self.assertEqual(report['received'],len(report['products'])+sum(report['excluded_counts'].values()))

    def test_unselected_and_unknown_categories_never_expand_scope(self):
        snapshot=inspect_catalog(payload(product(),product('DEMO-P2','Dulces enteros','DEMO-C2')))
        report=select_preview(snapshot,['DEMO-C2'])
        self.assertEqual([p['id'] for p in report['products']],['DEMO-P2'])
        self.assertEqual(report['excluded_counts'],{'not_selected':1})
        for selected in [[],['INVENTADO'],['DEMO-C1','DEMO-C1']]:
            with self.assertRaises(DiagnosticError):select_preview(snapshot,selected)
        for selection in ['0','3','1,1','-1','1,x','']:
            with self.assertRaises(DiagnosticError):choose_ids(snapshot,selection)

    def test_same_category_id_with_incompatible_names_is_quarantined(self):
        snapshot=inspect_catalog(payload(product(),product('DEMO-P2','Bebidas','DEMO-C1')))
        self.assertEqual(snapshot['categories'],[])
        self.assertEqual(snapshot['category_conflicts'][0]['id'],'DEMO-C1')
        self.assertTrue(all(e['reason']=='category_conflict' for e in snapshot['entries']))

    def test_same_name_multiple_ids_requires_selection_and_is_not_merged(self):
        snapshot=inspect_catalog(payload(product(),product('DEMO-P2',category_id='DEMO-C9')))
        self.assertEqual(len(snapshot['categories']),2)
        self.assertEqual(len(select_preview(snapshot,['DEMO-C9'])['products']),1)

    def test_identical_duplicate_removed_and_conflicting_id_fails_closed(self):
        first=product()
        snapshot=inspect_catalog(payload(first,copy.deepcopy(first),product('DEMO-P2'),product('DEMO-P2',price=1)))
        report=select_preview(snapshot,['DEMO-C1'])
        self.assertEqual([p['id'] for p in report['products']],['DEMO-P1'])
        self.assertEqual(report['excluded_counts'],{'duplicate':1,'identity_conflict':2})
        self.assertEqual(len(report['selected_exclusions']),3)

    def test_cross_category_identity_conflict_not_silently_imported(self):
        snapshot=inspect_catalog(payload(product(),product(category='Bebidas',category_id='DEMO-OTHER')))
        report=select_preview(snapshot,['DEMO-C1'])
        self.assertEqual(report['products'],[])
        self.assertEqual(report['excluded_counts'],{'identity_conflict':1,'outside_scope':1})

    def test_modifier_records_excluded_but_product_options_not_unpacked(self):
        snapshot=inspect_catalog(payload(product(isModifier=True),product('DEMO-P2',modifiers=[{'id':'DEMO-OPTION','name':'OPCION_NO_MOSTRAR'}]),product('DEMO-P3',isModifier=0)))
        report=select_preview(snapshot,['DEMO-C1'])
        self.assertEqual([p['id'] for p in report['products']],['DEMO-P2'])
        self.assertEqual(report['products'][0]['modifier_options'],1)
        self.assertEqual(report['excluded_counts'],{'modifier':1,'invalid_fields':1})
        self.assertNotIn('OPCION_NO_MOSTRAR',json.dumps(report))

    def test_slices_codes_and_ambiguous_individual_excluded(self):
        rows=[product('DEMO-'+str(i),name=name,localCode=code) for i,(name,code) in enumerate([
            ('Trozo chocolate','DEMO-A'),('Porción cheesecake','DEMO-B'),('Chocolate','DEMO-TROZO-1'),
            ('Kuchen individual','DEMO-C'),('Torta 20 porciones','DEMO-ENT-20'),('Porciones chocolate','DEMO-D')])]
        report=select_preview(inspect_catalog(payload(*rows)),['DEMO-C1'])
        self.assertEqual([p['name'] for p in report['products']],['Torta 20 porciones'])
        self.assertEqual(report['excluded_counts'],{'portion':4,'ambiguous_format':1})

    def test_ok_false_unknown_envelope_and_empty(self):
        for value in [{'ok':False,'data':[product()],'msg':{'texto':FAKE}}, {'ok':1,'data':[]}, {'data':[]}, {'ok':True,'data':{}}]:
            with self.assertRaises(DiagnosticError) as caught:inspect_catalog(value)
            self.assertNotIn(FAKE,str(caught.exception))
        snapshot=inspect_catalog(payload());self.assertEqual(snapshot['received'],0);self.assertEqual(snapshot['categories'],[])

    def test_all_rows_validated_and_balance_for_malformed_records(self):
        rows=[product(),None,{'category':'Tortas enteras'},product('DEMO-P2',name='Nombre\x1b[31m'),product('DEMO-P3',idToteat=True),product('DEMO-P4',modifiers={})]
        report=select_preview(inspect_catalog(payload(*rows)),['DEMO-C1'])
        self.assertEqual(len(report['products']),1)
        self.assertEqual(sum(report['excluded_counts'].values()),5)
        self.assertNotIn('\x1b',json.dumps(report))

    def test_output_minimized_no_price_description_images_or_provider_message(self):
        report=select_preview(inspect_catalog(payload(product())),['DEMO-C1'])
        with contextlib.redirect_stdout(io.StringIO()) as output:show_preview(report)
        text=output.getvalue()
        self.assertIn('DEMO-P1',text);self.assertIn('Torta ejemplo',text)
        for private in ['99999','DESCRIPCION_NO_MOSTRAR','imagen-no-mostrar','MENSAJE_NO_MOSTRAR']:
            self.assertNotIn(private,text)

    def test_transport_projects_only_preview_and_blocks_reflected_token(self):
        calls=[]
        def transport(request,timeout):
            calls.append(request)
            return Response(json.dumps(payload(product())).encode())
        snapshot=query('products',{'activeProducts':'true'},IDS,FAKE,transport,project=inspect_catalog)
        self.assertEqual(len(calls),1)
        self.assertNotIn('categoryId=',calls[0].full_url)
        self.assertEqual(snapshot['categories'][0]['id'],'DEMO-C1')
        with self.assertRaises(DiagnosticError) as caught:
            query('products',{'activeProducts':'true'},IDS,FAKE,lambda *a,**kw:Response(json.dumps(payload(product(name=FAKE))).encode()),project=inspect_catalog)
        self.assertNotIn(FAKE,str(caught.exception))
        quoted=FAKE+'\"quoted'
        with self.assertRaises(DiagnosticError):
            query('products',{'activeProducts':'true'},IDS,quoted,lambda *a,**kw:Response(json.dumps(payload(product(name=quoted))).encode()),project=inspect_catalog)

    def test_user_confirmed_flow_has_one_request_and_no_file_writes(self):
        snapshot=inspect_catalog(payload(product()))
        with patch('builtins.input',side_effect=['DEMO','DEMO','DEMO','CONSULTAR','1','VER']),patch('scripts.toteat_catalog_preview.getpass',return_value=FAKE),patch('scripts.toteat_catalog_preview.query',return_value=snapshot) as send,patch('builtins.open') as disk,contextlib.redirect_stdout(io.StringIO()) as output:
            main()
            send.assert_called_once_with('products',{'activeProducts':'true'},IDS,FAKE,project=inspect_catalog)
            disk.assert_not_called()
            text=output.getvalue();self.assertIn('VISTA PREVIA LOCAL',text);self.assertIn('DEMO-P1',text);self.assertNotIn(FAKE,text)

    def test_cancel_before_request_or_before_preview(self):
        snapshot=inspect_catalog(payload(product()))
        for answers,called in [(['DEMO','DEMO','DEMO','NO'],False),(['DEMO','DEMO','DEMO','CONSULTAR',''],True),(['DEMO','DEMO','DEMO','CONSULTAR','1','NO'],True)]:
            with patch('builtins.input',side_effect=answers),patch('scripts.toteat_catalog_preview.getpass',return_value=FAKE),patch('scripts.toteat_catalog_preview.query',return_value=snapshot) as send,contextlib.redirect_stdout(io.StringIO()) as output:
                main();self.assertEqual(send.called,called);self.assertNotIn('VISTA PREVIA LOCAL',output.getvalue())


if __name__=='__main__':unittest.main()
