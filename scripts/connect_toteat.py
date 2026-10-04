"""Paso local autorizado: ingreso oculto, llavero de archivo y lector al iniciar sesión."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'private'
LABEL='cl.oveja.erp.toteat.reader'

def build():
    PRIVATE.mkdir(exist_ok=True,mode=0o700)
    source=ROOT/'native/ToteatReader.swift';binary=PRIVATE/'oveja-toteat-reader';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    marker=PRIVATE/'toteat-helper.sha256'
    if binary.exists() and marker.exists() and marker.read_text()==digest:
        subprocess.run(['/usr/bin/codesign','--verify',str(binary)],check=True,capture_output=True)
        return binary
    with tempfile.TemporaryDirectory(prefix='oveja-swift-') as cache:
        subprocess.run(['swiftc','-module-cache-path',cache,'-suppress-warnings',str(source),'-o',str(binary),'-framework','Security'],check=True,capture_output=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-','--identifier',LABEL,str(binary)],check=True,capture_output=True)
    result=subprocess.run([str(binary),'self-test'],check=True,capture_output=True,text=True)
    if not json.loads(result.stdout).get('ok'):raise ValueError('helper_self_test')
    marker.write_text(digest);binary.chmod(0o700)
    return binary

def install():
    agent=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    args=[sys.executable,str(ROOT/'scripts/toteat_worker.py')]
    config={'Label':LABEL,'ProgramArguments':args,'WorkingDirectory':str(ROOT),'RunAtLoad':True,
            'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':30,'Umask':63,
            'StandardOutPath':str(PRIVATE/'toteat-worker.log'),'StandardErrorPath':str(PRIVATE/'toteat-worker-error.log')}
    agent.parent.mkdir(parents=True,exist_ok=True)
    if agent.exists():
        previous=plistlib.loads(agent.read_bytes())
        if previous.get('ProgramArguments')!=args:raise ValueError('existing_job_different_project')
    contents=plistlib.dumps(config)
    if not agent.exists() or agent.read_bytes()!=contents:
        temporary=agent.with_suffix('.plist.tmp');temporary.write_bytes(contents);temporary.chmod(0o600);temporary.replace(agent)
    domain=f'gui/{os.getuid()}'
    exists=subprocess.run(['launchctl','print',domain+'/'+LABEL],capture_output=True).returncode==0
    if exists:subprocess.run(['launchctl','bootout',domain+'/'+LABEL],check=True,capture_output=True)
    subprocess.run(['launchctl','bootstrap',domain,str(agent)],check=True,capture_output=True)

def main():
    if sys.platform!='darwin' or not sys.stdin.isatty():
        print('Ejecuta este paso tú en Terminal de este Mac, con entrada oculta.');return 1
    os.umask(0o077)
    print('Conectar Toteat · lectura de comandas abiertas cada 30 segundos.')
    print('Ingresa los cuatro datos ocultos. Se guardan en el llavero local, sin iCloud.')
    print('El lector se iniciará con tu sesión. No modifica Toteat. No pegues el token en el chat.')
    try:
        binary=build()
        process=subprocess.run([str(binary),'setup'],stdout=subprocess.PIPE,text=True)
        result=json.loads(process.stdout)
        if process.returncode or result.get('ok') is not True:
            print('No se guardó la conexión. Código:',result.get('error','setup_failed'));return 1
        install()
        print('Lector instalado. Abre http://127.0.0.1:8765 para revisar recepción y comentarios.')
        print('La primera lectura validará el contrato; recibir una comanda no confirma automáticamente su agendamiento.')
    except (OSError,ValueError,subprocess.SubprocessError):
        print('No se pudo completar la instalación local. No se muestran datos privados.');return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
