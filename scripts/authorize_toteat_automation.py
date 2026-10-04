"""Paso único en Terminal para autorizar el helper de turnos ya preparado."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import date, datetime

ROOT = Path(__file__).resolve().parents[1]


def preflight(root=ROOT):
    """Verifica artefactos sin credenciales, red ni interacción con el llavero."""
    manifest = json.loads((root/'private/toteat-automation-manifest.json').read_text())
    if not isinstance(manifest,dict):
        raise SystemExit('Manifiesto preparado no válido.')
    if manifest.get('version') != 1 or manifest.get('binary') != 'private/oveja-toteat-automation':
        raise SystemExit('Versión preparada no válida.')
    binary = root/manifest['binary']
    if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest.get('binary_sha256'):
        raise SystemExit('La versión cambió; no se solicitó acceso.')
    for filename,key in [('native/ToteatReader.swift','base_source_sha256'),('private/ToteatAutomationReader.generated.swift','generated_source_sha256')]:
        if hashlib.sha256((root/filename).read_bytes()).hexdigest() != manifest.get(key):
            raise SystemExit('El código preparado cambió; no se solicitó acceso.')
    config = json.loads((root/'private/toteat-sales-config.json').read_text())
    if not isinstance(config,dict) or config.get('version')!=1 or config.get('binary') not in ('private/oveja-toteat-diagnostics','private/oveja-toteat-automation'):
        raise SystemExit('Configuración actual no válida; no se solicitó acceso.')
    if hashlib.sha256((root/config['binary']).read_bytes()).hexdigest()!=config.get('binary_sha256'):
        raise SystemExit('El lector actual cambió; no se solicitó acceso.')
    date.fromisoformat(config.get('shift_start',''))
    if datetime.fromisoformat(config.get('start_after','')).tzinfo is None or not isinstance(config.get('scope'),dict) or not all(isinstance(config['scope'].get(k),str) and config['scope'][k] for k in ('restaurant_id','local_id')):
        raise SystemExit('Alcance de ventas no válido; no se solicitó acceso.')
    subprocess.run(['codesign','--verify','--strict',str(binary)],check=True,stderr=subprocess.DEVNULL)
    check = subprocess.run([str(binary),'self-test'],check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15)
    tested = json.loads(check.stdout)
    if not isinstance(tested,dict) or tested.get('ok') is not True:
        raise SystemExit('Self-test rechazado; no se solicitó acceso.')
    return manifest,config,binary


def main():
    if sys.platform != 'darwin' or not sys.stdin.isatty():
        raise SystemExit('Ejecuta este paso desde tu Terminal de macOS.')
    manifest,config,binary = preflight(ROOT)
    print('macOS puede solicitar permiso para este lector de turnos. No ingreses ni compartas el token.')
    process = subprocess.run([str(binary),'authorize-version'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=180)
    result = json.loads(process.stdout)
    if not isinstance(result,dict) or process.returncode != 0 or result.get('ok') is not True or result.get('persistent_access_verified') is not True or result.get('token_exported') is not False:
        raise SystemExit('No se confirmó acceso persistente. Se conserva el lector actual.')
    path = ROOT/'private/toteat-sales-config.json'
    latest = json.loads(path.read_text())
    if latest != config:
        raise SystemExit('La configuración cambió durante la autorización; se conserva sin sobrescribirla.')
    config.update(binary=manifest['binary'],binary_sha256=manifest['binary_sha256'],automatic_shift_lookup=True,enabled=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as file:json.dump(config,file,ensure_ascii=False,indent=2)
    os.replace(temporary,path)
    print('Acceso persistente verificado. La detección de turnos se activará en el próximo ciclo, dentro de 90 segundos.')


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):
        raise SystemExit('No se completó el paso. No se muestran datos privados.')
