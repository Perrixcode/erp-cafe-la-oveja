"""Único handoff foreground para la versión consolidada ya compilada; no compila ni escribe ACL."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_toteat_comments import ROOT, BINARY, ProbeFailure, validate_manifest, local_context, run_probe


def main(root=ROOT):
    if sys.platform != 'darwin' or not sys.stdin.isatty():
        print('Ejecuta este paso tú en Terminal de este Mac. No pide el token.'); return 1
    manifest = validate_manifest(root)
    local_context(root, manifest)
    binary = root / BINARY
    if subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(binary)], capture_output=True).returncode:
        raise ProbeFailure('prepared_signature_invalid')
    print('Lector consolidado: oveja-toteat-diagnostics. El lector automático actual sigue activo.')
    print('Alcance: leer la misma orden, ventas del 03 al 04 de octubre de 2026 y las mesas del local.')
    print('Se guardan solo coincidencias pertinentes en la carpeta privada; no se escribe en Toteat.')
    print('macOS puede pedir acceso al ítem ERP Oveja · lectura Toteat del llavero inicio de sesión.')
    print('Para autorizar esta versión de forma persistente, elige Permitir siempre en ese diálogo de macOS.')
    print('No vuelvas a ingresar el token. Puedes cancelar; no habrá reintentos automáticos.')
    authorization = subprocess.run([str(binary), 'authorize-version'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        result = json.loads(authorization.stdout)
    except (ValueError, TypeError):
        raise ProbeFailure('invalid_authorization_result') from None
    if (authorization.returncode or not isinstance(result, dict) or result.get('ok') is not True
            or result.get('persistent_access_verified') is not True or result.get('token_exported') is not False):
        status = result.get('osstatus') if isinstance(result, dict) else None
        raise ProbeFailure('persistent_access_not_verified', osstatus=status if type(status) is int else None)
    print('Acceso verificado desde una segunda ejecución. Iniciando las lecturas acotadas.')
    return run_probe(root)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ProbeFailure as error:
        print(json.dumps(error.safe())); raise SystemExit(1)
    except KeyboardInterrupt:
        print('\nCancelado. No se reintentará automáticamente.'); raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print('No se pudo completar el paso preparado. No se muestran datos privados.'); raise SystemExit(1)
