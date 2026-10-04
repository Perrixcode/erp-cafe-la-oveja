"""Retira los ejemplos de la instalación local después de verificar un respaldo."""
from pathlib import Path
import json
import sys
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp.store import Store
from erp.maintenance import remove_demo_data
from erp.domain import DomainError
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    try:
        result=remove_demo_data(Store(ROOT/'data/erp-demo.sqlite3'),ROOT/'private/backups')
        print(json.dumps(result,ensure_ascii=False))
    except DomainError as error:
        print(str(error),file=sys.stderr);raise SystemExit(1)
