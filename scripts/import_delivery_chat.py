"""Importa un ZIP auditado en almacenamiento privado. Sin conexión a proveedores ni pagos."""
import argparse,json,os,sys
from pathlib import Path
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp.delivery_chat import import_package
from erp.store import Store

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--month',required=True)
    parser.add_argument('--library-file-id',required=True)
    parser.add_argument('--actor',required=True)
    args=parser.parse_args();os.umask(0o077)
    result=import_package(Store(args.database),args.input,args.month,args.library_file_id,args.actor)
    print(json.dumps(result))
if __name__=='__main__':main()
