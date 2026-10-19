"""Fictional invoices and purchasing intentions; temporary DBs, no integrations."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from erp.store import Store
from erp import payables as ap,procurement as pc,document_intake as di,invoice_ocr
from erp.domain import DomainError

class ReviewAgendaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'synthetic.sqlite3')
        clock=patch.object(ap,'local_day',return_value='2026-10-14');clock.start();self.addCleanup(clock.stop)
        self.supplier=self.ap('supplier',dict(name='Frutas Imaginarias SpA'))
        self.kg=self.pc('unit',dict(code='kg',name='Kilo ficticio',dimension='mass',precision=3))
        self.box=self.pc('unit',dict(code='caja',name='Caja ficticia',dimension='package',precision=0))
        self.item=self.pc('item',dict(code='PULPA-QA',name='Pulpa ficticia',base_unit_id=self.kg['id']))
        self.pc('equivalence',dict(item_id=self.item['id'],version=1,unit_id=self.box['id'],base_quantity='5',reason='Etiqueta ficticia 5x1 kg'))
        self.warehouse=self.pc('warehouse',dict(name='Bodega ficticia'))
    def ap(self,action,data,target=None):return ap.execute(self.store,action,dict(data,request_id=data.get('request_id',str(uuid4()))),'QA',target)
    def pc(self,action,data,target=None):return pc.execute(self.store,action,dict(data,request_id=data.get('request_id',str(uuid4()))),'QA',target)
    def draft(self):
        with patch.object(di,'extract',return_value=('Folio: 800\nTotal: $ 11.900','extracted','Ficticio')):return di.upload(self.store,b'%PDF-1.4\nFICTICIO\n%%EOF','ficticio.pdf','QA')
    def fields(self):return dict(supplier_id=self.supplier['id'],document_type='invoice',folio='800',amount=11900,issued_on='2026-10-13',condition='cash')
    def review(self,mapped=True):
        line=dict(code='FI-10',description='Pulpa ficticia caja 5x1 kg',format='Caja 5x1 kg',quantity='1',unit_price='10000',discount='0',amount_clp=10000)
        if mapped:line.update(item_id=self.item['id'],item_version=2,unit_id=self.box['id'])
        return dict(lines=[line],basis='net',net=10000,tax=1900,lines_confirmed=True)
    def confirm(self,draft,review=None,**changes):
        data=dict(version=draft['version'],invoice=self.fields(),confirmed=True,reason='Original ficticio cotejado',review=review if review is not None else self.review());data.update(changes)
        return self.ap('document_confirm',data,draft['id'])
    def agenda(self,**changes):
        data=dict(supplier_id=self.supplier['id'],scheduled_on='2026-10-14',expected_on='2026-10-15',reason='Plan ficticio',lines=[dict(description='Pulpa ficticia caja',quantity='2',item_id=self.item['id'],unit_id=self.box['id'])]);data.update(changes)
        return self.pc('agenda_save',data)
    def test_ocr_preserves_pack_quantity_and_labels(self):
        text='Frutas Imaginarias SpA\nFACTURA AFECTA ELECTRÓNICA\nN° 800\nFecha Emisión : 13-10-2026\nVencimiento: 20-10-2026\nForma Pago: Contado\nFI100  Pulpa ficticia caja 5x1 Kilo  CON  1,00  10.000,00  10.000  10.000\nMonto Neto $ 10.000\nMonto I.V.A (19%) $ 1.900\nMonto Total $ 11.900'
        s=invoice_ocr.suggest(text);self.assertEqual(s['folio'],'800');self.assertEqual(s['amount'],11900);self.assertEqual(s['issued_on'],'2026-10-13');self.assertEqual(s['condition'],'cash');self.assertEqual(s['lines'][0]['quantity'],'1.00');self.assertIn('5x1',s['lines'][0]['format']);self.assertTrue(s['lines'][0]['needs_review'])
    def test_incomplete_review_saved_without_debt_and_reextract_replays(self):
        draft=self.draft();payload={'invoice':{'folio':'800'},'lines':[{'description':'Por completar'}]}
        self.ap('document_save',dict(version=1,review=payload,reason='Avance ficticio'),draft['id'])
        with self.store.connect() as db:r=di.draft_view(db,draft['id'])
        self.assertEqual(r['review'],payload);self.assertEqual(r['version'],2);self.assertEqual(ap.view(self.store)['invoices'],[])
        body=dict(version=2,reason='Releer',request_id=str(uuid4()))
        with patch.object(di,'extract',return_value=('Folio: 801','extracted','Ficticio')) as reader:
            one=di.reextract(self.store,draft['id'],body,'QA');self.assertEqual(one,di.reextract(self.store,draft['id'],body,'QA'));self.assertEqual(reader.call_count,1)
        with self.store.connect() as db:self.assertEqual(di.draft_view(db,draft['id'])['review'],payload)
    def test_review_transaction_rejects_bad_math_and_rolls_back_invoice(self):
        draft=self.draft()
        for key,value in [('quantity','5'),('amount_clp',9999),('discount','1')]:
            review=self.review();review['lines'][0][key]=value
            with self.assertRaises(DomainError):self.confirm(draft,review)
        review=self.review();review['tax']=1800
        with self.assertRaises(DomainError):self.confirm(draft,review)
        self.assertEqual(ap.view(self.store)['invoices'],[])
    def test_prices_and_order_from_document_do_not_receive_or_pay(self):
        draft=self.draft();self.confirm(draft);v=pc.overview(self.store)
        self.assertEqual(v['prices'][0]['base_quantity'],'5');self.assertEqual(v['prices'][0]['unit_price'],'2000.0000');self.assertEqual(v['movements'],[])
        invoice=ap.view(self.store)['invoices'][0];self.assertEqual(invoice['paid'],0)
        body=dict(version=2,invoice_version=invoice['version'],supplier_version=1,item_versions={str(self.item['id']):2},warehouse_id=self.warehouse['id'],ordered_on='2026-10-13',confirmed=True,reason='Pedido ficticio',request_id=str(uuid4()))
        one=self.pc('document_order',body,draft['id']);self.assertEqual(one,self.pc('document_order',body,draft['id']))
        v=pc.overview(self.store);self.assertEqual(len(v['orders']),1);self.assertEqual(v['orders'][0]['lines'][0]['ordered'],'1');self.assertEqual(v['matching'][0]['invoiced'],'1');self.assertEqual(v['movements'],[]);self.assertEqual(v['orders'][0]['receipts'],[])
        with self.assertRaises(DomainError):self.pc('document_order',dict(body,request_id=str(uuid4()),version=3),draft['id'])
    def test_map_after_registration_and_changed_invoice_invalidates_prices(self):
        draft=self.draft();self.confirm(draft,self.review(False));self.assertEqual(pc.overview(self.store)['prices'],[])
        with self.store.connect() as db:r=di.draft_view(db,draft['id'])
        invoice=ap.view(self.store)['invoices'][0]
        self.ap('document_map',dict(version=2,invoice_version=invoice['version'],confirmed=True,reason='Equivalencia ficticia',mappings=[dict(line_id=r['lines'][0]['id'],item_id=self.item['id'],item_version=2,unit_id=self.box['id'])]),draft['id'])
        self.assertTrue(pc.overview(self.store)['prices'][0]['valid'])
        self.ap('invoice',dict(self.fields(),amount=12000,version=invoice['version'],reason='Cambio ficticio'),invoice['id'])
        self.assertFalse(pc.overview(self.store)['prices'][0]['valid'])
    def test_duplicate_file_and_csv_invoice_cannot_add_lines_twice(self):
        draft=self.draft();invoice=self.ap('invoice',self.fields());self.confirm(draft,existing_invoice_id=invoice['id'],invoice_version=invoice['version'])
        with patch.object(di,'extract',return_value=('','empty','Ficticio')):other=di.upload(self.store,b'%PDF-1.4\nOTRO\n%%EOF','otro.pdf','QA')
        with self.assertRaises(DomainError):self.confirm(other,existing_invoice_id=invoice['id'],invoice_version=invoice['version'])
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_availability_never_creates_daily_intentions(self):
        self.pc('directory',dict(supplier_version=1,profile_version=0,order_days=[0,1,2,3,4],reviewed=False,reason='Horario ficticio'),self.supplier['id'])
        for _ in range(3):self.assertEqual(pc.overview(self.store)['agenda'],[])
        self.assertEqual(len(pc.overview(self.store)['orders']),0)
    def test_agenda_replay_edit_postpone_history_and_conflict(self):
        req=str(uuid4());one=self.agenda(request_id=req);self.assertEqual(one,self.agenda(request_id=req))
        moved=self.pc('agenda_postpone',dict(version=1,scheduled_on='2026-10-16',expected_on='2026-10-17',reason='Cambio ficticio'),one['id'])
        self.assertEqual(moved['origin_on'],'2026-10-14');self.assertEqual(moved['status'],'postponed');self.assertEqual(len(moved['history']),2)
        with self.assertRaises(DomainError):self.pc('agenda_postpone',dict(version=1,scheduled_on='2026-10-17',reason='stale'),one['id'])
        with self.assertRaises(DomainError):self.agenda(scheduled_on='2026-10-16',expected_on='2026-10-17')
    def test_agenda_order_link_is_single_and_done_requires_external_evidence(self):
        r=self.agenda();body=dict(version=1,supplier_version=1,item_versions={str(self.item['id']):2},warehouse_id=self.warehouse['id'],confirmed=True,reason='Preparar ficticio',request_id=str(uuid4()))
        linked=self.pc('agenda_order',body,r['id']);self.assertEqual(linked,self.pc('agenda_order',body,r['id']));self.assertEqual(linked['status'],'pending')
        with self.assertRaises(DomainError):self.pc('agenda_done',dict(version=2,completed_on='2026-10-14',confirmed=True,reason='Ficticio'),r['id'])
        done=self.pc('agenda_done',dict(version=2,completed_on='2026-10-14',external_reference='Referencia ficticia enviada por QA',confirmed=True,reason='Ficticio'),r['id'])
        self.assertEqual(done['status'],'done');self.assertEqual(pc.overview(self.store)['movements'],[]);self.assertEqual(ap.view(self.store)['invoices'],[])
    def test_unmapped_agenda_lines_do_not_prepare_order(self):
        r=self.agenda(lines=[dict(description='Sin catálogo',quantity='2',item_id=None,unit_id=None)])
        with self.assertRaises(DomainError):self.pc('agenda_order',dict(version=1,reason='Ficticio'),r['id'])
        self.assertEqual(pc.overview(self.store)['orders'],[])


    def test_repeated_item_lines_prepare_one_order_line_without_losing_detail(self):
        draft=self.draft();review=self.review()
        review['lines'][0].update(unit_price='5000',amount_clp=5000)
        review['lines'].append(dict(review['lines'][0],code='FI-11'))
        self.confirm(draft,review)
        invoice=ap.view(self.store)['invoices'][0]
        self.pc('document_order',dict(version=2,invoice_version=invoice['version'],supplier_version=1,item_versions={str(self.item['id']):2},warehouse_id=self.warehouse['id'],ordered_on='2026-10-13',confirmed=True,reason='Agrupación ficticia'),draft['id'])
        data=pc.overview(self.store)
        self.assertEqual(len(data['orders'][0]['lines']),1)
        self.assertEqual(data['orders'][0]['lines'][0]['ordered'],'2')
        self.assertEqual(data['matching'][0]['invoiced'],'2')


class LayoutTests(unittest.TestCase):
    def box(self,text,x,y,width=50,score=.99):
        # Fictional slanted table with four product rows.
        y=y-.04*x
        return [[[x-width/2,y-5],[x+width/2,y-5-.04*width],[x+width/2,y+5-.04*width],[x-width/2,y+5]],text,score]
    def test_layout_aligns_slanted_columns_and_decimal_price(self):
        from erp.ocr_layout import boxes_text
        heads=[('Codigo',50),('Articulo',200),('Cantidad',400),('P.Unitario',500),('Total',700)]
        rows=[self.box(t,x,100) for t,x in heads]
        for n in range(4):
            rows.extend(self.box(t,x,130+n*18) for t,x in [(str(880100+n),50),('Pulpa ficticia Caja 5x1 Kilo',200),('1.00',400),('10.000.00',500),('10.000',700)])
        parsed=invoice_ocr.suggest(boxes_text(rows))
        self.assertEqual(len(parsed['lines']),4)
        self.assertEqual([r['quantity'] for r in parsed['lines']],['1.00']*4)
        self.assertEqual([r['unit_price'] for r in parsed['lines']],['10000.00']*4)
    def test_low_confidence_numeric_cell_does_not_create_guessed_line(self):
        from erp.ocr_layout import boxes_text
        rows=[self.box(t,x,100) for t,x in [('Codigo',50),('Articulo',200),('Cantidad',400),('P.Unitario',500),('Total',700)]]
        rows.extend(self.box(t,x,130,score=.4 if x==400 else .99) for t,x in [('880100',50),('Pulpa ficticia',200),('1.00',400),('10.000.00',500),('10.000',700)])
        self.assertEqual(invoice_ocr.suggest(boxes_text(rows))['lines'][0]['quantity'],'')

    def test_non_numeric_type_column_is_not_a_second_quantity(self):
        from erp.ocr_layout import boxes_text
        rows=[self.box(t,x,100) for t,x in [('Codigo',50),('Articulo',200),('T/Prd',340),('Cantidad',400),('P.Unitario',500),('SubTotal',600),('Dscto.',650),('Total',700)]]
        rows.extend(self.box(t,x,130) for t,x in [('880100',50),('Pulpa ficticia',200),('CON',340),('1.00',400),('10.000.00',500),('10.000',600),('10.000',700)])
        self.assertEqual(invoice_ocr.suggest(boxes_text(rows))['lines'][0]['quantity'],'1.00')


    def test_dense_rows_do_not_merge_adjacent_totals(self):
        from erp.ocr_layout import boxes_text
        rows=[self.box(t,x,100) for t,x in [('Codigo',50),('Articulo',200),('T/Prd',340),('Cantidad',400),('P.Unitario',500),('SubTotal',600),('Total',700)]]
        for n in range(4):
            for text,x in [(str(880100+n),50),('Producto ficticio '+str(n),200),('CON',340),('1.00',400),('10.000.00',500),('10.000',600),('10.000',700)]:
                box=self.box(text,x,130+n*11+(3 if x>300 else 0));rows.append(box)
        self.assertEqual(len(invoice_ocr.suggest(boxes_text(rows))['lines']),4)
