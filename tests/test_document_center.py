import base64,hashlib,json,struct,tempfile,unittest,uuid,zlib
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from app import handler_for
from erp import document_center as dc
from erp.backups import create_backup,verify_backup,restore_backup
from erp.partner_auth import PartnerAuth
from erp.store import Store
from erp.wsgi import WSGITransport
from erp.domain import DomainError

def pdf(label='SYNTHETIC TEST ONLY'):
    stream=('BT /F1 16 Tf 30 80 Td ('+label+') Tj ET').encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 150] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    data=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    return data+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'
def png(width=2,height=2):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    pixels=(b'\0'+b'\xff\xff\xff'*2)*2
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
def rid():return uuid.uuid4().hex
class DocumentCenterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'data/erp-demo.sqlite3');dc.migrate(self.store,self.root)
        self.cookies={};self.password='Synthetic-test-passphrase'
        auth=PartnerAuth(self.root/'private/partner-auth.sqlite3')
        for role in ('partner','production','cashier'):auth.create(role,role,self.password,role)
        self.handler=handler_for(self.store,data_root=self.root,transport=WSGITransport)
        for role in ('partner','production','cashier'):
            response=self.http('/api/partner/login',payload={'username':role,'password':self.password})
            self.cookies[role]=dict(response.response_headers)['Set-Cookie'].split(';')[0]
    def tearDown(self):self.temp.cleanup()
    def http(self,path,role=None,payload=None,headers=None):
        body=json.dumps(payload).encode() if payload is not None else b'';path,_,query=path.partition('?')
        env={'REQUEST_METHOD':'POST' if payload is not None else 'GET','PATH_INFO':path,'QUERY_STRING':query,'SERVER_PORT':'8766','HTTP_HOST':'127.0.0.1:8766','HTTP_COOKIE':self.cookies.get(role,''),'HTTP_X_ERP_LOCAL':'1','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body)),'wsgi.input':BytesIO(body)}
        env.update(headers or {});response=self.handler(env);response.safe_dispatch();return response
    def call(self,path,role='partner',payload=None,status=200,headers=None):
        r=self.http('/api/document-center'+path,role,payload,headers);self.assertEqual(r.status,status,r.wfile.getvalue());return json.loads(r.wfile.getvalue())
    def payload(self,title='Test procedure',content=None,state='draft',kind='document'):
        p={'request_id':rid(),'title':title,'description':'Fictional test only','section_id':'salon','kind':kind,'state':state}
        if kind=='checklist':p['items']=['Revisar mesa','Cerrar registro']
        else:p['file']={'name':'test.pdf','base64':base64.b64encode(content or pdf(title)).decode()}
        return p
    def create(self,**kw):return self.call('/documents',payload=self.payload(**kw),status=201)['id']
    def state(self,id,value,rev=1):return self.call('/documents/'+id+'/state',payload={'request_id':rid(),'state':value,'expected_revision':rev})
    def count(self,table):
        with self.store.connect() as db:return db.execute('SELECT count(*) FROM '+table).fetchone()[0]
    def test_auth_and_existing_role_boundaries(self):
        id=self.create(state='active')
        for path in ('','/documents/'+id,'/documents/'+id+'/file'):self.assertEqual(self.http('/api/document-center'+path).status,401)
        for role in ('production','cashier'):
            self.assertEqual(self.call('',role)['total'],1)
            self.assertEqual(self.http('/api/document-center/documents/'+id+'/file',role).status,200)
            for path,payload in [('/documents',self.payload()),('/sections',{'request_id':rid(),'name':'New'}),('/documents/'+id+'/state',{'request_id':rid(),'expected_revision':1,'state':'archived'}),('/documents/'+id+'/versions',self.payload()),('/documents/'+id+'/runs',{'request_id':rid()})]:self.call(path,role,payload,403)
        self.assertEqual(self.count('dc_documents'),1)
    def test_drafts_archives_and_direct_urls(self):
        id=self.create()
        for role in ('production','cashier'):
            self.assertEqual(self.call('',role)['total'],0);self.call('/documents/'+id,role,status=404)
            self.assertEqual(self.http('/api/document-center/documents/'+id+'/file?version=1',role).status,404)
        self.state(id,'active');self.state(id,'archived',2)
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file','cashier').status,404)
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file','partner').status,200)
    def test_version_keeps_live_bytes_and_metadata(self):
        old=pdf('original');id=self.create(content=old,state='active')
        p=self.payload(title='Private draft title',content=pdf('new'));p['expected_revision']=1
        self.call('/documents/'+id+'/versions',payload=p)
        self.assertEqual(self.call('',role='cashier')['documents'][0]['title'],'Test procedure')
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file','cashier').wfile.getvalue(),old)
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file?version=2','cashier').status,404)
        self.state(id,'active',2)
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file','cashier').wfile.getvalue(),pdf('new'))
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file?version=1','partner').wfile.getvalue(),old)
    def test_move_without_new_file(self):
        id=self.create(state='active');p=self.payload(title='Moved');p.pop('file');p.update(section_id='manuales',expected_revision=1)
        self.call('/documents/'+id+'/versions',payload=p);self.assertEqual(self.count('dc_assets'),1)
        self.assertEqual(self.call('?section=salon','cashier')['total'],1)
        self.state(id,'active',2);self.assertEqual(self.call('?section=manuales','cashier')['total'],1)
    def test_retries_duplicates_and_stale_update(self):
        p=self.payload();a=self.call('/documents',payload=p,status=201);self.assertEqual(self.call('/documents',payload=p,status=201),a)
        self.assertEqual(self.count('dc_versions'),1)
        self.call('/documents',payload=dict(p,request_id=rid(),title='Another title'),status=409)
        self.call('/documents',payload=dict(p,title='Different payload'),status=409)
        newer=self.payload(title='Revised');newer['expected_revision']=1;self.call('/documents/'+a['id']+'/versions',payload=newer)
        self.call('/documents/'+a['id']+'/versions',payload=dict(newer,request_id=rid(),title='Stale'),status=409)
    def test_sections_and_text_fields(self):
        s=self.call('/sections',payload={'request_id':rid(),'name':'  SALON '},status=201);self.assertEqual(s['id'],'salon');self.assertEqual(self.count('dc_sections'),8)
        self.call('/sections',payload={'request_id':rid(),'name':'Bad\x00name'},status=400)
        id=self.create(title='<script>fake()</script>');self.assertEqual(self.call('/documents/'+id)['title'],'<script>fake()</script>')
    def test_file_types_names_active_pdf_and_integrity(self):
        for name,content,status in [('fake.pdf',b'<html>bad</html>',415),('../file.pdf',pdf(),400),('a\\file.pdf',pdf(),400),('x.pdf\r\nHeader: bad',pdf(),400),('image.png',pdf(),415),('active.pdf',pdf().replace(b'%%EOF',b'/Java#53cript /JS (bad)\n%%EOF'),415),('x.svg',b'<svg/>',415)]:
            with self.subTest(name=name):
                p=self.payload();p['file']={'name':name,'base64':base64.b64encode(content).decode()};self.call('/documents',payload=p,status=status)
        p=self.payload();p['file']['base64']='!!!';self.call('/documents',payload=p,status=400)
        id=self.create()
        with self.store.connect() as db:db.execute('UPDATE dc_assets SET content=?',(b'corrupted',))
        self.assertEqual(self.http('/api/document-center/documents/'+id+'/file','partner').status,409)
    def test_png_headers_size_and_csrf(self):
        p=self.payload();p['file']={'name':'Prueba á.png','base64':base64.b64encode(png()).decode()};id=self.call('/documents',payload=p,status=201)['id']
        r=self.http('/api/document-center/documents/'+id+'/file?download=1','partner');h=dict(r.response_headers)
        self.assertEqual(r.status,200);self.assertEqual(r.wfile.getvalue(),png());self.assertEqual(h['Content-Type'],'image/png');self.assertIn("filename*=UTF-8''",h['Content-Disposition'])
        self.assertTrue(any(k=='Content-Security-Policy' and "default-src 'none'" in v and "frame-ancestors 'self'" in v for k,v in r.response_headers))
        self.assertEqual(h['X-Content-Type-Options'],'nosniff')
        with self.assertRaises(DomainError):dc.mime_type(png()[:-1]+b'x')
        with self.assertRaises(DomainError):dc.mime_type(png(12000,12000))
        content=b'%PDF-1.4\n'+b' '*(dc.MAX_FILE-15)+b'%%EOF\n';self.assertEqual(len(content),dc.MAX_FILE)
        self.assertEqual(dc.mime_type(content),'application/pdf')
        with self.assertRaises(DomainError):dc.mime_type(content+b' ')
        self.call('/documents',payload=self.payload(),status=403,headers={'HTTP_ORIGIN':'https://bad.invalid'})
        self.call('/documents',payload=self.payload(),status=403,headers={'HTTP_X_ERP_LOCAL':''})
    def test_checklist_snapshot_complete_and_idempotency(self):
        id=self.create(kind='checklist',state='active')
        p={'request_id':rid(),'expected_revision':1,'version':1,'responsible':'Persona ficticia','performed_on':'2026-10-16','checked':[True,True],'notes':'Test only'}
        first=self.call('/documents/'+id+'/runs',payload=p);self.assertEqual(self.call('/documents/'+id+'/runs',payload=p),first)
        self.call('/documents/'+id+'/runs',payload=dict(p,request_id=rid(),checked=[True,False]),status=400)
        v=self.payload(kind='checklist');v.update(expected_revision=1,items=['New item']);self.call('/documents/'+id+'/versions',payload=v);self.state(id,'active',2)
        self.call('/documents/'+id+'/runs',payload=dict(p,request_id=rid()),status=409)
        detail=self.call('/documents/'+id);self.assertEqual(len(detail['runs']),1);self.assertEqual(detail['runs'][0]['items'][0]['text'],'Revisar mesa')
        self.assertNotIn('runs',self.call('/documents/'+id,'cashier'))
    def test_legacy_idempotent_and_backup_restoration(self):
        path=self.root/dc.LEGACY_PATH;path.parent.mkdir(parents=True);path.write_bytes(pdf('LEGACY SYNTHETIC'))
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(dc,'LEGACY_SHA',digest):dc.migrate(self.store,self.root);dc.migrate(self.store,self.root)
        self.assertEqual(self.count('dc_documents'),1);self.assertEqual(self.count('dc_versions'),1)
        with self.store.connect() as db:self.assertIsNone(db.execute('SELECT content FROM dc_assets').fetchone()[0])
        self.assertEqual(self.http('/api/documents/salon/conteo-semanal-lunes-a-viernes.pdf','partner').status,307)
        self.create(title='Uploaded synthetic')
        backup=create_backup(self.root);metadata=verify_backup(backup);self.assertIn(dc.LEGACY_PATH,metadata['files'])
        restore=self.root/'restore-check';restore_backup(backup,restore);self.assertEqual(verify_backup(restore),metadata);self.assertEqual((restore/dc.LEGACY_PATH).read_bytes(),path.read_bytes())
    def test_both_existing_documents_survive_migration_and_backup(self):
        weekly=self.root/dc.LEGACY_PATH;labels=self.root/dc.LABELS_PATH
        weekly.parent.mkdir(parents=True);weekly.write_bytes(pdf('WEEKLY SYNTHETIC'));labels.write_bytes(pdf('LABELS SYNTHETIC'))
        with patch.object(dc,'LEGACY_SHA',hashlib.sha256(weekly.read_bytes()).hexdigest()),patch.object(dc,'LABELS_SHA',hashlib.sha256(labels.read_bytes()).hexdigest()):
            dc.migrate(self.store,self.root);dc.migrate(self.store,self.root)
        self.assertEqual(self.count('dc_documents'),2);self.assertEqual(self.count('dc_versions'),2)
        backup=create_backup(self.root);metadata=verify_backup(backup)
        self.assertIn(dc.LEGACY_PATH,metadata['files']);self.assertIn(dc.LABELS_PATH,metadata['files'])
        restored=self.root/'both-restore';restore_backup(backup,restored);self.assertEqual(verify_backup(restored),metadata)
        self.assertEqual((restored/dc.LABELS_PATH).read_bytes(),labels.read_bytes())
    def test_mismatch_before_migration_changes_nothing(self):
        root=self.root/'other';store=Store(root/'data/erp-demo.sqlite3');path=root/dc.LEGACY_PATH;path.parent.mkdir(parents=True);path.write_bytes(pdf('WRONG'))
        with self.assertRaises(DomainError):dc.migrate(store,root)
        with store.connect() as db:self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='dc_documents'").fetchone())
    def test_search_literal_and_pagination(self):
        self.create(title='100% verified');self.assertEqual(self.call('?q=%25')['total'],1);self.assertEqual(self.call('?q=_')['total'],0)
        self.call('?page=-1',status=400);self.assertEqual(self.call('?section=manuales')['total'],0)
if __name__=='__main__':unittest.main()
