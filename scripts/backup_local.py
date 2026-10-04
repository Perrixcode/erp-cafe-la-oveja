"""Crear, verificar o recuperar un respaldo privado sin sobreescribir datos."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from erp.backups import create_backup,verify_backup,restore_backup

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--verify',type=Path)
    parser.add_argument('--restore',type=Path)
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args()
    if args.verify and args.restore:parser.error('Selecciona verificar o recuperar.')
    if args.destination and not args.restore:parser.error('--destination solo se usa al recuperar.')
    try:
        if args.verify:
            result=verify_backup(args.verify);print('Respaldo íntegro · '+str(len(result['files']))+' archivos.')
        elif args.restore:
            if not args.destination:parser.error('Indica --destination con una carpeta NUEVA.')
            print('Recuperado sin activar servicios: '+str(restore_backup(args.restore,args.destination)))
        else:print('Respaldo privado verificado: '+str(create_backup(args.root)))
    except (ValueError,OSError) as error:raise SystemExit('No se completó el respaldo o recuperación: '+str(error))

if __name__=='__main__':main()
