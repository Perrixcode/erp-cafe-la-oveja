"""Cuentas locales y permisos. Sin cuentas iniciales ni contraseñas predeterminadas."""
from contextlib import closing
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from erp.domain import DomainError
from erp.store import clean_text

COOKIE = 'oveja_partner'
SESSION_SECONDS = 8 * 60 * 60
ROLE_LABELS = {'partner': 'Socio', 'production': 'Producción', 'cashier': 'Caja'}
PERMISSIONS = {
    'partner': {'read', 'receipt', 'request_mark', 'confirm_mark', 'deliver', 'correct', 'stock', 'recipe', 'simulate', 'customer', 'sos'},
    'production': {'read', 'request_mark', 'recipe'},
    'cashier': {'read', 'receipt', 'deliver'},
}


class PartnerAuth:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = []
        if self.path.exists():
            with closing(sqlite3.connect(self.path)) as db, db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(partners)')}
                if columns and 'role' not in columns:
                    db.execute("ALTER TABLE partners ADD COLUMN role TEXT NOT NULL DEFAULT 'partner'")
                if columns and 'enabled' not in columns:
                    db.execute('ALTER TABLE partners ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1')

    @staticmethod
    def derive(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64*1024*1024)

    def configured(self):
        if not self.path.exists():
            return False
        with closing(sqlite3.connect(self.path)) as db:
            return bool(db.execute('SELECT 1 FROM partners WHERE enabled=1 LIMIT 1').fetchone())

    def create(self, username, name, password, role='partner'):
        """Solo herramienta local interactiva; no hay ruta HTTP de alta."""
        username = clean_text(username, 'Usuario', maximum=80).casefold()
        name = clean_text(name, 'Nombre', maximum=80)
        if role not in PERMISSIONS:
            raise DomainError('Perfil no válido.')
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise DomainError('La contraseña debe tener entre 12 y 256 caracteres.')
        salt = secrets.token_bytes(16)
        digest = self.derive(password, salt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True);self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS partners(username TEXT PRIMARY KEY,name TEXT NOT NULL,salt BLOB NOT NULL,digest BLOB NOT NULL,role TEXT NOT NULL DEFAULT 'partner',enabled INTEGER NOT NULL DEFAULT 1)")
            try:
                db.execute('INSERT INTO partners(username,name,salt,digest,role) VALUES(?,?,?,?,?)',(username,name,salt,digest,role))
            except sqlite3.IntegrityError:
                raise DomainError('Ese usuario ya existe; no se cambió su contraseña.') from None

    def login(self, username, password):
        with self.lock:
            moment = time.monotonic()
            self.failures = [t for t in self.failures if moment-t < 300]
            if len(self.failures) >= 5:
                raise DomainError('Demasiados intentos. Espera cinco minutos.',429)
            if not isinstance(username,str) or not isinstance(password,str) or len(username)>80 or len(password)>256:
                self.failures.append(moment)
                raise DomainError('Usuario o contraseña incorrectos.',401)
            row = None
            if self.configured():
                with closing(sqlite3.connect(self.path)) as db:
                    row = db.execute('SELECT username,name,salt,digest FROM partners WHERE username=? AND enabled=1',(username.strip().casefold(),)).fetchone()
            digest = self.derive(password,row[2] if row else b'\0'*16)
            if not row or not hmac.compare_digest(digest,row[3]):
                self.failures.append(moment)
                raise DomainError('Usuario o contraseña incorrectos.',401)
            self.failures.clear()
            self.sessions = {k:v for k,v in self.sessions.items() if v['expires']>moment}
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {'username':row[0],'name':row[1],'expires':moment+SESSION_SECONDS}
            return token

    def token_key(self, header):
        try:
            cookie = SimpleCookie();cookie.load(header or '')
            token = cookie[COOKIE].value if COOKIE in cookie else ''
        except (CookieError,TypeError):
            token = ''
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, header):
        with self.lock:
            key = self.token_key(header)
            result = self.sessions.get(key)
            if result and result['expires'] > time.monotonic():
                # Comprobar de nuevo permite revocar una cuenta sin esperar ocho horas.
                with closing(sqlite3.connect(self.path)) as db:
                    row = db.execute('SELECT name,role FROM partners WHERE username=? AND enabled=1',(result['username'],)).fetchone()
                if row and row[1] in PERMISSIONS:
                    return {'username':result['username'],'name':row[0],'role':row[1],
                            'role_label':ROLE_LABELS[row[1]],'permissions':sorted(PERMISSIONS[row[1]])}
            self.sessions.pop(key,None)
            return None

    def require(self, header):
        partner = self.session(header)
        if not partner or partner['role'] != 'partner':
            raise DomainError('Inicia sesión como socio para cambiar estos datos.',403)
        return partner

    def authorize(self, header, permission):
        user = self.session(header)
        if not user:
            raise DomainError('Inicia sesión para acceder al ERP.',401)
        if permission not in user['permissions']:
            raise DomainError('Tu perfil no tiene permiso para esta operación.',403)
        return user

    def logout(self, header):
        with self.lock:
            self.sessions.pop(self.token_key(header),None)

    @staticmethod
    def cookie(token, clear=False, secure=False):
        return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else SESSION_SECONDS}'+('; Secure' if secure else '')
