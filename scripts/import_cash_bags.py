"""Default is read-only preflight. Applying is a separate coordinated operation."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from erp.cash_bags_import import import_manifest, plan_import
from erp.domain import DomainError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True, help='Existing ERP SQLite path; never creates a database')
    parser.add_argument('--manifest', type=Path, default=ROOT/'erp/data/cash-bags-preprinted-000001-000020.json')
    parser.add_argument('--apply', action='store_true', help='Apply the import after coordinated approval')
    parser.add_argument('--actor', help='Required audit identity only with --apply')
    args = parser.parse_args()
    if args.apply and not args.actor:
        parser.error('--apply requires --actor')
    try:
        manifest = json.loads(args.manifest.read_text())
        with closing(sqlite3.connect(args.db.resolve().as_uri()+('?mode=rw' if args.apply else '?mode=ro'), uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            result = import_manifest(db, manifest, args.actor) if args.apply else plan_import(db, manifest)
        print(json.dumps(dict(result, applied=args.apply and result['status']=='imported'), ensure_ascii=False))
    except (DomainError, sqlite3.Error, OSError, ValueError) as error:
        print(json.dumps({'error': str(error), 'applied': False}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
