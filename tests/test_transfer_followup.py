"""Paridad del panel con datos ficticios: historial compartido, permisos y CSV."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
from io import BytesIO, StringIO
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from erp.domain import DomainError
from erp.partner_auth import PartnerAuth
from erp.store import Store
from erp.transfer_followup import call
from erp.transfers import read_transfers, export_csv
from erp.wsgi import create_app
from integrations.ovejita_followup import FollowupError, prepare, read_histories, resolve
from scripts.bot_followup_service import make_service
from scripts.backup_bot_database import backup_database
from scripts.export_bot_transfers import export_snapshot


def bot_fixture(root, count=3):
    path = root/'bot.sqlite3'
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE revisiones(id INTEGER PRIMARY KEY,fecha TEXT,orden_id TEXT,monto_esperado INTEGER,codigo TEXT,registro TEXT,compatible INTEGER,estados TEXT,alertas TEXT,huella TEXT);
        CREATE TABLE revision_contexto(revision_id INTEGER,mesa TEXT,remitente TEXT);
        CREATE TABLE resoluciones(id INTEGER PRIMARY KEY,revision_id INTEGER,fecha TEXT,usuario TEXT,accion TEXT,motivo TEXT);
        CREATE TABLE salud(componente TEXT,estado TEXT,detalle TEXT,fecha TEXT);
        CREATE TABLE bot_salidas(mensaje_id TEXT,estado TEXT);
        INSERT INTO bot_salidas VALUES('ficticio-no-enviar','pendiente');
        INSERT INTO salud VALUES('Procesador','OK','Ficticio','2026-01-01T12:00:00+00:00');''')
        for rid in range(1,count+1):
            db.execute('INSERT INTO revisiones VALUES(?,?,?,?,?,?,?,?,?,?)', (rid,'2026-10-04T12:00:00+00:00','DEMO-1',1000,'DEMO','ASOCIADO',int(rid==2),'{"Monto":"NO_COINCIDE","Fecha":"COINCIDE"}','["Ficticio"]','a'*64))
            db.execute('INSERT INTO revision_contexto VALUES(?,?,?)',(rid,'Mesa ficticia','caja-a' if rid%2 else 'caja-b'))
    return path


class TransferFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='oveja-fw-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=bot_fixture(self.root);prepare(self.db)
        self.snapshot=self.root/'import/transferencias.json'
        self.project()
        self.filters=dict(start='2026-10-01',end='2026-10-31')

    def project(self):
        export_snapshot(self.db,self.snapshot,{'caja-a':'Caja ficticia A','caja-b':'Caja ficticia B'})

    def command(self, rid=1, action='revisado', version=0):
        current=read_histories(self.db,[rid])[0]
        return dict(revision_id=rid,version=version,source_ref=current['source_ref'],actor='socio-ficticio',action=action,reason='Revisión ficticia por socio',request_id=str(uuid.uuid4()))

    def start_bridge(self):
        self.socket=self.root/'service.sock';service=make_service(self.db,self.socket)
        thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
        def stop():service.shutdown();service.server_close();thread.join()
        self.addCleanup(stop)
        return self.socket

    def test_review_reopen_original_result_and_shared_monitor_query_preserved(self):
        with closing(sqlite3.connect(self.db)) as db:
            original=db.execute('SELECT * FROM revisiones').fetchall()
            def pending():return db.execute("SELECT COUNT(*) FROM revisiones r WHERE compatible=0 AND NOT EXISTS(SELECT 1 FROM resoluciones s WHERE s.revision_id=r.id AND s.id=(SELECT MAX(id) FROM resoluciones WHERE revision_id=r.id) AND s.accion='revisado')").fetchone()[0]
            self.assertEqual(pending(),2)
            reviewed=resolve(self.db,self.command());self.assertEqual(pending(),1)
            resolve(self.db,self.command(action='pendiente',version=reviewed['followup_version']));self.assertEqual(pending(),2)
            self.assertEqual(db.execute('SELECT * FROM revisiones').fetchall(),original)
            self.assertEqual(db.execute('SELECT estado FROM bot_salidas').fetchone()[0],'pendiente')
            self.assertEqual(db.execute('SELECT accion FROM resoluciones ORDER BY id').fetchall(),[('revisado',),('pendiente',)])

    def test_parallel_retries_are_one_event_and_stale_edits_rejected(self):
        payload=self.command()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:resolve(self.db,payload),range(2)))
        self.assertEqual(len({r['saved_event_id'] for r in results}),1)
        for other in [dict(payload,reason='Otro motivo ficticio'),self.command(action='pendiente'),dict(self.command(),source_ref='b'*64)]:
            with self.assertRaises(FollowupError) as error:resolve(self.db,other)
            self.assertEqual(error.exception.status,409)
        self.assertEqual(len(read_histories(self.db,[1])[0]['history']),1)

    def test_invalid_or_compatible_cannot_mutate(self):
        for change in [dict(action='pagado'),dict(actor=''),dict(reason=' '),dict(version=True),dict(request_id='../otro')]:
            with self.assertRaises(FollowupError):resolve(self.db,dict(self.command(),**change))
        with self.assertRaises(FollowupError):resolve(self.db,self.command(rid=2))
        self.assertEqual(read_histories(self.db,[1])[0]['history'],[])

    def test_online_bot_backup_restores_shared_audit_and_idempotency(self):
        payload=self.command();resolve(self.db,payload)
        before=self.db.read_bytes()
        directory=backup_database(self.db,self.root/'backups')
        manifest=json.loads((directory/'manifest.json').read_text());copy=directory/manifest['file']
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(),manifest['sha256'])
        self.assertEqual(copy.stat().st_mode&0o777,0o600)
        self.assertEqual(read_histories(copy,[1]),read_histories(self.db,[1]))
        self.assertTrue(resolve(copy,payload)['repeated'])
        self.assertEqual(read_histories(self.db,[1])[0]['followup_version'],1)

    def test_bridge_read_overlay_does_not_wait_for_snapshot_and_survives_restart(self):
        path=self.start_bridge();payload=self.command()
        result=call(path,'/follow-up',payload)
        self.assertEqual(result['seguimiento'],'revisado')
        self.assertEqual(read_transfers(self.snapshot,self.filters,path)['summary']['pending'],1)
        self.assertEqual(read_transfers(self.snapshot,dict(self.filters,status='reviewed'),path)['total'],1)
        self.assertEqual(read_transfers(self.snapshot,dict(self.filters,status='review'),path)['total'],2)
        prepare(self.db)
        self.assertTrue(call(path,'/follow-up',payload)['repeated'])
        with self.assertRaises(DomainError) as conflict:call(path,'/follow-up',self.command(action='pendiente'))
        self.assertEqual(conflict.exception.status,409)
        unavailable=read_transfers(self.snapshot,self.filters,self.root/'missing.sock')
        self.assertFalse(unavailable['followup_available']);self.assertTrue(unavailable['read_only'])
        self.assertEqual(self.socket.stat().st_mode&0o777,0o660)

    def test_csv_all_pages_filters_formula_safety_zero_dedupe_and_audit(self):
        other=self.root/'many';other.mkdir();db=bot_fixture(other,61);snapshot=other/'out.json'
        export_snapshot(db,snapshot,{'caja-a':' =1+1','caja-b':'Caja B'})
        store=Store(self.root/'erp.sqlite3');rid=str(uuid.uuid4())
        data=read_transfers(snapshot,self.filters)
        self.assertEqual(len(data['rows']),50);self.assertEqual(data['summary']['expected_amount'],1000)
        content=export_csv(snapshot,self.filters,store,'socio-ficticio',rid)
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.reader(StringIO(content.decode('utf-8-sig'))));self.assertEqual(len(rows),62)
        self.assertTrue(rows[1][3].startswith("'"));self.assertEqual(len(data['breakdown']['cashiers']),2)
        self.assertTrue(data['health'][0]['delayed'])
        export_csv(snapshot,self.filters,store,'socio-ficticio',rid)
        with store.connect() as conn:
            audit=conn.execute('SELECT actor,row_count FROM transfer_exports').fetchall();self.assertEqual([tuple(r) for r in audit],[('socio-ficticio',61)])
        cashier=next(r['id'] for r in data['cashiers'] if r['name']=='Caja B')
        filtered=export_csv(snapshot,dict(self.filters,cashier=cashier,status='compatible'),store,'socio-ficticio',str(uuid.uuid4()))
        self.assertEqual(len(list(csv.reader(StringIO(filtered.decode('utf-8-sig'))))),2)
        with self.assertRaises(DomainError):export_csv(snapshot,dict(self.filters,status='pending'),store,'socio-ficticio',rid)

    def test_wsgi_requires_partner_origin_and_uses_authenticated_actor(self):
        socket_path=self.start_bridge();root=self.root/'web';application=create_app(root,'https://erp.example.test')
        auth=PartnerAuth(root/'private/partner-auth.sqlite3')
        auth.create('socio-fixture','Socio ficticio','Clave-ficticia-2026')
        auth.create('caja-fixture','Caja ficticia','Clave-ficticia-2026','cashier')
        def request(path,body=None,cookie='',origin='https://erp.example.test'):
            raw=json.dumps(body).encode() if body is not None else b'';answer=[]
            payload=application(dict(REQUEST_METHOD='POST' if body is not None else 'GET',PATH_INFO=path,HTTP_HOST='erp.example.test',HTTP_ORIGIN=origin,HTTP_COOKIE=cookie,HTTP_X_ERP_LOCAL='1',CONTENT_TYPE='application/json',CONTENT_LENGTH=str(len(raw)),**{'wsgi.input':BytesIO(raw)}),lambda s,h:answer.append((int(s.split()[0]),dict(h))))
            return *answer[0],b''.join(payload)
        with patch.dict(os.environ,{'OVEJA_TRANSFERS_SNAPSHOT':str(self.snapshot),'OVEJA_TRANSFERS_SOCKET':str(socket_path)}):
            payload=dict(self.command(),actor='usurpado')
            self.assertEqual(request('/api/transfers/1/follow-up',payload)[0],401)
            def login(user):return request('/api/partner/login',dict(username=user,password='Clave-ficticia-2026'))[1]['Set-Cookie'].split(';')[0]
            cashier=login('caja-fixture');partner=login('socio-fixture')
            self.assertEqual(request('/api/transfers/1/follow-up',payload,cashier)[0],403)
            self.assertEqual(request('/api/transfers/export',dict(filters=self.filters,request_id=str(uuid.uuid4())),cashier)[0],403)
            self.assertEqual(request('/api/transfers/1/follow-up',payload,partner,'https://evil.invalid')[0],403)
            status,_,raw=request('/api/transfers/1/follow-up',payload,partner)
            self.assertEqual(status,200);self.assertEqual(json.loads(raw)['history'][-1]['usuario'],'socio-fixture')
            status,headers,raw=request('/api/transfers/export',dict(filters=self.filters,request_id=str(uuid.uuid4())),partner)
            self.assertEqual(status,200);self.assertIn('attachment',headers['Content-Disposition']);self.assertIn('no-store',headers['Cache-Control'])
            self.assertEqual(read_histories(self.db,[1])[0]['seguimiento'],'revisado')


if __name__=='__main__':unittest.main()
