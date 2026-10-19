"""Read-only reconciliation and PDF tests, entirely synthetic."""
from copy import deepcopy
from io import BytesIO
import hashlib,json,tempfile,threading,unittest,zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from erp import delivery_chat as chat,delivery_audit as audit,delivery_settlement as settlement
from erp.store import Store
from erp.domain import DomainError
from app import make_server
from http_test_support import authenticated_opener
from test_delivery_chat import fixture,package as chat_package


def package(path,change=None):
    records=fixture()[0];rows=[];reports=[];sources={'chat':{'sha256':'a'*64}}
    files={}
    for driver,indices,values,extension in [('Repartidor A',[0,3,2],[1000,800,2000],'pdf'),('Repartidor B',[1,4],[1500,1200],'xlsx')]:
        original=f'fuentes/{driver}.{extension}';content=b'%PDF-1.4\nDocumento ficticio' if extension=='pdf' else b'PK\x03\x04Documento ficticio'
        files[original]=content;sources[audit.key(driver)]={'original':original,'sha256':hashlib.sha256(content).hexdigest()}
        base=chat.totals([r for r in records if r['driver']==driver])
        reports.append({'conductor':driver,'cantidad_importes_informe':len(indices),'bruto_declarado_informe_clp':sum(values),'cantidad_base_chat_provisional':base['base_records'],'bruto_base_chat_provisional_clp':base['base'],'adelanto_declarado_clp':500 if extension=='pdf' else None,'saldo_declarado_clp':3300 if extension=='pdf' else None,'detalle_diferencia':'Reclamación ficticia con atribución pendiente.','estado':'Sin aprobación'})
        for ordinal,(index,amount) in enumerate(zip(indices,values),1):
            r=records[index];rows.append({'conductor_informe':driver,'ordinal_informe':ordinal,'fecha_informe':r['sent_at'][:10],'ordinal_dia_informe':ordinal,'ubicacion_fuente':'Página ficticia / celda A1','importe_informe_clp':amount,'chat_registro_id':r['id'],'chat_fecha_hora':r['sent_at'],'conductor_explicito_chat':r['driver'] or 'Sin conductor explícito','importe_original_chat_clp':r['amount_clp'],'chat_linea_inicio':r['line_start'],'chat_linea_fin':r['line_end'],'estado_base_original':r['analysis_status'],'observacion':'Coincidencia documental ficticia; no prueba ejecución.'})
    cases=[{'caso_id':'DEMO-1','fecha':'2026-09-01','importe_clp':2000,'conductores_relacionados':'Repartidor A / Repartidor B','chat_registros_id':'3.1','ubicacion_informe':'Celda ficticia A1','hallazgo':'Reclamación de un importe sin conductor.','evidencia_pendiente':'Confirmar ejecutor.','estado':'Pendiente','asignacion_aprobada':False,'pago_aprobado':False}]
    summary={'schema':'comparacion_repartos_tres_fuentes_evidencia_v1','periodo':'2026-09','moneda':'CLP','fuentes':sources,'planillas':reports,'suma_dos_informes':{'cantidad_importes':len(rows),'bruto_clp':sum(r['importe_informe_clp'] for r in rows)},'restricciones':['No atribuir ni pagar automáticamente.']}
    if change:change(summary,rows,cases)
    for name,data in [('resumen_conjunto.json',summary),('cruce_440_importes.json',rows),('pendientes_conciliacion.json',cases)]:files[name]=json.dumps(data).encode()
    manifest={'schema':'comparacion_repartos_evidencia_manifest_v1','periodo':'2026-09','cantidad_archivos_sin_manifest':len(files),'archivos':[{'archivo:n':n} for n in []]}
    manifest['archivos']=[{'archivo':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in files.items()]
    with zipfile.ZipFile(path,'w') as z:
        for n,b in files.items():z.writestr(n,b)
        z.writestr('manifest.json',json.dumps(manifest))
    return path


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root/'erp.sqlite3')
        chat.import_package(self.store,chat_package(self.root/'chat.zip'),'2026-09','libfile_qa_chat','Socio ficticio')
        self.path=package(self.root/'audit.zip')
    def load(self,path=None):return audit.import_package(self.store,path or self.path,'2026-09','libfile_qa_audit','Socio ficticio')

    def test_import_idempotent_immutable_and_original_hash(self):
        before=chat.view(self.store,'2026-09');self.assertTrue(self.load()['imported']);self.assertFalse(self.load()['imported'])
        self.assertEqual(before,chat.view(self.store,'2026-09'))
        a=audit.view(self.store,'2026-09');self.assertEqual(len(a['rows']),5);self.assertFalse(a['payment_approved'])
        for d in a['documents']:
            content,kind,ext=audit.document(self.store,'2026-09',d['document_key']);self.assertEqual(hashlib.sha256(content).hexdigest(),d['sha256'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_audit_batches').fetchone()[0],1)
            for table in ('orders','items','stock','delivery_transactions','reception_events'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_failed_validation_is_atomic_and_conflicting_versions_rejected(self):
        changes=[lambda s,r,c:r[0].update(chat_registro_id='999.1'),lambda s,r,c:r[0].update(chat_linea_inicio=999),lambda s,r,c:s['fuentes']['chat'].update(sha256='b'*64),lambda s,r,c:c[0].update(pago_aprobado=True),lambda s,r,c:r[0].update(conductor_explicito_chat='Repartidor B'),lambda s,r,c:s['planillas'][0].update(bruto_base_chat_provisional_clp=999)]
        for i,change in enumerate(changes):
            with self.subTest(i=i),self.assertRaises(DomainError):self.load(package(self.root/f'bad{i}.zip',change))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM delivery_audit_batches').fetchone()[0],0)
        self.load()
        with self.assertRaises(DomainError):self.load(package(self.root/'changed.zip',lambda s,r,c:r[0].update(observacion='Otro texto')))
        with zipfile.ZipFile(self.path) as z:entries={i.filename:z.read(i) for i in z.infolist()}
        entries['cruce_440_importes.json']+=b' '
        with zipfile.ZipFile(self.root/'tampered.zip','w') as z:
            for n,b in entries.items():z.writestr(n,b)
        with self.assertRaises(DomainError):self.load(self.root/'tampered.zip')

    def test_daily_unknowns_no_toteat_attribution_and_no_advance_deduction(self):
        self.load();a=audit.view(self.store,'2026-09');r=a['reports'][0]
        self.assertEqual((r['chat_base'],r['difference']),(1000,2800));self.assertIsNone(r['toteat'])
        empty=r['days'][2];self.assertIsNone(empty['report_amount']);self.assertIsNone(empty['difference'])
        s=settlement.view(self.store,'2026-09','Repartidor A');self.assertEqual(s['totals']['base'],1000);self.assertEqual(s['totals']['unknown_amounts'],1)
        self.assertEqual(s['reports'][0]['adelanto_declarado_clp'],500);self.assertFalse(s['final_settlement']);self.assertEqual(len(s['cases']),1)
        self.assertEqual(settlement.view(self.store,'2026-09','missing')['totals']['held_known'],2000)
        for driver in ('not-found',''):
            with self.assertRaises(DomainError):settlement.view(self.store,'2026-09',driver)
        with self.assertRaises(DomainError):settlement.view(self.store,'2026-10')

    def test_metrics_denominators_and_missing_previous_month(self):
        s=chat.view(self.store,'2026-09');m=s['metrics'];self.assertEqual(m['average_base'],1250)
        self.assertEqual(m['pending'],{'count':4,'denominator':6,'percent':66.67});self.assertEqual(m['attempts_under_review']['count'],1)
        self.assertIsNone(m['average_change_percent']);self.assertEqual(m['highest_amount_days'],['2026-09-28'])
        records,messages,summary=fixture()
        for r in records:r['sent_at']=r['sent_at'].replace('2026-09','2026-08')
        for r in messages:r['timestamp']=r['timestamp'].replace('2026-09','2026-08')
        summary['additional_text_only_attempt']['date']='2026-08-04';summary['unallocated_incidents'][0]['date']='2026-08-03'
        chat.import_package(self.store,chat_package(self.root/'previous.zip',records,messages,summary,source='b'*64),'2026-08','libfile_prev','Socio ficticio')
        self.assertEqual(chat.view(self.store,'2026-09')['metrics']['average_change_percent'],0)

    def test_three_source_scopes_missing_days_and_period_sums(self):
        from erp import delivery_finance as finance
        from test_delivery_finance import sale,payload,SCOPE
        self.load()
        finance.apply_day(self.store,'2026-09-01',payload(sale(fee=900)),SCOPE)
        comp=audit.view(self.store,'2026-09')['comparison']
        first=comp['days'][0]
        self.assertEqual((first['toteat_amount'],first['chat_amount'],first['report_amount']),(900,1000,3000))
        self.assertEqual((first['chat_toteat_difference'],first['report_chat_difference']),(100,2000))
        self.assertIsNone(comp['month']['toteat_amount'])
        self.assertIsNone(comp['days'][4]['chat_amount'])
        self.assertIsNone(comp['days'][4]['report_amount'])
        self.assertFalse(comp['reports_cover_global'])
        self.assertNotIn('report_toteat_difference',comp['month'])
        for day in range(2,31):finance.apply_day(self.store,f'2026-09-{day:02d}',payload(),SCOPE)
        comp=audit.view(self.store,'2026-09')['comparison']
        self.assertEqual(comp['month']['chat_toteat_difference'],1600)
        self.assertEqual(comp['month']['report_amount'],6500)
        for field in ('report_amount','chat_amount','report_chat_amount','toteat_amount'):
            self.assertEqual(sum(d[field] or 0 for d in comp['days']),comp['month'][field])
            self.assertEqual(sum(w[field] or 0 for w in comp['weeks']),comp['month'][field])
        empty=comp['days'][4]
        self.assertEqual(empty['toteat_amount'],0)
        self.assertIsNone(empty['chat_toteat_difference'])
        self.assertIsNone(chat.view(self.store,'2026-09')['days'][4]['comparison']['base_amount_difference'])

    def test_pdf_multipage_accents_totals_and_private_original_tampering(self):
        from pypdf import PdfReader
        self.load();s=settlement.view(self.store,'2026-09');content=settlement.pdf(s);reader=PdfReader(BytesIO(content));self.assertGreater(len(reader.pages),2)
        for p in reader.pages:self.assertIn('PROVISIONAL',p.extract_text());self.assertIn('Sin aprobación',p.extract_text())
        text='\n'.join(p.extract_text() for p in reader.pages)
        for expected in ('Liquidación','repartidores','$2.500','sin comprobante','3.1','2026-09-28'):self.assertIn(expected,text)
        with self.store.connect() as db:db.execute("UPDATE delivery_audit_documents SET content=X'00' WHERE document_key='1'")
        with self.assertRaises(DomainError):audit.document(self.store,'2026-09','1')

    def test_partner_only_all_new_endpoints_and_cache_headers(self):
        self.load();server=make_server(self.store.path,0,seed=False);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_address[1]);paths=['/api/delivery/audit?month=2026-09','/api/delivery/audit/document?month=2026-09&id=1','/api/delivery/audit/document?month=2026-09&id=2','/api/delivery/settlement?month=2026-09','/api/delivery/settlement/pdf?month=2026-09&driver=Repartidor%20A']
            with urlopen(base+'/delivery-review.js') as response:self.assertEqual(response.status,200);self.assertIn(b'OvejaDeliveryReview',response.read())
            clients={r:authenticated_opener(server,self.root,r,r+'-review') for r in ('partner','cashier','production')}
            for path in paths:
                for role in (None,'cashier','production'):
                    with self.assertRaises(HTTPError) as error:(clients[role].open if role else urlopen)(base+path,timeout=5)
                    with error.exception as response:self.assertEqual(response.status,403 if role else 401)
                with clients['partner'].open(base+path,timeout=5) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
                    if '/pdf?' in path:self.assertEqual(response.headers['Content-Type'],'application/pdf');self.assertTrue(response.read().startswith(b'%PDF-'))
            with clients['partner'].open(base+'/api/delivery/settlement/pdf?month=2026-09&download=1') as response:self.assertIn('attachment;',response.headers['Content-Disposition'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
