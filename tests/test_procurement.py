"""Synthetic ingredient purchase/receipt invariants; never an operational DB."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from erp import payables as ap, procurement as pc, supplier_directory as directory
from erp.domain import DomainError
from erp.store import Store


class ProcurementTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'synthetic.sqlite3')
        fixed=patch.object(ap,'local_day',return_value='2026-10-14');fixed.start();self.addCleanup(fixed.stop)
        self.supplier=self.finance('supplier',dict(name='Proveedor ficticio D',contact='Contacto existente QA',terms='Condición existente QA'))
        self.kg=self.command('unit',dict(code='kg',name='Kilogramo QA',dimension='mass',precision=3))
        self.bag=self.command('unit',dict(code='saco',name='Saco QA',dimension='package',precision=0))
        self.warehouse=self.command('warehouse',dict(name='Bodega ficticia QA'))
        self.item=self.command('item',dict(code='HARINA-QA',name='Harina ficticia',base_unit_id=self.kg['id']))
        self.equivalence=self.command('equivalence',dict(item_id=self.item['id'],version=1,unit_id=self.bag['id'],base_quantity='25',reason='Etiqueta ficticia de 25 kg'))
        self.item['version']=2

    def command(self,action,data,target=None):
        return pc.execute(self.store,action,dict(data,request_id=str(uuid4())),'socio-qa',target)

    def finance(self,action,data,target=None):
        return ap.execute(self.store,action,dict(data,request_id=str(uuid4())),'socio-qa',target)

    def order(self,**changes):
        values=dict(supplier_id=self.supplier['id'],supplier_version=self.supplier['version'],warehouse_id=self.warehouse['id'],ordered_on='2026-10-12',expected_on='2026-10-13',confirmed=True,
                    lines=[dict(item_id=self.item['id'],version=self.item['version'],unit_id=self.bag['id'],quantity='10')])
        values.update(changes);return self.command('order',values)

    def current(self,order):
        with self.store.connect() as db:return pc.order_view(db,order['id'])

    def receipt_body(self,order,accepted='3',rejected='0',reference='ENTREGA-QA-1',**changes):
        values=dict(version=order['version'],received_on='2026-10-14',reference=reference,confirmed=True,
                    lines=[dict(order_line_id=order['lines'][0]['id'],unit_id=self.bag['id'],accepted=accepted,rejected=rejected,reason='Envase ficticio dañado' if rejected!='0' else '')])
        values.update(changes);return values

    def receive(self,order,**changes):return self.command('receipt',self.receipt_body(order,**changes),order['id'])

    def ledger(self):
        with self.store.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM pc_stock_lines ORDER BY id')]

    def snapshot(self,financial=False):
        names=('ap_suppliers','ap_invoices','ap_payments','ap_account_entries','ap_account_applications','stock','stock_history') if financial else ('pc_orders','pc_order_lines','pc_receipts','pc_receipt_lines','pc_stock_documents','pc_stock_lines','pc_invoice_links','pc_issues','pc_requests','pc_events')
        with self.store.connect() as db:return {n:[tuple(r) for r in db.execute('SELECT * FROM '+n+' ORDER BY 1')] for n in names}

    def invoice(self,folio='F-QA',supplier=None):
        return self.finance('invoice',dict(supplier_id=supplier or self.supplier['id'],document_type='invoice',folio=folio,amount=10000,issued_on='2026-10-12',due_on='2026-10-30',condition='credit'))

    def test_empty_configuration_never_invents_stock_or_warehouse(self):
        with tempfile.TemporaryDirectory() as folder:
            data=pc.overview(Store(Path(folder)/'empty.sqlite3'))
        self.assertEqual((data['units'],data['warehouses'],data['items'],data['balances']),( [],[],[],[]))
        self.assertFalse(data['opening_verified'])

    def test_two_partial_receipts_complete_exactly_once_and_leave_rejection_pending(self):
        order=self.order();before=self.snapshot(financial=True)
        self.receive(order,accepted='3',rejected='1')
        partial=self.current(order);line=partial['lines'][0]
        self.assertEqual((partial['state'],line['accepted'],line['rejected'],line['pending']),('partial','3','1','7'))
        self.assertEqual(partial['open_issues'],1)
        self.receive(partial,accepted='7',reference='ENTREGA-QA-2')
        complete=self.current(order)
        self.assertEqual((complete['state'],complete['lines'][0]['accepted'],complete['lines'][0]['pending']),('complete','10','0'))
        self.assertEqual(self.ledger(),[])
        self.assertEqual(before,self.snapshot(financial=True))
        self.assertEqual(pc.overview(self.store)['balances'],[])

    def test_same_request_is_idempotent_and_changed_request_body_is_rejected(self):
        order=self.order();body=dict(self.receipt_body(order),request_id=str(uuid4()))
        result=pc.execute(self.store,'receipt',body,'QA',order['id']);before=self.snapshot()
        self.assertEqual(pc.execute(self.store,'receipt',body,'QA',order['id']),result)
        self.assertEqual(before,self.snapshot())
        with self.assertRaises(DomainError):pc.execute(self.store,'receipt',dict(body,reference='OTHER'),'QA',order['id'])

    def test_duplicate_delivery_reference_under_new_request_is_atomic(self):
        order=self.order();self.receive(order);current=self.current(order);before=self.snapshot()
        with self.assertRaises(DomainError):self.receive(current,reference='entrega qa 1')
        self.assertEqual(before,self.snapshot())

    def test_invalid_quantities_units_and_over_receipt_do_not_write(self):
        order=self.order();valid=self.receipt_body(order)
        for row_changes in ({'accepted':'11'},{'accepted':'9','rejected':'2'},{'accepted':'0','rejected':'0'},{'accepted':True},{'accepted':1.0},{'accepted':'-1'},{'accepted':'NaN'},{'accepted':'0.5'},{'accepted':'1.0000001'},{'unit_id':self.kg['id']},{'unit_id':True},{'rejected':'1','reason':''},{'order_line_id':999}):
            before=self.snapshot();data=dict(valid,lines=[dict(valid['lines'][0],**row_changes)])
            with self.subTest(row_changes=row_changes),self.assertRaises(DomainError):self.command('receipt',data,order['id'])
            self.assertEqual(before,self.snapshot())

    def test_rejection_only_has_no_stock_and_is_traceable(self):
        order=self.order();result=self.receive(order,accepted='0',rejected='2')
        self.assertIsNone(result['stock_document_id']);self.assertEqual(self.ledger(),[])
        row=self.current(order);self.assertEqual((row['state'],row['lines'][0]['pending']),('open','10'))
        self.assertEqual(row['issues'][0]['actor'],'socio-qa')

    def test_invalid_dates_confirmation_and_stale_version(self):
        order=self.order()
        for changes in ({'received_on':'2026-10-11'},{'received_on':'2026-10-15'},{'confirmed':False},{'version':999},{'reference':''}):
            with self.subTest(changes=changes),self.assertRaises(DomainError):self.receive(order,**changes)
        self.assertEqual(self.ledger(),[])

    def test_duplicate_and_foreign_receipt_lines_are_rejected(self):
        order=self.order();other=self.order();body=self.receipt_body(order)
        for lines in ([body['lines'][0],body['lines'][0]],[dict(body['lines'][0],order_line_id=other['lines'][0]['id'])]):
            with self.assertRaises(DomainError):self.command('receipt',dict(body,lines=lines),order['id'])
        self.assertEqual(self.ledger(),[])

    def test_explicit_equivalence_is_required_and_order_keeps_snapshot(self):
        litre=self.command('unit',dict(code='l',name='Litro QA',dimension='volume',precision=3))
        with self.assertRaises(DomainError):self.order(lines=[dict(item_id=self.item['id'],version=2,unit_id=litre['id'],quantity='1')])
        order=self.order()
        self.assertEqual(order['lines'][0]['base_ratio_micros'],25*pc.SCALE)
        with self.assertRaises(DomainError):self.command('equivalence',dict(item_id=self.item['id'],version=2,unit_id=self.bag['id'],base_quantity='20',reason='Intento de redefinir'))
        self.assertEqual(self.current(order)['lines'][0]['base_ratio_micros'],25*pc.SCALE)

    def test_no_implicit_rounding_to_base_unit(self):
        unit=self.command('unit',dict(code='u',name='Unidad indivisible',dimension='count',precision=0))
        item=self.command('item',dict(code='UNIDAD-QA',name='Unidad QA',base_unit_id=unit['id']))
        self.command('equivalence',dict(item_id=item['id'],version=1,unit_id=self.kg['id'],base_quantity='0.333333',reason='Equivalencia ficticia no exacta'))
        with self.assertRaises(DomainError):self.order(lines=[dict(item_id=item['id'],version=2,unit_id=self.kg['id'],quantity='1')])

    def test_cancelling_partial_order_keeps_received_stock_and_closes_only_pending(self):
        order=self.order();self.receive(order);current=self.current(order);before=self.ledger()
        result=self.command('cancel',dict(version=current['version'],reason='Pendiente ya no requerido'),order['id'])
        self.assertEqual((result['state'],result['lines'][0]['accepted'],result['lines'][0]['cancelled'],result['lines'][0]['pending']),('cancelled','3','7','0'))
        self.assertEqual(before,self.ledger())
        with self.assertRaises(DomainError):self.receive(result,reference='AFTER-CANCEL')

    def test_reversal_is_append_only_and_restores_pending_without_debt(self):
        order=self.order();receipt=self.receive(order,accepted='3',rejected='1');current=self.current(order);before=self.snapshot(financial=True)
        self.command('reverse_receipt',dict(version=1,order_version=current['version'],reason='Recepción ficticia registrada por error'),receipt['id'])
        self.assertEqual(self.ledger(),[])
        current=self.current(order);self.assertEqual((current['state'],current['lines'][0]['pending'],current['open_issues']),('open','10',0))
        self.assertEqual(before,self.snapshot(financial=True))
        with self.assertRaises(DomainError):self.command('reverse_receipt',dict(version=2,order_version=current['version'],reason='Segundo reverso'),receipt['id'])

    def test_reversal_on_cancelled_order_does_not_reopen_or_create_negative_pending(self):
        order=self.order();receipt=self.receive(order);current=self.current(order)
        cancelled=self.command('cancel',dict(version=current['version'],reason='Cierre ficticio'),order['id'])
        self.command('reverse_receipt',dict(version=1,order_version=cancelled['version'],reason='Corrección posterior'),receipt['id'])
        current=self.current(order);line=current['lines'][0]
        self.assertEqual((current['state'],line['accepted'],line['cancelled'],line['pending']),('cancelled','0','10','0'))
        self.assertEqual(sum(r['quantity_micros'] for r in self.ledger()),0)

    def test_corrected_delivery_reuses_reference_only_after_reversal_with_link(self):
        order=self.order();receipt=self.receive(order);current=self.current(order)
        self.command('reverse_receipt',dict(version=1,order_version=current['version'],reason='Corregir cantidad'),receipt['id'])
        corrected=self.receive(self.current(order),accepted='2')
        row=next(r for r in self.current(order)['receipts'] if r['id']==corrected['id'])
        self.assertEqual(row['replaces_receipt_id'],receipt['id'])
        self.assertEqual(self.ledger(),[])

    def test_concurrent_receipts_on_same_version_only_one_succeeds(self):
        order=self.order();barrier=threading.Barrier(2);results=[]
        def run(reference):
            barrier.wait()
            try:self.receive(order,accepted='7',reference=reference);results.append(201)
            except DomainError as error:results.append(error.status)
        threads=[threading.Thread(target=run,args=(ref,)) for ref in ('A','B')]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertCountEqual(results,[201,409]);self.assertEqual(self.ledger(),[])

    def test_invoice_before_after_and_payment_never_add_stock_or_duplicate_debt(self):
        order=self.order();invoice=self.invoice();debt=ap.view(self.store)['totals']['balance']
        self.command('invoice_link',dict(version=1,invoice_id=invoice['id'],invoice_version=1,reason='Factura antes de entrega'),order['id'])
        self.assertEqual(self.ledger(),[])
        self.receive(self.current(order),invoice_id=invoice['id'],invoice_version=1)
        ledger=self.ledger();invoice2=self.invoice('F-QA-2')
        self.command('invoice_link',dict(version=self.current(order)['version'],invoice_id=invoice2['id'],invoice_version=1,reason='Factura después de entrega'),order['id'])
        self.assertEqual(self.ledger(),ledger);self.assertEqual(ap.view(self.store)['totals']['balance'],debt+10000)
        self.finance('payment',dict(version=1,amount=1000,paid_on='2026-10-14',method='cash',confirmed=True),invoice['id'])
        self.assertEqual(self.ledger(),ledger);self.assertEqual(ap.view(self.store)['totals']['balance'],19000)
        self.assertEqual(len([r for r in self.current(order)['invoices'] if not r['voided_at']]),2)

    def test_wrong_supplier_invoice_rolls_back_entire_receipt(self):
        order=self.order();other=self.finance('supplier',dict(name='Otro proveedor QA'));invoice=self.invoice(supplier=other['id']);before=self.snapshot()
        with self.assertRaises(DomainError):self.receive(order,invoice_id=invoice['id'],invoice_version=1)
        self.assertEqual(before,self.snapshot())

    def test_linked_invoice_supplier_change_requires_explicit_unlink(self):
        order=self.order();invoice=self.invoice();other=self.finance('supplier',dict(name='Otro proveedor QA'))
        link=self.command('invoice_link',dict(version=1,invoice_id=invoice['id'],invoice_version=1,reason='Referencia'),order['id'])
        data=dict(invoice,supplier_id=other['id'],reason='Corregir proveedor')
        with self.assertRaises(DomainError):self.finance('invoice',data,invoice['id'])
        self.command('invoice_unlink',dict(version=self.current(order)['version'],reason='Vínculo equivocado'),link['id'])
        self.finance('invoice',data,invoice['id']);self.assertEqual(self.ledger(),[])

    def test_void_invoice_is_visible_without_altering_stock(self):
        order=self.order();invoice=self.invoice();self.receive(order,invoice_id=invoice['id'],invoice_version=1);ledger=self.ledger()
        self.finance('void_invoice',dict(version=1,reason='Documento ficticio anulado'),invoice['id'])
        current=self.current(order);self.assertTrue(current['invoices'][0]['invoice_voided_at']);self.assertTrue(current['missing_invoice']);self.assertEqual(ledger,self.ledger())

    def test_issue_resolution_keeps_actor_reason_history(self):
        order=self.order();issue=self.command('issue',dict(version=1,kind='quality',detail='Revisar calidad ficticia'),order['id'])
        self.command('resolve_issue',dict(version=1,reason='Revisión QA terminada'),issue['id'])
        row=self.current(order)['issues'][0];self.assertEqual(row['resolved_by'],'socio-qa');self.assertEqual(row['resolution'],'Revisión QA terminada')
        self.assertEqual(self.ledger(),[])

    def test_repeat_schema_preserves_receipts_and_ledger(self):
        order=self.order();self.receive(order);before=self.snapshot()
        self.store=Store(self.store.path);self.assertEqual(before,self.snapshot())

    def test_sii_historical_import_and_retry_never_add_ingredient_stock(self):
        from erp import sii_purchases as sii
        from test_sii_purchases import csv_file,document
        order=self.order();self.receive(order);before=self.ledger()
        batch=sii.upload(self.store,csv_file([document()]),'historico-ficticio.csv','QA')
        selected=dict(rows=[2],confirmed=True)
        self.assertEqual(sii.confirm(self.store,batch['id'],selected,'QA')['imported'],1)
        self.assertEqual(sii.confirm(self.store,batch['id'],selected,'QA')['already_imported'],1)
        self.assertEqual(before,self.ledger())


class SupplierDirectoryTests(unittest.TestCase):
    setUp=ProcurementTests.setUp
    command=ProcurementTests.command
    finance=ProcurementTests.finance

    def profile(self):
        with self.store.connect() as db:return next(r for r in directory.list_profiles(db) if r['id']==self.supplier['id'])

    def body(self,**changes):
        current=self.profile()
        data=dict(supplier_version=current['version'],profile_version=current['profile']['version'],reviewed=False,reason='Fuente ficticia QA')
        data.update(changes);return data

    def test_missing_fields_stay_null_and_contact_terms_are_reused(self):
        before=self.profile();self.command('directory',self.body(products_text='Lácteos ficticios'),self.supplier['id'])
        after=self.profile()
        self.assertEqual((after['contact'],after['terms']),(before['contact'],before['terms']))
        for field in ('order_days','cutoff_time','delivery_days','minimum_amount_clp','minimum_quantity','payment_term_days','substitute_ids'):
            self.assertIsNone(after['profile'][field])
        self.assertFalse(after['profile']['alerts_enabled']);self.assertEqual(after['tax_id'],'')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_suppliers').fetchone()[0],1)

    def test_multiple_order_and_delivery_days_are_independent_no_inferred_lead(self):
        self.command('directory',self.body(order_days=[0,3],cutoff_time='11:30',delivery_days=[1,4],reviewed=True),self.supplier['id'])
        profile=self.profile()['profile']
        self.assertEqual((profile['order_days'],profile['delivery_days']),([0,3],[1,4]))
        self.assertIsNone(profile['delivery_lead_days']);self.assertIsNone(profile['schedule_relationship']);self.assertFalse(profile['alerts_enabled'])

    def test_explicit_contact_change_preserves_bank_and_audits_existing_provider(self):
        original=self.finance('supplier',dict(self.supplier,bank='Banco ficticio',account_number='QA-ONLY',reason='Ficha bancaria sintética'),self.supplier['id'])
        self.command('directory',self.body(contact='Contacto corregido QA',terms='30 días explícitos',payment_term_days=30),self.supplier['id'])
        with self.store.connect() as db:
            supplier=ap.record(db,'ap_suppliers',self.supplier['id']);event=dict(db.execute("SELECT * FROM pc_events WHERE entity='supplier'").fetchone())
        self.assertEqual((supplier['bank'],supplier['account_number']),('Banco ficticio','QA-ONLY'))
        self.assertEqual(supplier['version'],original['version']+1);self.assertEqual(event['actor'],'socio-qa')
        self.assertEqual(json.loads(event['before_json'])['contact'],'Contacto existente QA')

    def test_invalid_days_time_minimum_and_substitute_are_atomic(self):
        for changes in ({'order_days':[True]},{'order_days':[0,0]},{'delivery_days':[7]},{'cutoff_time':'25:00'},{'minimum_amount_clp':True},{'minimum_quantity':'2'},{'minimum_quantity':'0.5','minimum_unit_id':self.bag['id']},{'substitute_ids':[self.supplier['id']]},{'substitute_ids':[999]},{'reviewed':'yes'}):
            with self.subTest(changes=changes),self.assertRaises(DomainError):self.command('directory',self.body(**changes),self.supplier['id'])
        self.assertEqual(self.profile()['profile']['version'],0)

    def test_stale_profile_or_contact_version_blocks_overwrite(self):
        data=self.body(products_text='Texto inicial');self.command('directory',data,self.supplier['id'])
        with self.assertRaises(DomainError):self.command('directory',data,self.supplier['id'])
        data=self.body(products_text='Texto nuevo')
        self.finance('supplier',dict(self.supplier,contact='Cambio concurrente',reason='QA'),self.supplier['id'])
        with self.assertRaises(DomainError):self.command('directory',data,self.supplier['id'])
        self.assertEqual(self.profile()['profile']['products_text'],'Texto inicial')

    def test_substitutes_link_existing_ids_without_creating_suppliers(self):
        other=self.finance('supplier',dict(name='Sustituto existente QA'))
        self.command('directory',self.body(substitute_ids=[other['id']],minimum_quantity='2',minimum_unit_id=self.bag['id'],minimum_amount_clp=25000),self.supplier['id'])
        profile=self.profile()['profile'];self.assertEqual(profile['substitute_ids'],[other['id']]);self.assertEqual(profile['minimum_quantity'],'2')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_suppliers').fetchone()[0],2)

    def test_preview_is_read_only_with_nulls_and_ambiguous_names(self):
        self.finance('supplier',dict(name=self.supplier['name']))
        with self.store.connect() as db:
            before=db.total_changes
            value=directory.preview(db,{'rows':[{'supplier_name':self.supplier['name']},{'supplier_name':'Desconocido QA','products_text':'Verduras'}]})
            self.assertEqual(db.total_changes,before)
        self.assertEqual([r['status'] for r in value['rows']],['ambiguous','unmatched'])
        self.assertIsNone(value['rows'][0]['tax_id']);self.assertIsNone(value['rows'][1]['proposed']['minimum_amount_clp'])
        self.assertFalse(value['automatic_import']);self.assertFalse(value['alerts_enabled'])


if __name__=='__main__':unittest.main()
