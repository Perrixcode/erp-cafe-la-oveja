import base64
from contextlib import closing
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from erp.partner_auth import PartnerAuth
from erp.wsgi import create_app
from erp.transfers import read_transfers,read_photo
from erp.domain import DomainError
from erp.toteat_transport import fetch
from scripts.probe_toteat_comments import ProbeFailure
from scripts.export_bot_transfers import export_snapshot
from integrations.ovejita_archive import archive_photo,archive_selected
from scripts.backup_bot_evidence import backup_evidence


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_wsgi_requires_auth_exact_host_origin_secure_cookie_and_survives_calls(self):
        app=create_app(self.root,'https://erp.example.test')
        PartnerAuth(self.root/'private/partner-auth.sqlite3').create('fixture','Socio ficticio','Clave-ficticia-2026')
        def call(path,body=None,cookie='',host='erp.example.test',origin='https://erp.example.test'):
            raw=json.dumps(body).encode() if body is not None else b'';answer=[]
            payload=app(dict(REQUEST_METHOD='POST' if body is not None else 'GET',PATH_INFO=path,HTTP_HOST=host,HTTP_ORIGIN=origin,HTTP_COOKIE=cookie,HTTP_X_ERP_LOCAL='1',CONTENT_TYPE='application/json',CONTENT_LENGTH=str(len(raw)),**{'wsgi.input':BytesIO(raw)}),lambda s,h:answer.append((int(s.split()[0]),dict(h))))
            return *answer[0],b''.join(payload)
        self.assertEqual(call('/api/board')[0],401)
        self.assertEqual(call('/api/health',host='evil.invalid')[0],403)
        self.assertEqual(call('/api/partner/login',{},origin='https://evil.invalid')[0],403)
        status,headers,_=call('/api/partner/login',dict(username='fixture',password='Clave-ficticia-2026'))
        self.assertEqual(status,200);self.assertIn('; Secure',headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertEqual(call('/api/board',cookie=cookie)[0],200)
        self.assertEqual(call('/api/partner/logout',{},cookie)[0],200)
        self.assertEqual(call('/api/board',cookie=cookie)[0],401)

    def bot_fixture(self):
        db=self.root/'bot.sqlite3'
        with closing(sqlite3.connect(db)) as conn,conn:
            conn.executescript('CREATE TABLE revisiones(id INTEGER PRIMARY KEY,fecha TEXT,orden_id TEXT,monto_esperado INTEGER,codigo TEXT,registro TEXT,compatible INTEGER,estados TEXT,alertas TEXT,huella TEXT); CREATE TABLE revision_contexto(revision_id INTEGER,mesa TEXT,remitente TEXT); CREATE TABLE resoluciones(id INTEGER,revision_id INTEGER,fecha TEXT,usuario TEXT,accion TEXT,motivo TEXT); CREATE TABLE salud(componente TEXT,estado TEXT,detalle TEXT,fecha TEXT);')
            photo=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0j8AAAAASUVORK5CYII=')
            fingerprint=hashlib.sha256(photo).hexdigest()
            conn.execute('INSERT INTO revisiones VALUES(1,?,?,?,?,?,?,?,?,?)',('2026-10-04T12:00:00+00:00','100',1000,'DEMO-1234','ASOCIADO',1,'{"Monto":"COINCIDE"}','[]',fingerprint))
            conn.execute('INSERT INTO revision_contexto VALUES(1,?,?)',('Mesa ficticia','sender-fixture'))
        return db,photo,fingerprint

    def test_bot_export_is_readonly_exact_association_private_photo_and_no_sender(self):
        db,photo,fingerprint=self.bot_fixture();before=db.read_bytes()
        archive_photo(self.root/'erp-evidence',photo)
        archive_selected(self.root/'erp-evidence',1,fingerprint,dict(id='100',mesa='Mesa ficticia',total=1000),dict(orderId=100,document=dict(line=[dict(productName='Producto ficticio',quantity=1)],secret='never-copy')),2)
        snapshot=self.root/'export/transferencias.json';export_snapshot(db,snapshot,{'sender-fixture':'Caja ficticia'})
        self.assertEqual(db.read_bytes(),before)
        raw=snapshot.read_text();self.assertNotIn('sender-fixture',raw);self.assertNotIn('never-copy',raw)
        data=read_transfers(snapshot,dict(start='2026-10-01',end='2026-10-31'))
        self.assertEqual(data['rows'][0]['selected_sale']['selected_option'],2)
        self.assertEqual(read_photo(snapshot,1)[0],photo)
        with self.assertRaises(DomainError):read_photo(snapshot,2)
        (snapshot.parent/'photos'/data['rows'][0]['photo']['filename']).write_bytes(b'tampered')
        with self.assertRaises(DomainError) as error:read_photo(snapshot,1)
        self.assertEqual(error.exception.status,409)

    def test_photo_wrong_order_cannot_attach_selected_products(self):
        db,photo,fingerprint=self.bot_fixture()
        archive_selected(self.root/'erp-evidence',1,fingerprint,dict(id='wrong'),dict(orderId=100),1)
        export=self.root/'out/data.json';export_snapshot(db,export)
        row=json.loads(export.read_text())['rows'][0];self.assertIsNone(row['photo']);self.assertIsNone(row['selected_sale'])
        with self.assertRaises(DomainError):read_transfers(export,dict(start='2026-01-01',end='2025-01-01'))

    def test_evidence_backup_is_private_incremental_and_detects_corruption(self):
        _, photo, fingerprint = self.bot_fixture()
        source, destination = self.root/'erp-evidence', self.root/'backups'
        archive_photo(source, photo)
        self.assertEqual(backup_evidence(source, destination), 1)
        self.assertEqual(backup_evidence(source, destination), 1)
        self.assertEqual(len(list((destination/'objects').iterdir())), 1)
        target = destination/'objects'/fingerprint
        self.assertEqual(target.read_bytes(), photo)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        target.write_bytes(b'corrupt fixture')
        with self.assertRaises(ValueError):backup_evidence(source, destination)

    def test_bot_projection_preserves_closure_send_state_and_uncertain_count(self):
        db,_,_=self.bot_fixture()
        with closing(sqlite3.connect(db)) as connection,connection:
            connection.executescript("CREATE TABLE cierres_diarios(fecha TEXT,actualizado TEXT,informe TEXT); CREATE TABLE bot_salidas(mensaje_id TEXT,estado TEXT); INSERT INTO cierres_diarios VALUES('2026-10-04','2026-10-04T21:00:00+00:00','{}'); INSERT INTO bot_salidas VALUES('cierre:2026-10-04','enviada'); INSERT INTO bot_salidas VALUES('mensaje-ficticio','incierto');")
        target=self.root/'out/transfers.json';export_snapshot(db,target)
        result=read_transfers(target,dict(start='2026-10-01',end='2026-10-31'))
        self.assertEqual(result['closures'][0]['send_status'],'enviada')
        self.assertEqual(result['uncertain_messages'],1)

    def test_linux_reader_has_fixed_get_modes_scope_limits_and_no_credential_reflection(self):
        (self.root/'toteat').write_text(json.dumps(dict(xir='111',xil='1',xiu='222',xapitoken='FAKE-TEST-SECRET')))
        seen=[]
        def opener(request,timeout):
            seen.append(request);return BytesIO(b'{"ok":true,"data":[]}')
        with patch.dict(os.environ,{'CREDENTIALS_DIRECTORY':str(self.root)}):
            fetch(['sales-one-day','20261004'],dict(restaurant_id='111',local_id='1'),opener)
            self.assertEqual(seen[0].method,'GET');self.assertIn('ini=20261004&end=20261004',seen[0].full_url)
            for args in [['post'],['detail','../etc'],['sales-one-day','20261301']]:
                with self.assertRaises(ProbeFailure):fetch(args,opener=opener)
            with self.assertRaises(ProbeFailure):fetch(['fetch'],dict(restaurant_id='other',local_id='1'),opener)
            with self.assertRaises(ProbeFailure) as error:fetch(['fetch'],opener=lambda *a,**k:BytesIO(b'{"ok":true,"data":"FAKE-TEST-SECRET"}'))
            self.assertEqual(error.exception.code,'credential_reflection_blocked')
