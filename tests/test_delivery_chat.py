"""Paquetes, importaciones y permisos con datos íntegramente ficticios."""
from copy import deepcopy
from datetime import date
import hashlib,json
from pathlib import Path
import tempfile,threading,unittest,zipfile
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from erp import delivery_chat as chat,delivery_finance as finance
from erp.domain import DomainError
from erp.store import Store
from app import make_server
from http_test_support import authenticated_opener


def fixture():
    records=[];messages=[]
    for i,(driver,amount,status,day) in enumerate([
        ('Repartidor A',1000,'structured_subtotal','2026-09-01'),
        ('Repartidor B',1500,'structured_subtotal','2026-09-28'),
        (None,2000,'pending_driver','2026-09-01'),
        ('Repartidor A',None,'pending_amount','2026-09-02'),
        ('Repartidor B',1200,'pending_possible_duplicate','2026-09-03'),
        ('Repartidor B',900,'pending_failed_attempt_payment','2026-09-04')],1):
        stamp=day+'T12:15:00'
        record={'id':str(i)+'.1','message_id':i,'line_start':i*10,'line_end':i*10+2,'sent_at':stamp,
                'sender':'Remitente distinto','driver':driver,'driver_basis':'explicit' if driver else 'missing',
                'amount_clp':amount,'customer':'Cliente ficticio '+str(i),'address':'Calle ficticia 1',
                'edited':i==2,'flags':[],'raw_block':'Contenido ficticio','operational_date':None,
                'date_basis':'WhatsApp local send date; time zone unspecified in export','analysis_status':status}
        if amount is None:record.update(suggested_amount_clp=800,support_message_ids=[1])
        records.append(record)
        messages.append({'id':i,'line_start':i*10,'line_end':i*10+2,'timestamp':stamp,'sender':'Remitente distinto',
                         'text':'Texto ficticio <img src=x onerror="window.injection=true">'})
    summary={'source_library_file_id':'libfile_fixture','source_sha256':'a'*64,'candidate_records':6,'numeric_records':5,
             'raw_numeric_total_clp':6600,'structured_subtotal_records':2,'structured_subtotal_clp':2500,'delivery_messages':6,
             'structured_subtotal_by_driver':[{'driver':'Repartidor A','records':1,'known_amount_clp':1000},{'driver':'Repartidor B','records':1,'known_amount_clp':1500}],
             'image_omissions':2,'deleted_messages':1,'edited_delivery_messages':1,'edited_delivery_records':1,
             'additional_text_only_attempt':{'date':'2026-09-04','driver':'Repartidor B','suggested_amount_clp':900,'support_message_ids':[6]},
             'unallocated_incidents':[{'date':'2026-09-03','message_ids':[5],'type':'do_not_deliver'}]}
    return records,messages,summary


def package(path,records=None,messages=None,summary=None,source=None):
    originals=fixture();records=deepcopy(records if records is not None else originals[0]);messages=deepcopy(messages if messages is not None else originals[1]);summary=deepcopy(summary if summary is not None else originals[2])
    if source:summary['source_sha256']=source
    files={chat.RECORDS:json.dumps(records).encode(),chat.MESSAGES:json.dumps(messages).encode(),chat.SUMMARY:json.dumps(summary).encode()}
    manifest={'package_schema':'erp_delivery_import_provisional_v1','source_library_file_id':'libfile_fixture','source_sha256':summary['source_sha256'],
              'candidate_count':6,'structured_subtotal_count':2,'known_gross_clp':6600,'structured_subtotal_clp':2500,'evidence_message_count':6,
              'files':[{'name':n,'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for n,v in files.items()]}
    with zipfile.ZipFile(path,'w') as archive:
        for name,content in files.items():archive.writestr(name,content)
        archive.writestr('manifest.json',json.dumps(manifest))
    return path


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Store(self.root/'erp.sqlite3');self.path=package(self.root/'fixture.zip')
    def load(self,path=None):return chat.import_package(self.store,path or self.path,'2026-09','libfile_package_fixture','Socio ficticio')

    def test_control_totals_reimport_restart_and_no_operational_mutations(self):
        self.assertTrue(self.load()['imported']);self.assertFalse(self.load()['imported'])
        view=chat.view(Store(self.store.path),'2026-09')
        self.assertEqual(view['totals'],{'records':6,'base_records':2,'base':2500,'known_gross':6600,'unknown_amounts':1,'pending':4})
        self.assertEqual(len(view['records']),6);self.assertEqual(len(view['incidents']),2)
        self.assertFalse(view['payment_executed']);self.assertFalse(view['final_settlement'])
        with self.store.connect() as db:
            for table in ('orders','items','stock','delivery_transactions','reception_events'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM delivery_chat_batches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT actor FROM delivery_chat_batches').fetchone()[0],'Socio ficticio')

    def test_unknown_not_zero_suggestions_not_added_and_sender_never_becomes_driver(self):
        self.load();view=chat.view(self.store,'2026-09')
        unknown=next(r for r in view['records'] if r['analysis_status']=='pending_amount')
        self.assertIsNone(unknown['amount_clp']);self.assertEqual(unknown['suggested_amount_clp'],800)
        missing=next(c for c in view['couriers'] if c['driver'] is None)
        self.assertEqual(missing['known_gross'],2000);self.assertEqual(missing['base'],0)
        self.assertNotIn('Remitente distinto',str(view['couriers']))

    def test_days_weeks_month_match_and_boundary_weeks_are_clipped(self):
        self.load();view=chat.view(self.store,'2026-09')
        self.assertEqual(view['weeks'][0]['start'],'2026-09-01');self.assertEqual(view['weeks'][-1]['end'],'2026-09-30')
        self.assertEqual(sum(d['base'] for d in view['days']),2500)
        self.assertEqual(sum(d['base'] for d in view['weeks']),2500)
        self.assertEqual(sum(c['base'] for c in view['couriers']),2500)
        self.assertTrue(all(day['toteat'] is None for day in view['days']))

    def test_toteat_comparison_uses_available_shift_data_without_claiming_match(self):
        self.load();finance.apply_day(self.store,'2026-09-01',{'ok':True,'data':[]},{'restaurant_id':'DEMO','local_id':'1'})
        view=chat.view(self.store,'2026-09');self.assertEqual(view['days'][0]['toteat'],0)
        self.assertIsNone(view['days'][1]['toteat']);self.assertIsNone(view['toteat']['total'])
        self.assertEqual(view['date_basis'],'message_local_date_without_timezone')

    def test_preserves_original_evidence_lines_edited_markers_and_context(self):
        self.load();data=chat.evidence(self.store,'a'*64,'4.1')
        self.assertEqual([m['id'] for m in data['messages']],[4,1]);self.assertEqual(data['record']['line_start'],40)
        self.assertIn('<img',data['messages'][0]['text']);self.assertEqual(data['record']['sent_at'],'2026-09-02T12:15:00')
        self.assertEqual(chat.evidence(self.store,'a'*64,incident_key='additional-attempt')['messages'][0]['id'],6)
        with self.assertRaises(DomainError):chat.evidence(self.store,'a'*64,'999.1')

    def test_tampered_archive_duplicate_records_missing_evidence_or_totals_fail_atomically(self):
        self.load();records,messages,summary=fixture()
        variants=[]
        duplicate=deepcopy(records);duplicate[-1]['id']=duplicate[0]['id'];variants.append((duplicate,messages,summary))
        bad=deepcopy(records);bad[0]['amount_clp']=True;variants.append((bad,messages,summary))
        bad=deepcopy(records);bad[0]['sent_at']+='Z';variants.append((bad,messages,summary))
        bad=deepcopy(records);bad[0]['support_message_ids']=[99];variants.append((bad,messages,summary))
        bad_summary=deepcopy(summary);bad_summary['raw_numeric_total_clp']+=1;variants.append((records,messages,bad_summary))
        for index,(r,m,s) in enumerate(variants):
            with self.subTest(index=index),self.assertRaises(DomainError):self.load(package(self.root/f'bad{index}.zip',r,m,s))
        with zipfile.ZipFile(self.path) as archive:entries={i.filename:archive.read(i) for i in archive.infolist()}
        entries[chat.RECORDS]+=b' '
        broken=self.root/'broken.zip'
        with zipfile.ZipFile(broken,'w') as archive:
            for name,value in entries.items():archive.writestr(name,value)
        with self.assertRaises(DomainError):self.load(broken)
        self.assertEqual(chat.view(self.store,'2026-09')['totals']['records'],6)

    def test_changed_source_version_or_overlapping_export_is_not_summed(self):
        self.load();records,messages,summary=fixture();records[0]['customer']='Cambio ficticio'
        with self.assertRaises(DomainError):self.load(package(self.root/'changed.zip',records,messages,summary))
        with self.assertRaises(DomainError):self.load(package(self.root/'other.zip',source='b'*64))
        self.assertEqual(chat.view(self.store,'2026-09')['totals']['base'],2500)
        self.assertFalse(chat.view(self.store,'2026-10')['available'])

    def test_http_partner_only_for_summary_and_original_evidence(self):
        self.load();server=make_server(self.store.path,0,seed=False)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_address[1]);paths=['/api/delivery/chat?month=2026-09','/api/delivery/chat/evidence?source='+'a'*64+'&record=1.1']
            clients={role:authenticated_opener(server,self.root,role,role+'-chat-qa') for role in ('partner','cashier','production')}
            for path in paths:
                for role in (None,'cashier','production'):
                    with self.assertRaises(HTTPError) as error:(clients[role].open if role else urlopen)(base+path,timeout=3)
                    with error.exception as response:self.assertEqual(response.status,403 if role else 401)
                with clients['partner'].open(base+path,timeout=3) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
        finally:server.shutdown();server.server_close();thread.join()
