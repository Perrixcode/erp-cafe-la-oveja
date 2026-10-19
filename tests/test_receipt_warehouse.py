"""Optional entries, concurrent retries and compatibility use only fictional SQLite."""
import threading
import unittest
from uuid import uuid4
from erp import procurement as pc, supply_planning as sp
from erp.domain import DomainError
import test_procurement as fixtures


class WarehouseTests(unittest.TestCase):
    for method in ('setUp','command','finance','order','current','receipt_body','receive','ledger','snapshot','invoice'):
        locals()[method]=getattr(fixtures.ProcurementTests,method)

    def entry_body(self,order,receipt,quantity='1',**changes):
        order=self.current(order);r=next(r for r in order['receipts'] if r['id']==receipt['id'])
        body=dict(version=r['version'],order_version=order['version'],warehouse_id=self.warehouse['id'],occurred_on='2026-10-14',confirmed=True,reason='Ingreso físico ficticio',lines=[dict(receipt_line_id=r['lines'][0]['id'],unit_id=self.bag['id'],quantity=quantity)])
        body.update(changes);return body

    def enter(self,o,r,quantity='1',**changes):return self.command('warehouse_entry',self.entry_body(o,r,quantity,**changes),r['id'])

    def test_receipt_is_informational_and_partial_full_entries_do_not_touch_finance(self):
        o=self.order();before=self.snapshot(financial=True);r=self.receive(o,accepted='3',rejected='1')
        self.assertIsNone(r['stock_document_id']);self.assertEqual(self.ledger(),[])
        self.assertEqual(self.current(o)['receipts'][0]['warehouse_state'],'pending')
        self.enter(o,r);q=self.current(o)['receipts'][0]
        self.assertEqual((q['warehouse_state'],q['lines'][0]['entered'],q['lines'][0]['warehouse_pending']),('partial','1','2'))
        self.enter(o,r,'2');q=self.current(o)['receipts'][0]
        self.assertEqual(q['warehouse_state'],'entered');self.assertEqual(sum(l['quantity_micros'] for l in self.ledger()),75*pc.SCALE)
        self.assertEqual(before,self.snapshot(financial=True))

    def test_idempotent_retry_body_conflict_and_stale_new_request(self):
        o=self.order();r=self.receive(o);body=dict(self.entry_body(o,r),request_id=str(uuid4()))
        one=pc.execute(self.store,'warehouse_entry',body,'QA',r['id']);snapshot=self.snapshot()
        self.assertEqual(one,pc.execute(self.store,'warehouse_entry',body,'QA',r['id']));self.assertEqual(snapshot,self.snapshot())
        for other in (dict(body,reason='Otro'),dict(body,request_id=str(uuid4()))):
            with self.assertRaises(DomainError):pc.execute(self.store,'warehouse_entry',other,'QA',r['id'])

    def test_concurrent_new_requests_only_one_entry_commits(self):
        o=self.order();r=self.receive(o);body=self.entry_body(o,r,'2');barrier=threading.Barrier(2);out=[]
        def worker():
            barrier.wait()
            try:self.command('warehouse_entry',body,r['id']);out.append(201)
            except DomainError as error:out.append(error.status)
        workers=[threading.Thread(target=worker) for _ in range(2)]
        for t in workers:t.start()
        for t in workers:t.join()
        self.assertCountEqual(out,[201,409]);self.assertEqual(len(self.ledger()),1)

    def test_units_quantities_duplicate_lines_foreign_lines_dates_and_confirmation(self):
        o=self.order();r=self.receive(o);body=self.entry_body(o,r);line=body['lines'][0]
        invalid=[dict(body,lines=[dict(line,quantity=q)]) for q in ('4','0','-1','0.5','NaN',1,True)]
        invalid += [dict(body,lines=[line,line]),dict(body,lines=[dict(line,unit_id=self.kg['id'])]),dict(body,lines=[dict(line,receipt_line_id=999)]),dict(body,confirmed=False),dict(body,occurred_on='2026-10-13'),dict(body,occurred_on='2026-10-15'),dict(body,warehouse_id=999)]
        for value in invalid:
            before=self.snapshot()
            with self.subTest(value=value),self.assertRaises(DomainError):self.command('warehouse_entry',value,r['id'])
            self.assertEqual(before,self.snapshot())

    def test_destination_and_reversal_only_remove_entered_quantities(self):
        o=self.order();r=self.receive(o,accepted='5');other=self.command('warehouse',{'name':'Otra bodega QA'})
        self.enter(o,r,'1');self.enter(o,r,'2',warehouse_id=other['id'])
        current=self.current(o);q=current['receipts'][0]
        result=self.command('reverse_receipt',dict(version=q['version'],order_version=current['version'],reason='Reverso ficticio'),r['id'])
        self.assertEqual(len(result['stock_reversal_ids']),2)
        self.assertEqual(sum(l['quantity_micros'] for l in self.ledger()),0)
        self.assertEqual(sorted(l['quantity_micros'] for l in self.ledger()),[-50*pc.SCALE,-25*pc.SCALE,25*pc.SCALE,50*pc.SCALE])
        with self.assertRaises(DomainError):self.enter(o,r)

    def test_unentered_receipt_reverse_has_no_stock_movement(self):
        o=self.order();r=self.receive(o);current=self.current(o)
        result=self.command('reverse_receipt',dict(version=1,order_version=current['version'],reason='Informativa errónea'),r['id'])
        self.assertEqual(result['stock_reversal_ids'],[]);self.assertEqual(self.ledger(),[])

    def test_historical_receipt_is_already_entered_without_rewriting_it(self):
        o=self.order();r=self.receive(o)
        with self.store.connect() as db:
            line=db.execute('SELECT id FROM pc_receipt_lines WHERE receipt_id=?',(r['id'],)).fetchone()[0]
            pc.stock_receipt(db,o,r['id'],[(o['lines'][0],line,3*pc.SCALE)],'Legacy QA','2026-10-14','Entrada histórica ficticia')
        before=self.ledger();q=self.current(o)['receipts'][0]
        self.assertEqual((q['warehouse_state'],q['lines'][0]['warehouse_pending']),('entered','0'));self.assertEqual(before,self.ledger())
        with self.assertRaises(DomainError):self.enter(o,r)
        current=self.current(o);self.command('reverse_receipt',dict(version=1,order_version=current['version'],reason='Reverso legado'),r['id'])
        self.assertEqual(sum(l['quantity_micros'] for l in self.ledger()),0)

    def test_count_after_entry_blocks_reversal_and_generic_reverse_is_blocked(self):
        o=self.order();r=self.receive(o);entry=self.enter(o,r)
        with self.assertRaises(DomainError):self.command('stock_reverse',{'reason':'Reverso por vía incorrecta'},entry['stock_document_id'])
        with self.store.connect() as db:p=sp.position(db,self.item['id'],self.warehouse['id'])
        self.command('stock',dict(kind='count',item_id=self.item['id'],warehouse_id=self.warehouse['id'],quantity='25',stock_token=p['token'],reason='Conteo posterior',confirmed=True))
        current=self.current(o)
        with self.assertRaises(DomainError):self.command('reverse_receipt',dict(version=2,order_version=current['version'],reason='Reverso antiguo'),r['id'])

    def test_receipt_before_count_is_allowed_but_later_entry_is_blocked(self):
        o=self.order();r=self.receive(o,received_on='2026-10-13')
        with self.store.connect() as db:p=sp.position(db,self.item['id'],self.warehouse['id'])
        self.command('stock',dict(kind='count',item_id=self.item['id'],warehouse_id=self.warehouse['id'],quantity='75',stock_token=p['token'],reason='Conteo posterior',confirmed=True))
        with self.assertRaises(DomainError):self.enter(o,r)

    def test_price_history_is_independent_and_not_duplicated_by_stock(self):
        o=self.order();i=self.invoice();self.command('invoice_link',dict(version=1,invoice_id=i['id'],invoice_version=1,reason='Vínculo ficticio'),o['id'])
        r=self.receive(self.current(o));o=self.current(o)
        self.command('price',dict(order_line_id=o['lines'][0]['id'],order_version=o['version'],invoice_id=i['id'],invoice_version=1,quantity='3',amount_clp=3000,basis='gross',reason='Precio cotejado ficticio',confirmed=True))
        before=pc.overview(self.store)['prices'];self.enter(o,r);self.enter(o,r,'2')
        self.assertEqual(pc.overview(self.store)['prices'],before)

    def test_stock_overflow_rolls_back_entry_and_receipt_version(self):
        o=self.order();r=self.receive(o)
        with self.store.connect() as db:
            doc=db.execute("INSERT INTO pc_stock_documents(kind,occurred_on,reason,actor,created_at) VALUES('adjustment','2026-10-14','QA','QA','QA')").lastrowid
            db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros) VALUES(?,?,?,?,?)',(doc,self.item['id'],self.warehouse['id'],self.kg['id'],pc.MAX_QUANTITY))
        before=self.snapshot()
        with self.assertRaises(DomainError):self.enter(o,r)
        self.assertEqual(before,self.snapshot())
