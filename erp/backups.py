"""Respaldos privados con SQLite online backup, manifiesto y recuperación aislada."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading

DATABASES = ('private/toteat-reader.sqlite3', 'data/erp-demo.sqlite3', 'private/partner-auth.sqlite3')
SALON_DOCUMENT = 'private/documents/salon/Conteo_semanal_lunes_a_viernes.pdf'
LABELS_DOCUMENT = 'private/documents/salon/bolsas-000001-000020-qr.pdf'


def allowed(name):
    return name in DATABASES or name in (SALON_DOCUMENT,LABELS_DOCUMENT) or bool(re.fullmatch(r'private/receipts/[a-f0-9]{64}\.pdf', name))


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def check_db(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('La base de respaldo no pasó la verificación.')


def regular(root, name):
    path=root/name
    if not allowed(name) or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Archivo de respaldo no válido.')
    return path


def verify_backup(directory):
    directory=Path(directory)
    manifest_path=directory/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('Falta el manifiesto de respaldo.')
    manifest=json.loads(manifest_path.read_text())
    files=manifest.get('files',{})
    if manifest.get('version') != 1 or 'data/erp-demo.sqlite3' not in files:
        raise ValueError('Respaldo incompleto.')
    for name, metadata in files.items():
        file=regular(directory,name)
        if file.stat().st_size != metadata['bytes'] or digest(file) != metadata['sha256']:
            raise ValueError('Un archivo del respaldo no coincide con el manifiesto.')
        if name in DATABASES:check_db(file)
    with closing(sqlite3.connect((directory/'data/erp-demo.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        for filename,sha256,size in db.execute('SELECT filename,sha256,bytes FROM document_attachments'):
            name='private/receipts/'+filename
            metadata=files.get(name)
            if not metadata or metadata['sha256']!=sha256 or metadata['bytes']!=size:
                raise ValueError('El respaldo no contiene una boleta adjunta íntegra.')
    with closing(sqlite3.connect((directory/'data/erp-demo.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dc_assets'").fetchone():
            for sha256,kind,size,content in db.execute('SELECT sha256,storage,bytes,content FROM dc_assets'):
                if kind in ('legacy-salon','legacy-labels'):
                    metadata=files.get(SALON_DOCUMENT if kind=='legacy-salon' else LABELS_DOCUMENT)
                    if not metadata or metadata['sha256']!=sha256 or metadata['bytes']!=size:
                        raise ValueError('El respaldo no contiene el documento Salón íntegro.')
                elif kind!='database' or not content or len(content)!=size or hashlib.sha256(content).hexdigest()!=sha256:
                    raise ValueError('Un documento del respaldo no pasó la verificación.')
    with closing(sqlite3.connect((directory/'data/erp-demo.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cv2_submissions'").fetchone():
            from erp.cashier_v2 import verify_submission
            db.row_factory=sqlite3.Row
            try:
                for submission in db.execute('SELECT * FROM cv2_submissions'):
                    verify_submission(db,submission)
            except Exception as exc:
                raise ValueError('El respaldo V2 no conserva firma, snapshot y evidencia íntegros.') from exc
    return manifest


def create_backup(root, destination=None):
    root=Path(root).resolve();destination=Path(destination or root/'private/backups').resolve()
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=Path(tempfile.mkdtemp(prefix='.incomplete-',dir=destination));temporary.chmod(0o700)
    try:
        # Lector primero: una recepción más antigua puede reprocesarse de forma idempotente.
        for name in DATABASES:
            if not (root/name).exists():
                if name=='data/erp-demo.sqlite3':raise ValueError('No existe una base para respaldar.')
                continue
            source=regular(root,name);target=temporary/name
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as origin, closing(sqlite3.connect(target)) as copy:
                origin.backup(copy,pages=256)
            target.chmod(0o600);check_db(target)
        with closing(sqlite3.connect(temporary/'data/erp-demo.sqlite3')) as db:
            receipts=list(db.execute('SELECT filename,sha256,bytes FROM document_attachments'))
        for filename,sha256,size in receipts:
            name='private/receipts/'+filename;source=regular(root,name)
            if source.stat().st_size!=size or digest(source)!=sha256 or filename!=sha256+'.pdf':
                raise ValueError('Una boleta requiere revisión antes del respaldo.')
            target=temporary/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(source,target);target.chmod(0o600)
        # Existing documents stay in place; new uploads live in SQLite.
        legacy={}
        with closing(sqlite3.connect(temporary/'data/erp-demo.sqlite3')) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dc_assets'").fetchone():
                legacy={r[0]:(r[1],r[2]) for r in db.execute("SELECT storage,sha256,bytes FROM dc_assets WHERE storage<>'database'")}
        for kind,name in [('legacy-salon',SALON_DOCUMENT),('legacy-labels',LABELS_DOCUMENT)]:
            reference=legacy.get(kind)
            if reference or (root/name).exists():
                source=regular(root,name)
                if reference and (digest(source)!=reference[0] or source.stat().st_size!=reference[1]):
                    raise ValueError('Un documento de Salón requiere revisión antes del respaldo.')
                target=temporary/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                shutil.copyfile(source,target);target.chmod(0o600)
        files={str(f.relative_to(temporary)):{'bytes':f.stat().st_size,'sha256':digest(f)} for f in temporary.rglob('*') if f.is_file()}
        now=datetime.now(timezone.utc)
        manifest={'version':1,'created_at':now.isoformat(),'files':files,'toteat_credentials_included':False,
                  'account_password_hashes_included':'private/partner-auth.sqlite3' in files,
                  'reader_requires_configuration':True}
        (temporary/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');(temporary/'manifest.json').chmod(0o600)
        verify_backup(temporary)
        final=destination/('oveja-'+now.strftime('%Y%m%dT%H%M%S%fZ'))
        temporary.rename(final)
        return final
    except Exception:
        shutil.rmtree(temporary)
        raise


def restore_backup(backup, destination):
    """Solo a carpeta NUEVA; nunca reemplaza una instalación ni activa el lector."""
    backup=Path(backup);destination=Path(destination)
    manifest=verify_backup(backup)
    if destination.exists():raise ValueError('La recuperación requiere una carpeta nueva, sin archivos.')
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    try:
        for name in manifest['files']:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(regular(backup,name),target);target.chmod(0o600)
        shutil.copyfile(backup/'manifest.json',destination/'manifest.json');(destination/'manifest.json').chmod(0o600)
        verify_backup(destination)
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


class BackupRunner:
    """Al arrancar y cada 24 h mientras el servidor siga activo; reintento cada 30 min."""
    def __init__(self, root):
        self.root=Path(root);self.stop_event=threading.Event();self.thread=None
        self.status_path=self.root/'private/backup-status.json'

    def status(self):
        try:return json.loads(self.status_path.read_text())
        except (OSError,ValueError):return {'state':'pending','last_success':None}

    def tick(self):
        status=self.status();last=status.get('last_success')
        if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()<86400:return
        try:
            backup=create_backup(self.root)
            status={'state':'ready','last_success':verify_backup(backup)['created_at'],'backup_name':backup.name}
        except Exception:
            # No registrar rutas de boletas, cuentas, clientes ni trazas.
            status={**status,'state':'error','error':'No se pudo verificar el respaldo. Requiere revisión local.'}
        self.status_path.parent.mkdir(exist_ok=True,mode=0o700)
        temporary=self.status_path.with_suffix('.tmp')
        temporary.touch(mode=0o600,exist_ok=True);temporary.write_text(json.dumps(status)+'\n');temporary.replace(self.status_path)

    def start(self):
        def run():
            while not self.stop_event.is_set():
                self.tick()
                if self.stop_event.wait(1800):break
        self.thread=threading.Thread(target=run,name='oveja-backup',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=5)
