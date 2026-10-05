"""Copia privada en línea de SQLite Ovejita, incluido el seguimiento compartido."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


def backup_database(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError('Base de Ovejita no disponible para respaldo.')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix='.incomplete-', dir=destination))
    target = directory/'whatsapp.sqlite3'
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as original, closing(sqlite3.connect(target)) as copy:
        original.backup(copy, pages=256)
        if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or copy.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('El respaldo de Ovejita requiere revisión.')
    target.chmod(0o600)
    manifest = {'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'file': target.name, 'bytes': target.stat().st_size,
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (directory/'manifest.json').write_text(json.dumps(manifest)+'\n')
    (directory/'manifest.json').chmod(0o600)
    final = destination/('ovejita-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.rename(final)
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    backup_database(args.database, args.destination)
