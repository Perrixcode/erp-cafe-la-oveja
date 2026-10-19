"""Synthetic stock, reconciliation and replenishment regressions."""
import unittest
from uuid import uuid4
import test_procurement as fixtures
from erp import procurement as pc,supply_planning as sp,payables as ap
from erp.domain import DomainError

class PlanningTests(unittest.TestCase):
    setUp=fixtures.ProcurementTests.setUp
    command=fixtures.ProcurementTests.command
    finance=fixtures.ProcurementTests.finance
    order=fixtures.ProcurementTests.order
    current=fixtures.ProcurementTests.current
    invoice=fixtures.ProcurementTests.invoice
    receipt_body=fixtures.ProcurementTests.receipt_body
    receive=fixtures.ProcurementTests.receive
    snapshot=fixtures.ProcurementTests.snapshot
    ledger=fixtures.ProcurementTests.ledger
    def stock(self,kind='count',quantity='100',warehouse=None,**changes):
        warehouse=warehouse or self.warehouse['id']
        with self.store.connect() as db:p=sp.position(db,self.item['id'],warehouse)
        values=dict(kind=kind,item_id=self.item['id'],warehouse_id=warehouse,quantity=quantity,stock_token=p['token'],reason='Revisión física ficticia',confirmed=True);values.update(changes)
        return self.command('stock',values)
    def price(self,order,invoice,**changes):
        values=dict(order_line_id=order['lines'][0]['id'],order_version=order['version'],invoice_id=invoice['id'],invoice_version=invoice['version'],quantity='2',amount_clp=5000,basis='net',reason='Línea ficticia revisada',confirmed=True);values.update(changes);return self.command('price',values)
    def policy(self,**changes):
        values=dict(item_id=self.item['id'],warehouse_id=self.warehouse['id'],supplier_id=self.supplier['id'],unit_id=self.bag['id'],minimum='50',target='100',daily='10',lead_days=3,version=0,reason='Consumo y plazo ficticios revisados');values.update(changes);return self.command('policy',values)
    def test_unknown_stock_blocks_outflows_and_suggestions(self):
        self.policy();before=self.snapshot(financial=True)
        with self.assertRaises(DomainError):self.stock('issue','1')
        row=pc.overview(self.store)['suggestions'][0];self.assertIsNone(row['stock']);self.assertIsNone(row['quantity']);self.assertEqual(before,self.snapshot(financial=True))
    def test_zero_count_is_explicit_verified_and_does_not_invent_stock(self):
        self.stock(quantity='0');p=pc.overview(self.store)['positions'][0];self.assertTrue(p['verified']);self.assertEqual(p['quantity'],'0');self.assertEqual(len(self.ledger()),0)
    def test_count_reconciles_net_without_doubling_prior_receipts(self):
        self.receive(self.order(),accepted='2');self.stock(quantity='40');self.assertEqual(sum(l['quantity_micros'] for l in self.ledger()),40*pc.SCALE)
    def test_stock_concurrency_and_underflow_are_atomic(self):
        self.stock(quantity='10')
        with self.store.connect() as db:token=sp.position(db,self.item['id'],self.warehouse['id'])['token']
        self.stock('issue','3');before=self.ledger()
        for changes in ({'stock_token':token},{'quantity':'8'},{'quantity':'0.0001'}):
            with self.assertRaises(DomainError):self.stock('issue',**dict({'quantity':'1'},**changes))
            self.assertEqual(before,self.ledger())
    def test_transfer_balanced_atomic_and_reversible(self):
        self.stock();other=self.command('warehouse',{'name':'Destino ficticio'})
        self.stock(quantity='0',warehouse=other['id'])
        with self.store.connect() as db:p=sp.position(db,self.item['id'],other['id'])
        transfer=self.stock('transfer','20',destination_id=other['id'],destination_token=p['token'])
        self.assertEqual(sum(r['quantity_micros'] for r in self.ledger()),100*pc.SCALE)
        self.command('stock_reverse',{'reason':'Reverso ficticio'},transfer['id'])
        self.assertEqual([p['quantity'] for p in pc.overview(self.store)['positions']],['100','0'])
        with self.assertRaises(DomainError):self.command('stock_reverse',{'reason':'Reintento distinto'},transfer['id'])
    def test_destination_unknown_or_same_blocks_transfer(self):
        self.stock();other=self.command('warehouse',{'name':'Destino sin contar'})
        with self.store.connect() as db:p=sp.position(db,self.item['id'],other['id'])
        for dest in (other['id'],self.warehouse['id']):
            with self.assertRaises(DomainError):self.stock('transfer','1',destination_id=dest,destination_token=p['token'])
    def test_waste_adjustment_and_reversal_do_not_change_finance_or_cake_stock(self):
        before=self.snapshot(financial=True);self.stock();self.stock('waste','5');r=self.stock('adjustment','2',direction='out');self.command('stock_reverse',{'reason':'Corrección ficticia'},r['id']);self.assertEqual(pc.overview(self.store)['positions'][0]['quantity'],'95');self.assertEqual(before,self.snapshot(financial=True))
    def test_count_after_movement_prevents_reversing_old_basis(self):
        self.stock();move=self.stock('issue','2');self.stock(quantity='98')
        with self.assertRaises(DomainError):self.command('stock_reverse',{'reason':'Reverso antiguo'},move['id'])
    def test_count_after_receipt_prevents_old_reversal_and_backdated_receipt(self):
        order=self.order();receipt=self.receive(order);current=self.current(order);line=current['receipts'][0]['lines'][0]
        self.command('warehouse_entry',dict(version=1,order_version=current['version'],warehouse_id=self.warehouse['id'],occurred_on='2026-10-14',reason='Ingreso ficticio',confirmed=True,lines=[dict(receipt_line_id=line['id'],unit_id=self.bag['id'],quantity='3')]),receipt['id'])
        self.stock(quantity='70');current=self.current(order)
        with self.assertRaises(DomainError):self.command('reverse_receipt',{'version':2,'order_version':current['version'],'reason':'Reverso antiguo'},receipt['id'])
        self.receive(current,received_on='2026-10-13',reference='OTRA')
    def linked(self):
        o=self.order();i=self.invoice();self.command('invoice_link',dict(version=o['version'],invoice_id=i['id'],invoice_version=i['version'],reason='Referencia ficticia'),o['id']);return self.current(o),i
    def test_prices_use_explicit_equivalence_and_reconcile_received_quantities(self):
        o,i=self.linked();self.receive(o,accepted='2');o=self.current(o);before=self.ledger();self.price(o,i)
        data=pc.overview(self.store);self.assertEqual(data['prices'][0]['unit_price'],'100.0000');self.assertEqual(data['prices'][0]['base_quantity'],'50');self.assertEqual(data['matching'][0]['status'],'matched');self.assertEqual(before,self.ledger())
    def test_price_mismatch_is_reported_not_silently_accepted_as_match(self):
        o,i=self.linked();self.price(o,i);self.assertEqual(pc.overview(self.store)['matching'][0]['status'],'mismatch')
    def test_price_rejects_unlinked_wrong_supplier_over_total_and_duplicate(self):
        o=self.order();i=self.invoice()
        with self.assertRaises(DomainError):self.price(o,i)
        self.command('invoice_link',dict(version=1,invoice_id=i['id'],invoice_version=1,reason='Vínculo'),o['id']);o=self.current(o)
        with self.assertRaises(DomainError):self.price(o,i,amount_clp=10001)
        self.price(o,i)
        with self.assertRaises(DomainError):self.price(o,i)
    def test_payment_does_not_stale_price_but_invoice_edit_does(self):
        o,i=self.linked();self.price(o,i)
        self.finance('payment',dict(version=i['version'],amount=10,paid_on='2026-10-14',method='cash',reference='QA',confirmed=True),i['id'])
        self.assertTrue(pc.overview(self.store)['prices'][0]['valid'])
        with self.store.connect() as db:current=ap.record(db,'ap_invoices',i['id'])
        self.finance('invoice',dict(current,folio='NEW-F-QA',reason='Corrección ficticia'),i['id'])
        self.assertFalse(pc.overview(self.store)['prices'][0]['valid'])
    def test_void_line_preserves_history_and_allows_replacement(self):
        o,i=self.linked();p=self.price(o,i);self.command('price_void',{'reason':'Corrección de línea'},p['id']);self.price(o,i,amount_clp=4000);rows=pc.overview(self.store)['prices'];self.assertEqual(len(rows),2);self.assertEqual(sum(r['valid'] for r in rows),1)
    def test_replenishment_formula_rounds_up_purchase_pack(self):
        self.stock(quantity='60');self.policy();s=pc.overview(self.store)['suggestions'][0];self.assertEqual((s['stock'],s['projected'],s['quantity']),('60','30','3'))
    def test_pending_deliveries_reduce_suggestion_and_unknown_dates_block(self):
        self.stock(quantity='60');self.policy();o=self.order(expected_on='2026-10-15',lines=[dict(item_id=self.item['id'],version=2,unit_id=self.bag['id'],quantity='2')]);s=pc.overview(self.store)['suggestions'][0];self.assertEqual(s['status'],'covered')
        self.order(expected_on='');self.assertIsNone(pc.overview(self.store)['suggestions'][0]['quantity'])
    def test_policy_stale_and_negative_consumption_rejected(self):
        self.policy()
        with self.assertRaises(DomainError):self.policy()
        with self.assertRaises(DomainError):self.policy(version=1,daily='-1')
    def test_dashboard_excludes_unvalidated_balance_from_due_totals(self):
        i=self.invoice()
        with self.store.connect() as db:db.execute('UPDATE ap_invoices SET payment_review=1,due_on=? WHERE id=?',('2026-10-13',i['id']))
        p=pc.overview(self.store)['priorities'];self.assertEqual(p['overdue'],0);self.assertEqual(p['review'],1)
    def test_duplicate_delivery_across_orders_is_blocked(self):
        one=self.order();two=self.order();self.receive(one)
        with self.assertRaises(DomainError):self.receive(two)
    def test_late_future_delivery_and_unverified_monetary_minimum_block_proposal(self):
        self.stock(quantity='20');self.policy();self.order(expected_on='2026-11-01');self.assertIsNone(pc.overview(self.store)['suggestions'][0]['quantity'])
    def test_directory_banking_contact_channel_share_audited_supplier(self):
        result=self.command('directory',dict(supplier_version=1,profile_version=0,reviewed=False,reason='Campos ficticios revisados',bank='Banco ficticio',account_number='NO-REAL-QA',account_type='Ficticia',account_holder='Titular ficticio',account_tax_id='',contact='Vendedor ficticio',phone='No real',order_channel='Portal ficticio',order_contact='Equipo QA',order_url='https://example.invalid/pedidos'),self.supplier['id'])
        self.assertEqual(result['bank'],'Banco ficticio');self.assertEqual(result['profile']['order_contact'],'Equipo QA')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_suppliers').fetchone()[0],1);self.assertTrue(db.execute("SELECT 1 FROM pc_events WHERE entity='supplier'").fetchone())
        with self.assertRaises(DomainError):self.command('directory',dict(supplier_version=result['version'],profile_version=1,reviewed=False,reason='URL inválida',order_url='javascript:alert(1)'),self.supplier['id'])
    def test_price_comparison_uses_latest_per_supplier_and_keeps_tax_bases_separate(self):
        o,i=self.linked();self.price(o,i)
        other=self.finance('supplier',{'name':'Segundo proveedor ficticio'})
        second=self.order(supplier_id=other['id'],supplier_version=1);invoice=self.invoice(folio='SECOND',supplier=other['id'])
        self.command('invoice_link',dict(version=1,invoice_id=invoice['id'],invoice_version=1,reason='Vínculo ficticio'),second['id']);second=self.current(second);self.price(second,invoice,quantity='4')
        rows=pc.overview(self.store)['comparisons'][0]['rows'];self.assertEqual([r['unit_price'] for r in rows],['50.0000','100.0000']);self.assertEqual(rows[0]['supplier_id'],other['id'])
    def test_directory_name_and_notes_are_not_lost_when_profile_is_saved(self):
        r=self.command('directory',dict(supplier_version=1,profile_version=0,name='Nombre corregido ficticio',notes='Nota conservada',reviewed=False,reason='Corrección ficticia'),self.supplier['id'])
        self.assertEqual(r['name'],'Nombre corregido ficticio');self.assertEqual(r['notes'],'Nota conservada')
        r=self.command('directory',dict(supplier_version=r['version'],profile_version=1,reviewed=False,reason='Solo operación'),self.supplier['id'])
        self.assertEqual(r['notes'],'Nota conservada')
