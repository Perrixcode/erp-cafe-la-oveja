"""Synthetic export, adjustment accounting, idempotency and permission coverage."""
import csv,json,tempfile,threading,unittest
from copy import deepcopy
from io import BytesIO,StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from unittest.mock import patch
from pypdf import PdfReader
from erp import delivery_chat as chat,delivery_audit as audit,delivery_summary as summary,delivery_adjustments as adjustments,delivery_settlement as settlement
from erp.store import Store
from erp.domain import DomainError
from app import make_server
from http_test_support import authenticated_opener
from test_delivery_chat import package as cp
from test_delivery_review import package as ap

class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root/'test.sqlite3')
        chat.import_package(self.store,cp(self.root/'chat.zip'),'2026-09','libfile_qa_chat','Socio ficticio')
        audit.import_package(self.store,ap(self.root/'audit.zip'),'2026-09','libfile_qa_audit','Socio ficticio')
    def data(self,**patch):
        return dict(request_id='synthetic-request-0001',month='2026-09',driver='Repartidor A',kind='addition',amount=700,concept='Viaje adicional ficticio',**patch)
    def add(self,**patch):
        data=self.data();data.update(patch);return adjustments.create(self.store,data,'socio-qa')
    def view(self,driver='all'):return summary.view(self.store,'2026-09',driver)

    def test_adjustments_totals_and_sources_unchanged(self):
        before=chat.view(self.store,'2026-09');self.add();self.add(request_id='synthetic-request-0002',kind='deduction',amount=200,concept='Descuento ficticio')
        self.add(request_id='synthetic-request-0003',driver='Repartidor B',amount=300,concept='Encargo ficticio')
        value=self.view();self.assertEqual(value['totals']['base'],2500);self.assertEqual(value['totals']['principal'],6500);self.assertEqual(value['totals']['net'],7300)
        a=self.view('Repartidor A');self.assertEqual(a['totals']['net'],4300);self.assertEqual(a['rows'][0]['declared_advance'],500)
        self.assertEqual(a['totals']['unknown_amounts'],1);self.assertEqual(self.view('missing')['totals']['held_known'],2000)
        self.assertEqual(before,chat.view(self.store,'2026-09'));self.assertFalse(value['payment_executed'])
        self.assertEqual(settlement.view(self.store,'2026-09','Repartidor A')['totals']['net'],4300)
        self.assertNotIn('Repartidor B',[a['driver'] for a in self.view('Repartidor A')['adjustments']])

    def test_retries_conflicts_void_history_and_restart(self):
        first=self.add();self.assertEqual(first,self.add())
        with self.assertRaises(DomainError):self.add(amount=701)
        body={'request_id':'synthetic-void-0001','version':1,'reason':'Corrección ficticia del concepto'}
        voided=adjustments.void(self.store,first['id'],body,'socio-qa');self.assertEqual(voided['version'],2)
        self.assertEqual(voided,adjustments.void(self.store,first['id'],body,'socio-qa'))
        with self.assertRaises(DomainError):adjustments.void(self.store,first['id'],dict(body,request_id='synthetic-void-0002'),'socio-qa')
        self.store=Store(self.store.path);self.assertEqual(self.view()['totals']['net'],6500)
        saved=self.view()['adjustments'][0];self.assertEqual(saved['amount'],700);self.assertEqual(saved['concept'],first['concept']);self.assertEqual(saved['void_actor'],'socio-qa')

    def test_concurrent_retry_counts_only_once(self):
        results=[]
        def create():results.append(self.add())
        workers=[threading.Thread(target=create) for _ in range(2)]
        for t in workers:t.start()
        for t in workers:t.join()
        self.assertEqual(len(results),2);self.assertEqual(self.view()['totals']['additions'],700)

    def test_invalid_inputs_and_unconfirmed_driver(self):
        for patch in ({'amount':0},{'amount':-1},{'amount':True},{'amount':1.5},{'kind':'other'},{'driver':'missing'},{'month':'2026-10'},{'concept':'x'},{'request_id':'short'}):
            data=self.data();data.update(patch)
            with self.subTest(patch=patch),self.assertRaises(DomainError):adjustments.create(self.store,data,'socio-qa')
        self.assertEqual(self.view()['adjustments'],[])
        with self.assertRaises(DomainError):self.view('unknown')
        with self.assertRaises(DomainError):summary.view(self.store,'2026-10')

    def test_pdf_csv_summary_matches_adjusted_ui_and_retains_unknowns(self):
        self.add();self.add(request_id='synthetic-request-0002',kind='deduction',amount=200,concept='Descuento de prueba')
        data=self.view();raw=summary.csv_bytes(data);self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.DictReader(StringIO(raw.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(list(rows[0]),['Repartidor','Base chat (referencia)','Monto declarado en planilla','Adicionales','Descuentos','Total a pagar'])
        self.assertEqual(sum(int(r['Total a pagar']) for r in rows if r['Total a pagar']!='Pendiente'),7000)
        unknown=next(r for r in rows if r['Repartidor']=='Sin repartidor confirmado');self.assertEqual(unknown['Monto declarado en planilla'],'Sin dato');self.assertEqual(unknown['Total a pagar'],'Pendiente')
        a=next(r for r in rows if r['Repartidor']=='Repartidor A');self.assertEqual(a['Base chat (referencia)'],'1000');self.assertEqual(a['Monto declarado en planilla'],'3800');self.assertEqual(a['Total a pagar'],'4300')
        pdf=PdfReader(BytesIO(summary.pdf(data)));text='\n'.join(p.extract_text() for p in pdf.pages)
        self.assertLessEqual(len(pdf.pages),2)
        for expected in ('PROVISIONAL','Repartidor A','$7.000','Viaje adicional ficticio','Descuento de prueba','Sin dato','Pendiente','SUBTOTAL CON PLANILLA'):self.assertIn(expected,text)
        self.assertNotIn('Fichas del registro',text)
        detailed=PdfReader(BytesIO(settlement.pdf(settlement.view(self.store,'2026-09'))))
        self.assertIn('$7.000',detailed.pages[0].extract_text())
        evil=deepcopy(data);evil['rows'][0]['label']='  =HYPERLINK("https://invalid.example")';evil['rows'][0]['adjustments'][0]['concept']='@SUM(1)'
        out=list(csv.DictReader(StringIO(summary.csv_bytes(evil).decode('utf-8-sig')),delimiter=';'))
        self.assertTrue(out[0]['Repartidor'].startswith("'"))

    def test_missing_planilla_keeps_concepts_pending_and_zero_is_valid(self):
        self.add(driver='Repartidor B')
        comparison=audit.view(self.store,'2026-09')
        comparison['reports']=[r for r in comparison['reports'] if r['conductor']=='Repartidor A']
        with patch.object(summary.audit,'view',return_value=comparison):
            value=self.view();b=self.view('Repartidor B')
            self.assertEqual(b['totals']['base'],1500);self.assertIsNone(b['totals']['principal']);self.assertIsNone(b['totals']['net'])
            self.assertEqual(b['rows'][0]['additions'],700);self.assertEqual(value['totals']['net'],3800)
            self.assertEqual(value['totals']['missing_reports'],2);self.assertEqual(value['totals']['additions'],0);self.assertEqual(value['totals']['unapplied_additions'],700)
            self.assertIsNone(settlement.view(self.store,'2026-09','Repartidor B')['totals']['net'])
            comparison['reports']=[]
            self.assertIsNone(self.view()['totals']['net'])
            self.assertIn('Pendiente',PdfReader(BytesIO(summary.pdf(self.view()))).pages[0].extract_text())
            comparison['reports']=[{'conductor':'Repartidor B','bruto_declarado_informe_clp':0}]
            self.assertEqual(self.view('Repartidor B')['totals']['net'],700)

    def test_negative_balance_kept_numeric_in_csv(self):
        self.add(kind='deduction',amount=4000)
        value=self.view('Repartidor A');self.assertEqual(value['totals']['net'],-200)
        rows=list(csv.DictReader(StringIO(summary.csv_bytes(value).decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(rows[0]['Total a pagar'],'-200')

    def test_multiple_couriers_filter_totals_history_metrics_and_exports(self):
        self.add();self.add(request_id='synthetic-multi-0002',driver='Repartidor B',amount=300)
        selected=['Repartidor A','missing']
        value=self.view(selected)
        self.assertEqual(value['drivers'],selected);self.assertEqual(value['totals']['net'],4500)
        self.assertEqual([r['driver'] for r in value['rows']],['Repartidor A',None])
        self.assertEqual([a['driver'] for a in value['adjustments']],['Repartidor A'])
        records=[r for r in chat.view(self.store,'2026-09')['records'] if r['driver'] in ('Repartidor A',None)]
        self.assertEqual(value['metrics'],chat.metrics(records))
        data=settlement.view(self.store,'2026-09',selected)
        self.assertEqual(data['totals']['net'],4500);self.assertEqual(data['records'],records)
        self.assertEqual([r['conductor'] for r in data['reports']],['Repartidor A'])
        exported=list(csv.DictReader(StringIO(summary.csv_bytes(value).decode('utf-8-sig')),delimiter=';'))
        self.assertEqual([r['Repartidor'] for r in exported],['Repartidor A','Sin repartidor confirmado'])
        pdf='\n'.join(p.extract_text() for p in PdfReader(BytesIO(summary.pdf(value))).pages)
        self.assertIn('Repartidor A',pdf);self.assertNotIn('Repartidor B',pdf)
        both=self.view(['Repartidor A','Repartidor B'])
        self.assertEqual(both['totals']['net'],7500);self.assertEqual(both['totals']['missing_reports'],0)
        self.assertEqual(both['metrics']['average_base'],1250)
        self.assertEqual(self.view(['Repartidor A','Repartidor A'])['totals']['net'],4500)
        for invalid in ([],[''],['all','Repartidor A'],['Repartidor A','unknown']):
            with self.subTest(invalid=invalid),self.assertRaises(DomainError):self.view(invalid)

    def test_http_exports_and_adjustments_partner_only_actor_from_session(self):
        server=make_server(self.store.path,0,seed=False);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_address[1]);clients={r:authenticated_opener(server,self.root,r,r+'-summary') for r in ('partner','cashier','production')}
            paths=['/api/delivery/summary?month=2026-09','/api/delivery/summary.pdf?month=2026-09&download=1','/api/delivery/summary.csv?month=2026-09']
            for path in paths:
                for role in (None,'cashier','production'):
                    with self.assertRaises(HTTPError) as caught:(clients[role].open if role else urlopen)(base+path)
                    with caught.exception as response:self.assertEqual(response.status,403 if role else 401)
                with clients['partner'].open(base+path) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
                    if '.pdf' in path or '.csv' in path:self.assertIn('attachment;',response.headers['Content-Disposition'])
            selection=urlencode([('month','2026-09'),('driver','Repartidor A'),('driver','missing')])
            for endpoint in ('summary','summary.csv','summary.pdf','settlement','settlement/pdf'):
                with clients['partner'].open(base+'/api/delivery/'+endpoint+'?'+selection) as response:
                    if endpoint.endswith('.csv'):
                        exported=list(csv.DictReader(StringIO(response.read().decode('utf-8-sig')),delimiter=';'))
                        self.assertEqual([r['Repartidor'] for r in exported],['Repartidor A','Sin repartidor confirmado'])
                    elif 'pdf' in endpoint:
                        self.assertTrue(response.read().startswith(b'%PDF-'))
                    else:
                        value=json.load(response);self.assertEqual(value['drivers'],['Repartidor A','missing']);self.assertEqual(value['totals']['net'],3800)
            for invalid in ('driver=', 'driver=all&driver=Repartidor+A'):
                with self.assertRaises(HTTPError) as caught:clients['partner'].open(base+'/api/delivery/summary?month=2026-09&'+invalid)
                with caught.exception as response:self.assertEqual(response.status,400)
            body=self.data();body['actor']='forged-actor'
            req=Request(base+'/api/delivery/adjustments',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'})
            for role in (None,'cashier','production'):
                with self.assertRaises(HTTPError) as caught:(clients[role].open if role else urlopen)(Request(req.full_url,data=req.data,headers={'Content-Type':'application/json','X-ERP-Local':'1'}))
                with caught.exception as response:self.assertEqual(response.status,403 if role else 401)
            with clients['partner'].open(req) as response:created=json.load(response)
            self.assertEqual(created['actor'],'partner-summary')
            cancel=Request(base+'/api/delivery/adjustments/'+created['id']+'/void',data=json.dumps({'request_id':'synthetic-http-void','version':1,'reason':'Corrección de prueba'}).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'})
            with self.assertRaises(HTTPError) as caught:clients['cashier'].open(Request(cancel.full_url,data=cancel.data,headers={'Content-Type':'application/json','X-ERP-Local':'1'}))
            with caught.exception as response:self.assertEqual(response.status,403)
            with clients['partner'].open(cancel) as response:self.assertEqual(json.load(response)['void_actor'],'partner-summary')
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
