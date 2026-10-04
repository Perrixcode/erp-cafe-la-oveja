"""Respaldo incremental privado de fotos y asociaciones archivadas por Ovejita."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def backup_evidence(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    objects = destination / 'objects'
    objects.mkdir(exist_ok=True, mode=0o700)
    entries = {}
    for path in sorted(source.iterdir()) if source.exists() else []:
        if not re.fullmatch(r'(?:[a-f0-9]{64}\.(?:png|jpg|webp)|[1-9][0-9]*\.json)', path.name):
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Archivo de evidencia no válido.')
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if path.suffix != '.json' and path.stem != sha:
            raise ValueError('La foto no coincide con su huella.')
        target = objects / sha
        if not target.exists():
            descriptor, temporary = tempfile.mkstemp(dir=objects, prefix='.copy-')
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(content)
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError('El respaldo de evidencia no pasó la verificación.')
        entries[path.name] = {'sha256': sha, 'bytes': len(content)}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    manifest = destination / (stamp + '.json')
    descriptor = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump({'version': 1, 'files': entries}, output)
    return len(entries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    backup_evidence(args.source, args.destination)
