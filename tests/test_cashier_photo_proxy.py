"""Real isolated Caddy -> WSGI -> photo storage, no live requests or login credentials.

Run with a Caddy binary on PATH and this repository on PYTHONPATH. Existing
production configuration, services and databases are never changed.
"""
from contextlib import contextmanager
from copy import deepcopy
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from random import Random
from socketserver import ThreadingMixIn
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
import base64, hashlib, json, os, shutil, socket, subprocess, tempfile, time

from PIL import Image
from erp import cashier_v2 as v2, cash_closures as cl, cash_bags as bags, cash_control as cc
from erp.store import Store
from erp.wsgi import create_app

ORIGIN = 'https://erp.example.test'
CASHIER = {'role':'cashier','username':'synthetic-cashier','name':'Synthetic QA'}
V2_BLOCK = '''    # V2 photos: 3 MiB binary becomes 4 MiB base64 plus JSON metadata.
    @closure_v2_attachment {
        method POST
        path_regexp closure_v2_file ^/api/cash-closures/v2/[a-f0-9]{32}/(?:attach|reference-attach)$
    }
    request_body @closure_v2_attachment {
        max_size 5242880
    }
'''
V2_EXEMPT = '''        not {
            method POST
            path_regexp closure_v2_exempt ^/api/cash-closures/v2/[a-f0-9]{32}/(?:attach|reference-attach)$
        }
'''

class MemorySessions:
    """Session lookup only is synthetic; route role/ownership checks are real."""
    def session(self, cookie):
        return deepcopy({'qa=cashier': CASHIER, 'qa=other': {**CASHIER, 'username':'other-cashier'},'qa=partner':{'role':'partner','username':'synthetic-admin','name':'Synthetic QA'}}.get(cookie))

class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args): pass

class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

@contextmanager
def proxy(root, template, backend_port, name):
    proxy_port=port()
    config=root/(name+'.Caddyfile')
    config.write_text('{\n admin off\n auto_https off\n}\n'+template.replace('erp.example.test {',f'http://erp.example.test:{proxy_port} {{\n bind 127.0.0.1').replace('127.0.0.1:8010',f'127.0.0.1:{backend_port}'))
    validation=subprocess.run(['caddy','validate','--config',str(config),'--adapter','caddyfile'],capture_output=True,text=True)
    assert validation.returncode==0, validation.stderr
    log=(root/(name+'.log')).open('w+')
    process=subprocess.Popen(['caddy','run','--config',str(config),'--adapter','caddyfile'],stdout=log,stderr=log,env={**os.environ,'XDG_DATA_HOME':str(root/'caddy-data'),'XDG_CONFIG_HOME':str(root/'caddy-config')})
    try:
        for _ in range(100):
            assert process.poll() is None, 'Isolated Caddy exited'
            try:
                with socket.create_connection(('127.0.0.1',proxy_port),timeout=.1):break
            except OSError:time.sleep(.02)
        else:raise AssertionError('Isolated proxy startup timeout')
        yield proxy_port
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()

def request(proxy_port,path,body=None,cookie='qa=cashier',origin=ORIGIN):
    headers={'Host':'erp.example.test','Origin':origin,'Content-Type':'application/json','X-ERP-Local':'1'}
    if cookie:headers['Cookie']=cookie
    data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
    req=Request(f'http://127.0.0.1:{proxy_port}'+path,data=data,headers=headers)
    try:
        with urlopen(req,timeout=15) as response:return response.status,response.read()
    except HTTPError as error:
        result=error.code,error.read();error.close();return result

def run():
    assert shutil.which('caddy'), 'This integration test requires the real Caddy binary'
    template_path=Path(os.environ.get('ERP_TEST_CADDYFILE',Path(__file__).resolve().parents[1]/'deploy/Caddyfile.erp'))
    fixed=template_path.read_text();assert fixed.count(V2_BLOCK)==1 and fixed.count(V2_EXEMPT)==1
    baseline=fixed.replace(V2_BLOCK,'').replace(V2_EXEMPT,'')
    temporary=tempfile.TemporaryDirectory(prefix='oveja-photo-storage-qa-')
    case=SimpleNamespace(temp=temporary,store=Store(Path(temporary.name)/'data/erp-demo.sqlite3'))
    bags.initialize(case.store);cc.initialize(case.store);cl.initialize(case.store);checks=[]
    try:
        register=cl.execute(case.store,'register',{'request_id':uuid4().hex,'confirmed':True,'name':'Synthetic QA'},{'role':'partner','username':'synthetic-admin','name':'Synthetic QA'})['register_id']
        initial=v2.command(case.store,'create',{'request_id':uuid4().hex,'confirmed':True,'identity':{'register_id':register,'business_date':'2026-10-16'}},CASHIER)
        root=Path(case.temp.name)
        with patch('app.PartnerAuth',return_value=MemorySessions()),patch('erp.wsgi.Store',return_value=case.store):
            app=create_app(root,ORIGIN)
        server=make_server('127.0.0.1',0,app,server_class=ThreadedServer,handler_class=QuietHandler)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            pixels=Random(1234).randbytes(1400*1100*3);image=Image.frombytes('RGB',(1400,1100),pixels);buffer=BytesIO();image.save(buffer,format='JPEG',quality=85);photo=buffer.getvalue()
            assert 64000<len(photo)<3*1024*1024
            payload={'request_id':uuid4().hex,'confirmed':True,'version':initial['version'],'kind':'mp','file_name':'synthetic-receipt.jpg','file_type':'image/jpeg','file_data':base64.b64encode(photo).decode()}
            path='/api/cash-closures/v2/'+initial['id']+'/attach'
            with proxy(root,baseline,server.server_port,'baseline') as p:
                status,error_body=request(p,path,payload);assert status in (413,502),status
                with case.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM cv2_images').fetchone()[0]==0
                checks.append('baseline real proxy rejects valid large JPEG and persists zero images')
                baseline_status=status
            with proxy(root,fixed,server.server_port,'fixed') as p:
                for cookie,expected in [(None,401),('qa=other',403)]:
                    status,_=request(p,path,{'request_id':uuid4().hex,'confirmed':True},cookie=cookie);assert status==expected,(status,expected)
                checks.append('expired session and other cashier remain 401/403')
                status,_=request(p,path,{'confirmed':True},origin='https://outside.example.test');assert status==403
                checks.append('foreign origin remains rejected')
                status,body=request(p,path,payload);assert status==200,(status,body[:200]);meta=json.loads(body)
                assert meta['size']==len(photo) and meta['sha256']==hashlib.sha256(photo).hexdigest() and meta['legible'] is False
                checks.append('same JPEG traverses real Caddy and WSGI and receives verified attachment metadata')
                declaration={k:deepcopy(initial[k]) for k in v2.DECLARATION_FIELDS};declaration['photos']=[meta]
                save={'request_id':uuid4().hex,'confirmed':True,'version':initial['version'],'declaration':declaration}
                status,_=request(p,path.removesuffix('/attach')+'/save',save);assert status==200
                status,body=request(p,path.removesuffix('/attach'));assert status==200
                reopened=json.loads(body);assert len(reopened['photos'])==1 and reopened['photos'][0]['sha256']==meta['sha256']
                checks.append('saved draft reload contains exactly one photo')
                status,body=request(p,'/api/cash-closures/'+initial['id']+'/attachments/'+meta['id']);assert status==200 and body==photo
                checks.append('protected attachment retrieval returns exact original JPEG bytes')
                status,body=request(p,path,payload);assert status==200 and json.loads(body)['id']==meta['id']
                checks.append('lost-response retry after later save returns same attachment without duplication')
                reference_path=path.removesuffix('/attach')+'/reference-attach'
                status,_=request(p,reference_path,{'request_id':uuid4().hex,'confirmed':True,'version':reopened['version']});assert status==403,status
                status,body=request(p,reference_path,{**payload,'request_id':uuid4().hex,'version':reopened['version']},cookie='qa=partner');assert status==200 and json.loads(body)['id']==meta['id']
                checks.append('large reference attachment works only for partner and preserves cashier restriction')
                too_large={**payload,'request_id':uuid4().hex,'version':reopened['version'],'file_data':base64.b64encode(b'x'*(3*1024*1024+1)).decode()}
                status,body=request(p,path,too_large);assert status==400 and '3 MiB' in json.loads(body)['error']
                checks.append('application still rejects binary payload over 3 MiB')
                status,body=request(p,path,{**payload,'request_id':uuid4().hex,'version':reopened['version'],'file_type':'image/heic','file_data':base64.b64encode(b'fake').decode()});assert status==400
                checks.append('application still rejects unsupported image format')
                status,_=request(p,path,b' '*(5*1024*1024+1));assert status in (413,502),status
                checks.append('proxy still rejects transport body over 5 MiB')
                status,_=request(p,path.removesuffix('/attach')+'/save',b' '*(64000+1));assert status in (413,502),status
                checks.append('ordinary save route retains 64000-byte proxy limit')
                with case.store.connect() as db:
                    assert db.execute('SELECT COUNT(*) FROM cv2_images').fetchone()[0]==1
                    assert db.execute('SELECT COUNT(*) FROM cl_attachments').fetchone()[0]==1
                    assert db.execute('SELECT COUNT(*) FROM cc_cash_events').fetchone()[0]==0
                assert not list(root.rglob('partner-auth.sqlite3'))
                checks.append('one durable attachment, zero cash events and no login credential database')
            print(json.dumps({'passed':len(checks),'checks':checks,'baseline_status':baseline_status,'baseline_response_body_bytes':len(error_body),'jpeg_bytes':len(photo),'json_body_bytes':len(json.dumps(payload).encode()),'stored_attachments':1,'limits':['Isolated loopback Caddy and real WSGI/domain/storage; session lookup is synthetic, role/ownership checks are real. No production records or browser/camera used.']}))
        finally:server.shutdown();server.server_close();thread.join(timeout=5)
    finally:temporary.cleanup()

if __name__=='__main__':run()
