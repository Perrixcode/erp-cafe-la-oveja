#!/usr/bin/env python3
"""Explicit additive migration. Dry run by default; no unrelated Store migrations."""
from contextlib import contextmanager, closing
from pathlib import Path
import argparse,hashlib,json,sqlite3,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp import document_center as dc
from erp.backups import create_backup,verify_backup,restore_backup

class ExistingDatabase:
    def __init__(self,path):self.path=Path(path)
    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=10)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
            with db:yield db

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--allow-empty-salon',action='store_true',help='Only for isolated test environments without the existing PDF.')
    args=parser.parse_args();root=args.data_root.resolve();database=root/'data/erp-demo.sqlite3'
    if not database.is_file() or database.is_symlink():raise SystemExit('Existing database not found or invalid.')
    legacy=root/dc.LEGACY_PATH
    if not args.allow_empty_salon and not legacy.is_file():raise SystemExit('Existing Salón PDF is required; no migration performed.')
    if legacy.exists() and (legacy.is_symlink() or not legacy.resolve().is_relative_to(root) or hashlib.sha256(legacy.read_bytes()).hexdigest()!=dc.LEGACY_SHA):raise SystemExit('Salón PDF differs; no migration performed.')
    labels=root/dc.LABELS_PATH
    if not args.allow_empty_salon and not labels.is_file():raise SystemExit('Existing labels PDF is required; no migration performed.')
    if labels.exists() and (labels.is_symlink() or not labels.resolve().is_relative_to(root) or hashlib.sha256(labels.read_bytes()).hexdigest()!=dc.LABELS_SHA):raise SystemExit('Labels PDF differs; no migration performed.')
    if not args.apply:
        print(json.dumps({'dry_run':True,'tables_to_add':7,'legacy_reference':legacy.exists(),'legacy_moved_or_replaced':False,'business_tables_modified':False,'next':'Run with --apply after reviewing the release and backup plan.'}));return
    backup=create_backup(root);metadata=verify_backup(backup)
    restore=backup.with_name(backup.name+'-document-center-restore-check');restore_backup(backup,restore)
    assert verify_backup(restore)==metadata
    result=dc.migrate(ExistingDatabase(database),root)
    print(json.dumps({'applied':True,'backup':str(backup),'restore_check':str(restore),**result}))
if __name__=='__main__':main()
