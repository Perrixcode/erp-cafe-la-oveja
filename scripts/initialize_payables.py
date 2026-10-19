"""Initialize reviewed balances once, preserving source evidence in SQLite."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp.store import Store
from erp.payable_baseline import initialize
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--input',type=Path,required=True);p.add_argument('--database',type=Path,required=True)
p.add_argument('--actor',required=True);p.add_argument('--expected-total',type=int,required=True);p.add_argument('--expected-rows',type=int,required=True)
a=p.parse_args();print(json.dumps(initialize(Store(a.database),a.input,a.actor,a.expected_total,a.expected_rows),ensure_ascii=False))
