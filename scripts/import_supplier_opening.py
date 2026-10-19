"""Import initial documentary evidence without creating or settling payments."""
import argparse
import json
import os
from pathlib import Path
import sys
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from erp.supplier_opening import import_snapshot
from erp.store import Store

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'database', 'actor'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(import_snapshot(Store(args.database), args.input, args.actor)))

if __name__ == '__main__':
    main()
