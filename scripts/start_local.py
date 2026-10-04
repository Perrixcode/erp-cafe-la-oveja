"""Inicia localhost separado de la terminal; sin instalar servicios ni tocar Toteat."""
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
import json
import os
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'


def healthy():
    try:
        with urlopen(URL + '/api/health', timeout=1) as response:
            data = json.load(response)
        return data.get('ok') is True and data.get('catalog_mode') == 'local'
    except (OSError, URLError, ValueError):
        return False


def main():
    if healthy():
        print('ERP ya disponible: ' + URL); return 0
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8765)) == 0:
            print('Puerto 8765 ocupado por otro proceso. No se detuvo ni reemplazó.',file=sys.stderr); return 1
    private = ROOT / 'private'; private.mkdir(mode=0o700,exist_ok=True)
    descriptor = os.open(private / 'local-server.log',os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    with os.fdopen(descriptor,'a') as log:
        process = subprocess.Popen([sys.executable,str(ROOT/'app.py')],cwd=ROOT,
                                   stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    (private/'local-server.pid').write_text(str(process.pid)+'\n')
    for _ in range(30):
        if healthy():
            print('ERP disponible: ' + URL + ' · proceso local ' + str(process.pid)); return 0
        if process.poll() is not None: break
        time.sleep(0.1)
    print('No se pudo verificar el arranque. Revisa private/local-server.log.',file=sys.stderr); return 1


if __name__ == '__main__':raise SystemExit(main())
