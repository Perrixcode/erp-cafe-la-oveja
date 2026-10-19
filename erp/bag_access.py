"""Optional registered-email/password access, isolated from ERP accounts.

Disabled by default. No email sending, public registration or real account seeding.
"""
from http.cookies import SimpleCookie, CookieError
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import re
import secrets
import time
import threading
from uuid import uuid4

from erp.domain import DomainError
from erp import cash_bags as bags

PREFIX='/api/bag-access'
MANAGER='/api/bag-cashiers'
COOKIE='oveja_bag_access'
ACTIVATION_SECONDS=20*60
HASH_N=2**17
HASH_R=8
HASH_P=1
HASH_SLOTS=threading.BoundedSemaphore(2)
REMEMBER_SECONDS=14*86400
SESSION_SECONDS=8*3600

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def normalize_email(value):
    if not isinstance(value,str) or len(value)>254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",value.strip()):
        raise DomainError('Ingresa un correo válido.')
    return value.strip().casefold()
def cookie_value(header,name):
    try:
        c=SimpleCookie();c.load(header or '');value=c[name].value if name in c else ''
        return value if len(value)<=200 else ''
    except (CookieError,TypeError):return ''
def cookie(name,value,seconds,secure):
    return f'{name}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={seconds}'+('; Secure' if secure else '')
def payload(data,fields):
    if set(data)-set(fields):raise DomainError('La solicitud contiene campos no admitidos.')
def client_key(handler):
    # The deployed listener is loopback-only behind Caddy. Use the last proxy-added
    # XFF address only for loopback peers; ignore forwarded headers on direct peers.
    peer=getattr(handler,'client_address',('unknown',0))[0]
    try:
        address=ipaddress.ip_address(peer)
        if address.is_loopback and handler.headers.get('X-Forwarded-For'):
            peer=str(ipaddress.ip_address(handler.headers['X-Forwarded-For'].split(',')[-1].strip()))
        else:peer=str(address)
    except ValueError:peer='unknown'
    return digest(peer)

class BagAccess:
    def __init__(self,store,clock=time.time):
        self.store=store;self.clock=clock
        with store.connect() as db:db.executescript(Path(__file__).with_suffix('.sql').read_text())

    def now(self):return int(self.clock())

    def limits(self,entries):
        now=self.now();allowed=True
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM bag_access_limits WHERE window<?',(now-86400,))
            for key,period,maximum in entries:
                window=(now//period)*period;scope=digest(key)
                row=db.execute('SELECT used FROM bag_access_limits WHERE scope=? AND window=?',(scope,window)).fetchone()
                if row and row[0]>=maximum:allowed=False
            if allowed:
                for key,period,_ in entries:
                    db.execute('INSERT INTO bag_access_limits VALUES(?,?,1) ON CONFLICT(scope,window) DO UPDATE SET used=used+1',(digest(key),(now//period)*period))
        if not allowed:raise DomainError('Demasiados intentos. Espera antes de volver a intentar.',429)

    @staticmethod
    def password_value(data):
        value=data.get('password')
        if not isinstance(value,str) or not 15<=len(value)<=128 or not value.strip() or any(ord(c)<32 for c in value):
            raise DomainError('Usa una contraseña de 15 a 128 caracteres; puedes usar una frase.')
        if value!=data.get('password_confirmation'):
            raise DomainError('Las contraseñas no coinciden.')
        return value

    @staticmethod
    def password_hash(password,salt,n=HASH_N,r=HASH_R,p=HASH_P):
        if not HASH_SLOTS.acquire(blocking=False):
            raise DomainError('Hay varios accesos en curso. Vuelve a intentar en unos segundos.',429)
        try:return hashlib.scrypt(password.encode(),salt=salt,n=n,r=r,p=p,maxmem=256*1024*1024,dklen=32)
        finally:HASH_SLOTS.release()

    def credential_matches(self,row,password):
        if not isinstance(password,str) or len(password)>128:return False
        computed=self.password_hash(password,row['salt'] if row else b'\0'*16)
        return bool(row and hmac.compare_digest(computed,row['password_hash']))

    def revoke(self,db,cashier_id,now):
        db.execute('UPDATE bag_access_sessions SET revoked_at=? WHERE cashier_id=? AND revoked_at IS NULL',(now,cashier_id))
        db.execute('UPDATE bag_access_enrollments SET used_at=? WHERE cashier_id=? AND used_at IS NULL',(now,cashier_id))

    def put_password(self,db,cashier_id,password,now):
        salt=secrets.token_bytes(16);hashed=self.password_hash(password,salt)
        db.execute('INSERT INTO bag_access_credentials VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(cashier_id) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,n=excluded.n,r=excluded.r,p=excluded.p,version=version+1,updated_at=excluded.updated_at',
                   (cashier_id,salt,hashed,HASH_N,HASH_R,HASH_P,now))
        self.revoke(db,cashier_id,now)
        email=db.execute('SELECT email FROM bag_cashiers WHERE id=?',(cashier_id,)).fetchone()[0]
        db.execute('DELETE FROM bag_access_limits WHERE scope=?',(digest('login-email:'+email),))

    def login(self,data,client):
        payload(data,{'email','password','remember','device_name'})
        email=normalize_email(data.get('email'));password=data.get('password');remember=data.get('remember')
        if type(remember) is not bool:raise DomainError('Indica si deseas recordar este dispositivo.')
        device=bags.text(data.get('device_name','Mi celular'),'Nombre del dispositivo',60)
        self.limits([('login-email:'+email,900,5),('login-ip:'+client,900,30),('login-global',900,300)])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT c.*,u.enabled,u.name FROM bag_access_credentials c JOIN bag_cashiers u ON u.id=c.cashier_id WHERE u.email=?',(email,)).fetchone()
            matched=self.credential_matches(row,password)
            if not matched or not row['enabled']:
                raise DomainError('Correo o contraseña incorrectos, o acceso no habilitado.',401)
            now=self.now();token=secrets.token_urlsafe(32);seconds=REMEMBER_SECONDS if remember else SESSION_SECONDS
            db.execute('INSERT INTO bag_access_sessions VALUES(?,?,?,?,?,?,NULL)',(uuid4().hex,digest(token),row['cashier_id'],device,now,now+seconds))
            db.execute('INSERT INTO bag_access_events(cashier_id,action,actor,created_at) VALUES(?,?,?,?)',(row['cashier_id'],'password_login',row['cashier_id'],now))
        return token,seconds

    def activate(self,data,client):
        payload(data,{'token','email','password','password_confirmation'})
        token=data.get('token');email=normalize_email(data.get('email'));password=self.password_value(data)
        self.limits([('activate-ip:'+client,900,15),('activate-email:'+email,900,5)])
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            raise DomainError('Enlace inválido, usado o vencido. Pide ayuda al administrador.',401)
        now=self.now()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT e.*,u.enabled,u.email FROM bag_access_enrollments e JOIN bag_cashiers u ON u.id=e.cashier_id WHERE token_hash=?',(digest(token),)).fetchone()
            if not row or not row['enabled'] or row['email']!=email or row['used_at'] is not None or row['expires_at']<=now:
                raise DomainError('Enlace inválido, usado o vencido. Pide ayuda al administrador.',401)
            self.put_password(db,row['cashier_id'],password,now)
            db.execute('INSERT INTO bag_access_events(cashier_id,action,actor,created_at) VALUES(?,?,?,?)',(row['cashier_id'],'password_established_by_holder',row['cashier_id'],now))
        return {'password_set':True,'message':'Contraseña guardada. Vuelve al QR e ingresa con tu correo y contraseña.'}

    def change_password(self,header,data,client):
        payload(data,{'current_password','password','password_confirmation'})
        password=self.password_value(data)
        self.limits([('change-ip:'+client,900,15)])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');user=self.session_row(db,header)
            if not user:raise DomainError('Inicia sesión para cambiar tu contraseña.',401)
            row=db.execute('SELECT * FROM bag_access_credentials WHERE cashier_id=?',(user['id'],)).fetchone()
            if not self.credential_matches(row,data.get('current_password')):
                raise DomainError('La contraseña actual no es correcta.',401)
            self.put_password(db,user['id'],password,self.now())
            db.execute('INSERT INTO bag_access_events(cashier_id,action,actor,created_at) VALUES(?,?,?,?)',(user['id'],'password_changed_by_holder',user['id'],self.now()))
        return {'password_set':True,'signed_in':False}

    def session_row(self,db,header):
        token=cookie_value(header,COOKIE)
        if not token:return None
        return db.execute('SELECT s.id session_id,s.expires_at,s.device_name,u.id,u.name,u.email,u.access_role,u.shared FROM bag_access_sessions s JOIN bag_cashiers u ON u.id=s.cashier_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.enabled=1 AND EXISTS(SELECT 1 FROM bag_access_credentials c WHERE c.cashier_id=u.id)',(digest(token),self.now())).fetchone()

    def view(self,header,bag_id):
        with self.store.connect() as db:
            row=self.session_row(db,header)
            if not row:return {'signed_in':False}
            if not isinstance(bag_id,str) or not re.fullmatch(bags.BAG_ID,bag_id):raise DomainError('Abre el QR de la bolsa.')
            bag=bags.row_for(db,bag_id)
            # No stored amounts, colleague names, verification or history leave this projection.
            linked=db.execute('SELECT id,creator_key,responsible_json FROM cl_closures WHERE bag_id=?',(bag['id'],)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='cl_closures'").fetchone() else None
            owned=bool(linked and ('bag:'+row['id'] in (linked['creator_key'],json.loads(linked['responsible_json']).get('key'))))
            return {'signed_in':True,'cashier':{'name':row['name'],'shared':bool(row['shared']),'access_role':row['access_role']},'expires_at':row['expires_at'],
                    'bag':{'id':bag['id'],'label':f'BOL-{bag["number"]:06d}','available':bag['status']=='blank' and not linked},
                    'digital_closure_id':linked['id'] if owned else None}

    def logout(self,header):
        with self.store.connect() as db:
            db.execute('UPDATE bag_access_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL',(self.now(),digest(cookie_value(header,COOKIE))))
        return {'signed_in':False}

    def submit(self,header,bag_id,data,origin,client):
        payload(data,{'request_id','confirmed','business_date','register_name','shift_name','counted','retained','declared_name'})
        self.limits([('submit-ip:'+client,900,30),('submit-bag:'+bag_id,900,12)])
        # execute rechecks the session INSIDE the same write transaction as the declaration,
        # so a concurrent revocation cannot race past the permission check.
        with self.store.connect() as db:
            row=self.session_row(db,header)
            if not row:raise DomainError('Ingresa con tu correo y contraseña para entregar esta bolsa.',401)
        return bags.execute(self.store,'complete',dict(data,version=1),{'role':'bag_cashier','username':row['id']},origin,bag_id,
                            bag_identity=lambda db:self.session_row(db,header))

    def require_admin(self,header):
        with self.store.connect() as db:
            row=self.session_row(db,header)
            if not row or row['access_role']!='access_admin':
                raise DomainError('Se requiere una cuenta autorizada o administrador de accesos de bolsas.',403)
            return 'bag-access:'+row['id']

    def check_admin_transaction(self,db,actor,header):
        if actor.startswith('bag-access:'):
            row=self.session_row(db,header)
            if not row or row['access_role']!='access_admin' or actor!='bag-access:'+row['id']:
                raise DomainError('Acceso de administración revocado.',403)

    def manage(self,data,actor,admin_header=None):
        action=data.get('action');now=self.now()
        if action=='save':
            payload(data,{'action','id','version','email','name','enabled','access_role','shared'})
            email=normalize_email(data.get('email'));name=bags.text(data.get('name'),'Nombre de cajera',100)
            role=data.get('access_role','cashier');shared=data.get('shared',False)
            if role not in {'cashier','access_admin'} or type(shared) is not bool:raise DomainError('Rol o tipo de cuenta no válido.')
            if type(data.get('enabled')) is not bool:raise DomainError('Indica si el acceso está habilitado.')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.check_admin_transaction(db,actor,admin_header)
                target=data.get('id') or uuid4().hex
                old=db.execute('SELECT * FROM bag_cashiers WHERE id=?',(target,)).fetchone()
                duplicate=db.execute('SELECT id FROM bag_cashiers WHERE email=? AND id<>?',(email,target)).fetchone()
                if duplicate:raise DomainError('Ese correo ya está asociado a una cajera.',409)
                if data.get('id') and (not old or type(data.get('version')) is not int or data['version']!=old['version']):raise DomainError('El registro cambió. Vuelve a abrirlo.',409)
                if old:
                    db.execute('UPDATE bag_cashiers SET email=?,name=?,enabled=?,access_role=?,shared=?,version=version+1,updated_at=? WHERE id=?',(email,name,int(data['enabled']),role,int(shared),now,target))
                    if old['email']!=email or old['access_role']!=role or old['shared']!=int(shared) or not data['enabled']:
                        self.revoke(db,target,now)
                        if old['email']!=email or old['shared']!=int(shared):
                            db.execute('DELETE FROM bag_access_credentials WHERE cashier_id=?',(target,))
                else:db.execute('INSERT INTO bag_cashiers(id,email,name,enabled,access_role,shared,version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)',(target,email,name,int(data['enabled']),role,int(shared),now,now))
                after=dict(db.execute('SELECT * FROM bag_cashiers WHERE id=?',(target,)).fetchone())
                db.execute('INSERT INTO bag_access_events(cashier_id,action,actor,created_at,before_json,after_json) VALUES(?,?,?,?,?,?)',(target,'updated' if old else 'created',actor,now,json.dumps(dict(old)) if old else None,json.dumps(after)))
            return {'id':target}
        if action in {'prepare_activation','set_password'}:
            payload(data,{'action','id','version','confirmed'} | ({'password','password_confirmation'} if action=='set_password' else set()))
            if data.get('confirmed') is not True:raise DomainError('Confirma el restablecimiento de acceso y cierre de sesiones.')
            password=self.password_value(data) if action=='set_password' else None
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.check_admin_transaction(db,actor,admin_header)
                user=db.execute('SELECT * FROM bag_cashiers WHERE id=?',(data.get('id'),)).fetchone()
                if not user or not user['enabled']:raise DomainError('Habilita la cuenta registrada antes de establecer su contraseña.',409)
                if type(data.get('version')) is not int or data['version']!=user['version']:raise DomainError('El registro cambió. Vuelve a abrirlo.',409)
                self.revoke(db,user['id'],now)
                if action=='prepare_activation':
                    db.execute('DELETE FROM bag_access_credentials WHERE cashier_id=?',(user['id'],))
                    token=secrets.token_urlsafe(32)
                    db.execute('INSERT INTO bag_access_enrollments VALUES(?,?,?,?,NULL)',(digest(token),user['id'],now,now+ACTIVATION_SECONDS))
                    result={'activation_path':'/bag-activate#token='+token,'expires_at':now+ACTIVATION_SECONDS}
                else:
                    self.put_password(db,user['id'],password,now);result={'password_set':True}
                db.execute('UPDATE bag_cashiers SET version=version+1,updated_at=? WHERE id=?',(now,user['id']))
                db.execute('INSERT INTO bag_access_events(cashier_id,action,actor,created_at) VALUES(?,?,?,?)',(user['id'],action,actor,now))
            return result
        if action=='revoke_device':
            payload(data,{'action','session_id'})
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.check_admin_transaction(db,actor,admin_header)
                row=db.execute('SELECT cashier_id FROM bag_access_sessions WHERE id=?',(data.get('session_id'),)).fetchone()
                if not row:raise DomainError('Dispositivo no encontrado.',404)
                db.execute('UPDATE bag_access_sessions SET revoked_at=? WHERE id=?',(now,data['session_id']))
                db.execute('INSERT INTO bag_access_events(cashier_id,action,actor,created_at,after_json) VALUES(?,?,?,?,?)',(row[0],'device_revoked',actor,now,json.dumps({'session_id':data['session_id'],'revoked_at':now})))
            return {'revoked':True}
        raise DomainError('Acción no disponible.')

    def management(self):
        with self.store.connect() as db:
            return {'cashiers':[dict(r) for r in db.execute('SELECT u.*,EXISTS(SELECT 1 FROM bag_access_credentials c WHERE c.cashier_id=u.id) has_password FROM bag_cashiers u ORDER BY name,email')],
                    'devices':[dict(r) for r in db.execute('SELECT id,cashier_id,device_name,created_at,expires_at FROM bag_access_sessions WHERE revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC',(self.now(),))],
                    'authentication':'registered_email_password'}

    def route(self,handler,path,query,origin):
        header=handler.headers.get('Cookie');secure=origin.startswith('https:')
        if handler.command=='GET' and path==PREFIX+'/session':return handler.send(200,self.view(header,query.get('bag',[''])[0]))
        if handler.command=='POST':
            # Browser writes require an exact Origin, JSON and a custom header. No CORS.
            if handler.headers.get('Origin')!=origin or handler.headers.get('Sec-Fetch-Site') not in {None,'same-origin'}:
                raise DomainError('Origen externo rechazado.',403)
            client=client_key(handler);data=handler.body(maximum=8192)
            if path==PREFIX+'/login':
                token,seconds=self.login(data,client)
                return handler.send(200,{'signed_in':True},extra_headers={'Set-Cookie':cookie(COOKIE,token,seconds,secure)})
            if path==PREFIX+'/activate':return handler.send(200,self.activate(data,client))
            if path==PREFIX+'/change-password':
                result=self.change_password(header,data,client)
                return handler.send(200,result,extra_headers={'Set-Cookie':cookie(COOKIE,'',0,secure)})
            if path==PREFIX+'/logout':
                payload(data,set());return handler.send(200,self.logout(header),extra_headers={'Set-Cookie':cookie(COOKIE,'',0,secure)})
            match=re.fullmatch(PREFIX+r'/('+bags.BAG_ID+r')/deliver',path)
            if match:return handler.send(200,self.submit(header,match[1],data,origin,client))
        raise DomainError('Ruta no disponible.',404)
