"""Proyección mínima del panel. SELECT en SQLite mode=ro; salida atómica privada."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile


def export_snapshot(database,destination,names=None,evidence_root=None):
    names=names or {};destination=Path(destination)
    evidence_root=Path(evidence_root or Path(database).parent/'erp-evidence')
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        values=[dict(value) for value in db.execute("""SELECT r.id,r.fecha,r.orden_id,r.monto_esperado,r.codigo,r.registro,r.huella,r.compatible,r.estados,r.alertas,
              c.mesa,c.remitente,COALESCE((SELECT accion FROM resoluciones s WHERE s.revision_id=r.id ORDER BY s.id DESC LIMIT 1),'pendiente') seguimiento
              FROM revisiones r LEFT JOIN revision_contexto c ON c.revision_id=r.id ORDER BY r.id DESC""")]

        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        closures=[{'date':r[0],'updated_at':r[1],'report':json.loads(r[2])} for r in db.execute('SELECT fecha,actualizado,informe FROM cierres_diarios ORDER BY fecha DESC LIMIT 366')] if 'cierres_diarios' in tables else []
        uncertain=0
        if 'bot_salidas' in tables:
            uncertain=db.execute("SELECT COUNT(*) FROM bot_salidas WHERE estado='incierto'").fetchone()[0]
            for closure in closures:
                sent=db.execute('SELECT estado FROM bot_salidas WHERE mensaje_id=?',('cierre:'+closure['date'],)).fetchone()
                closure['send_status']=sent[0] if sent else 'Sin envío'
        alerts=[dict(r) for r in db.execute('SELECT fecha,revision_id,tipo,detalle FROM alertas_operativas ORDER BY fecha DESC LIMIT 20')] if 'alertas_operativas' in tables else []
        health=[dict(r) for r in db.execute('SELECT componente,estado,detalle,fecha FROM salud ORDER BY componente')]
        histories={}
        for value in db.execute('SELECT revision_id,fecha,usuario,accion,motivo FROM resoluciones ORDER BY id'):
            event=dict(value);histories.setdefault(event.pop('revision_id'),[]).append(event)
    # Release SQLite read locks before hashing/copying photographs.
    rows=[]
    for value in values:
        row=dict(value);row['cajera']=names.get(row.pop('remitente'),'Sin identificar (histórico)')
        fingerprint=row.pop('huella');row['photo']=None;row['selected_sale']=None
        if isinstance(fingerprint,str) and len(fingerprint)==64 and all(c in '0123456789abcdef' for c in fingerprint):
            for extension,kind in [('jpg','image/jpeg'),('png','image/png'),('webp','image/webp')]:
                photo=evidence_root/(fingerprint+'.'+extension)
                if not photo.is_file() or photo.is_symlink() or photo.stat().st_size>8*1024*1024:continue
                content=photo.read_bytes()
                if hashlib.sha256(content).hexdigest()!=fingerprint:continue
                output=destination.parent/'photos'/photo.name;output.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
                if not output.exists():
                    descriptor,temporary=tempfile.mkstemp(prefix='.photo-',dir=output.parent)
                    with os.fdopen(descriptor,'wb') as file:file.write(content)
                    os.chmod(temporary,0o640);os.replace(temporary,output)
                row['photo']={'filename':photo.name,'sha256':fingerprint,'bytes':len(content),'type':kind}
            details=evidence_root/(str(row['id'])+'.json')
            if details.is_file() and not details.is_symlink() and details.stat().st_size<1024*1024:
                evidence=json.loads(details.read_text())
                if evidence.get('revision_id')==row['id'] and evidence.get('fingerprint')==fingerprint and str(evidence.get('sale',{}).get('id'))==str(row['orden_id']):row['selected_sale']=evidence
        row['estados']=json.loads(row['estados']);row['alertas']=json.loads(row['alertas'])
        row['history']=histories.get(row['id'],[])
        rows.append(row)
    data={'version':1,'updated_at':datetime.now(timezone.utc).isoformat(),'rows':rows,'health':health,'closures':closures,'alerts':alerts,'uncertain_messages':uncertain}
    destination.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix='.transfers-',dir=destination.parent)
    try:
        with os.fdopen(descriptor,'w') as output:json.dump(data,output,ensure_ascii=False)
        os.chmod(temporary,0o640);os.replace(temporary,destination)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return len(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path);parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--names-config',type=Path)
    args=parser.parse_args();names={}
    if args.names_config:names=json.loads(args.names_config.read_text()).get('nombres',{})
    export_snapshot(args.database,args.destination,names)

if __name__=='__main__':main()
