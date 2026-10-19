"""Financial PDF contracts using fictional transactions and disposable SQLite only."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from pypdf import PdfReader

from erp import payables as ap, supplier_account as account, supplier_finance_pdf as pdf, sii_purchases as sii
from erp.store import Store


class FictionalAccounts:
    def __init__(self,store):self.store=store

    def command(self,action,data,target=None):
        return ap.execute(self.store,action,dict(data,request_id=str(uuid4())),'Socio ficticio QA',target)

    def supplier(self,name):
        return self.command('supplier',dict(name=name,bank='BANCO PRIVADO FICTICIO',account_number='CUENTA PRIVADA QA'))

    def invoice(self,supplier,folio,amount=10000,**extra):
        return self.command('invoice',dict(supplier_id=supplier['id'],document_type='invoice',folio=folio,amount=amount,
                            issued_on='2026-01-01',due_on='2026-01-30',condition='credit',**extra))

    def current(self,invoice):return next(i for i in ap.view(self.store)['invoices'] if i['id']==invoice['id'])

    def allocation(self,invoice,amount):return dict(invoice_id=invoice['id'],version=self.current(invoice)['version'],amount=amount)

    def payment(self,supplier,amount,applications=()):
        return self.command('account_payment',dict(supplier_id=supplier['id'],amount=amount,occurred_on='2026-01-02',
                            method='transfer',confirmed=True,applications=list(applications)))

    def mixed(self):
        self.a=self.supplier('Alimentos del Sur · FICTICIO');self.b=self.supplier('Envases & Café <QA> · FICTICIO')
        self.first=self.invoice(self.a,'1001',10000);self.second=self.invoice(self.a,'1002',6000)
        self.command('payment',dict(version=1,amount=2000,paid_on='2026-01-02',method='cash',confirmed=True),self.first['id'])
        self.p=self.payment(self.a,4000,[self.allocation(self.first,3000)])
        self.note=self.command('account_credit_note',dict(supplier_id=self.a['id'],amount=1500,occurred_on='2026-01-03',
                              folio='NC-QA-900',confirmed=True,origin_invoice_id=self.first['id'],origin_version=self.current(self.first)['version'],
                              applications=[self.allocation(self.first,1000)]))
        self.command('payment_hold',dict(version=self.current(self.second)['version'],hold=True,reason='Diferencia ficticia'),self.second['id'])
        self.payment(self.b,12000)
        self.paid=self.invoice(self.a,'1003',5000);self.payment(self.a,5000,[self.allocation(self.paid,5000)])
        self.voided=self.invoice(self.a,'1004',7000)
        self.command('void_invoice',dict(version=1,reason='Documento ficticio anulado'),self.voided['id'])
        batch=sii.upload(self.store,('Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'
                                   '1;33;Del Giro;76.333.333-3;Importación pendiente FICTICIA;9001;01/01/2026;800000\n').encode(),'sii-ficticio.csv','QA')
        sii.confirm(self.store,batch['id'],dict(rows=[2],confirmed=True),'QA')
        return self


def read_pdf(content):
    reader=PdfReader(BytesIO(content))
    return reader,[page.extract_text() for page in reader.pages]


class SupplierFinancePDFTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=Store(Path(tmp.name)/'ficticio.sqlite3');self.f=FictionalAccounts(self.store)

    def snapshot(self):
        with self.store.connect() as db:return '\n'.join(db.iterdump())

    def test_pending_totals_keep_credits_with_supplier_and_unknown_balances_separate(self):
        f=self.f.mixed();data=pdf.pending_data(self.store);groups={g['supplier_id']:g for g in data['groups']}
        a=groups[f.a['id']];b=groups[f.b['id']]
        self.assertEqual((a['amount'],a['paid'],a['credited'],a['balance'],a['available_credit'],a['payable']),
                         (16000,5000,1000,10000,1500,8500))
        self.assertEqual((b['balance'],b['available_credit'],b['in_favor']),(0,12000,12000))
        self.assertEqual(data['totals'],dict(amount=16000,paid=5000,credited=1000,balance=10000,available_credit=13500,
                                          payable=8500,in_favor=12000,invoices=2,review=1,suppliers=3))
        self.assertTrue(all(g['consistent'] for g in data['groups']))
        self.assertEqual([i['folio'] for i in a['invoices']],['1001','1002'])
        self.assertTrue(a['invoices'][1]['payment_hold'])
        self.assertEqual(account.statement(self.store,f.a['id'])['totals']['net_balance'],8500)

    def test_pdf_renders_canonical_rows_without_writes_or_bank_details(self):
        f=self.f.mixed();before=self.snapshot()
        statement=account.statement(self.store,f.a['id'])
        pages=read_pdf(pdf.statement_pdf(statement))[1];text='\n'.join(pages)
        for value in ('Factura 1001','Pago histórico PH-','Nota de crédito NC-QA-900','Aplicación CC-','Anulación de factura 1004','$ 8.500'):
            self.assertIn(value,text)
        consolidated='\n'.join(read_pdf(pdf.pending_pdf(pdf.pending_data(self.store)))[1])
        for value in ('Envases & Café <QA>','POR VALIDAR','800.000','Pago en pausa','12.000','8.500','Total general'):
            self.assertIn(value,consolidated)
        for forbidden in ('Factura 1003','Factura 1004','BANCO PRIVADO','CUENTA PRIVADA'):
            self.assertNotIn(forbidden,consolidated)
        for forbidden in ('BANCO PRIVADO','CUENTA PRIVADA'):self.assertNotIn(forbidden,text)
        self.assertEqual(self.snapshot(),before)

    def test_filtered_statement_keeps_full_account_opening_and_closing_balances(self):
        f=self.f.mixed()
        data=account.statement(self.store,f.a['id'],'2026-01-02','2026-01-03','payments')
        self.assertEqual((data['period']['opening_balance'],data['period']['closing_balance']), (28000,15500))
        self.assertEqual(data['period']['shown_credit'],11000)
        text='\n'.join(read_pdf(pdf.statement_pdf(data))[1])
        self.assertIn('$ 28.000',text);self.assertIn('$ 15.500',text);self.assertIn('$ 8.500',text)
        self.assertIn('Pagos y anulaciones',text);self.assertNotIn('Nota de crédito NC-QA-900',text)
        self.assertNotIn('Factura 1002',text)
        for row in data['journal']:self.assertIn(' '.join(row['label'].split()),' '.join(text.split()))

    def test_unapply_and_void_restore_pending_without_double_credit(self):
        f=self.f.mixed();entry=next(e for e in account.statement(self.store,f.a['id'])['entries'] if e['id']==f.p['id']);a=entry['applications'][0]
        f.command('account_unapply',dict(version=entry['version'],invoice_version=a['invoice_version'],reason='Aplicación ficticia equivocada'),a['id'])
        group=next(g for g in pdf.pending_data(self.store)['groups'] if g['supplier_id']==f.a['id'])
        self.assertEqual((group['balance'],group['available_credit'],group['payable']),(13000,4500,8500))
        entry=next(e for e in account.statement(self.store,f.a['id'])['entries'] if e['id']==f.p['id'])
        f.command('account_void',dict(version=entry['version'],reason='Registro ficticio anulado',invoices=[]),entry['id'])
        group=next(g for g in pdf.pending_data(self.store)['groups'] if g['supplier_id']==f.a['id'])
        self.assertEqual((group['balance'],group['available_credit'],group['payable']),(13000,500,12500))
        data=account.statement(self.store,f.a['id']);self.assertEqual(data['totals']['net_balance'],12500)
        self.assertTrue(data['totals']['consistent']);self.assertIn('Aplicación CC-1 anulada','\n'.join(read_pdf(pdf.statement_pdf(data))[1]))

    def test_empty_and_no_matching_movements_are_explicit(self):
        supplier=self.f.supplier('Sin operaciones FICTICIO')
        text='\n'.join(read_pdf(pdf.statement_pdf(account.statement(self.store,supplier['id'])))[1])
        self.assertIn('Sin movimientos para los filtros aplicados.',text)
        text='\n'.join(read_pdf(pdf.pending_pdf(pdf.pending_data(self.store)))[1])
        self.assertIn('No hay facturas pendientes',text);self.assertIn('$ 0',text)
        self.f.invoice(supplier,'1')
        text='\n'.join(read_pdf(pdf.statement_pdf(account.statement(self.store,supplier['id'],'2026-02-01','','credit_notes')))[1])
        self.assertIn('Sin movimientos para los filtros aplicados.',text);self.assertIn('$ 10.000',text)

    def test_multipage_repeats_headers_keeps_each_row_and_subtotal_together(self):
        supplier=self.f.supplier('Mayorista FICTICIO & Prueba <larga>')
        for number in range(72):self.f.invoice(supplier,'QA-'+str(10000+number),ap.MAX_AMOUNT)
        for kind,content in [('pending',pdf.pending_pdf(pdf.pending_data(self.store))),
                             ('statement',pdf.statement_pdf(account.statement(self.store,supplier['id'])))]:
            reader,pages=read_pdf(content);self.assertGreater(len(pages),3);joined='\n'.join(pages)
            for number in range(72):self.assertEqual(joined.count('QA-'+str(10000+number)),1)
            for i,text in enumerate(pages):
                self.assertIn('OVEJA',text);self.assertIn('Página '+str(i+1),text)
                if 'QA-' in text:
                    self.assertIn('DOCUMENTO',text)
                    self.assertIn('PENDIENTE' if kind=='pending' else 'CARGO',text)
                    self.assertIn('$ 999.999.999.999',text)
            if kind=='pending':
                subtotal=next(text for text in pages if 'Subtotal validado' in text)
                self.assertIn('QA-10071',subtotal);self.assertIn('Neto por pagar:',subtotal)
                self.assertGreater(float(reader.pages[0].mediabox.width),float(reader.pages[0].mediabox.height))

    def test_undated_and_unvalidated_are_reported_without_fabricated_dates(self):
        self.f.mixed()
        invoice=next(i for i in ap.view(self.store)['invoices'] if i['payment_review'])
        self.f.command('reconcile',dict(version=invoice['version'],previous_paid=50000,confirmed=True,reason='Antecedente ficticio sin fecha'),invoice['id'])
        data=account.statement(self.store,invoice['supplier_id'])
        self.assertEqual(data['period']['undated_count'],1)
        text='\n'.join(read_pdf(pdf.statement_pdf(data))[1]);self.assertIn('Sin fecha',text)
        self.assertIn('1 movimientos sin fecha',text);self.assertIn('$ 750.000',text)


if __name__=='__main__':unittest.main()
