"""Importa evidencia de informes sin modificar repartos ni pagos."""
import argparse,json,os,sys
from pathlib import Path
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp.delivery_audit import import_package
from erp.store import Store

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('input','database','month','library-file-id','actor'):p.add_argument('--'+name,required=True)
    a=p.parse_args();os.umask(0o077)
    print(json.dumps(import_package(Store(a.database),a.input,a.month,a.library_file_id,a.actor)))
if __name__=='__main__':main()
