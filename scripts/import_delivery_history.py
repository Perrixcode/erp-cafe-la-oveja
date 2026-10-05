"""Importa proyecciones privadas ya consultadas; nunca llama a Toteat.

Ejemplo: python3 scripts/import_delivery_history.py --input private/delivery-history/september-projection.json
La base y el resumen quedan aislados en private/delivery-history/.
"""
import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp import delivery_finance as finance
from erp.store import Store


def import_projection(source, directory, month='2026-09'):
    os.umask(0o077)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/'history-preview.sqlite3'
    store=Store(path);path.chmod(0o600)
    snapshots=json.loads(Path(source).read_text())
    if not isinstance(snapshots,list):raise ValueError('Se espera una lista de proyecciones.')
    rows=[];errors=[];seen_days=set();payments={};raw_fee=Decimal(0)
    for snapshot in snapshots:
        day=snapshot['source_day']
        if not day.startswith(month+'-'):continue
        date.fromisoformat(day)
        if day in seen_days:raise ValueError('Día duplicado en la proyección.')
        seen_days.add(day)
        if not snapshot.get('ok'):
            errors.append({'day':day,'error':snapshot.get('error','source_error')});continue
        if snapshot.get('projection')!='delivery-only-no-customer-data':raise ValueError('Proyección no reconocida.')
        # Verificación independiente del total de las respuestas, antes del SQL.
        for row in snapshot['data']:
            key=(str(snapshot['scope']['restaurant_id']),str(snapshot['scope']['local_id']),str(row['paymentId']))
            if key in payments:raise ValueError('Pago repetido entre respuestas; revisar antes de sumar.')
            payments[key]=day
            for product in row['products']:
                if product.get('id')!=finance.PRODUCT_ID:raise ValueError('La proyección incluye un producto ajeno.')
                raw_fee+=Decimal(str(product['payed']))
        finance.apply_day(store,day,snapshot,snapshot['scope'],snapshot['queried_at'])
    view=finance.view(store,month)
    with store.connect() as db:
        sql=db.execute('SELECT COALESCE(SUM(fee),0),COUNT(*) FROM delivery_transactions WHERE source_day LIKE ?', (month+'-%',)).fetchone()
        history_before=db.execute('SELECT COUNT(*) FROM delivery_finance_history').fetchone()[0]
    if int(raw_fee)!=view['totals']['fee'] or sql[0]!=view['totals']['fee'] or sql[1]!=len(payments):
        raise ValueError('Las respuestas, el SQL y el resumen no coinciden. No presentar un total completo.')
    # Segunda carga de las mismas respuestas acredita persistencia/idempotencia.
    reopened=Store(path)
    for snapshot in snapshots:
        if snapshot['source_day'].startswith(month+'-') and snapshot.get('ok'):
            finance.apply_day(reopened,snapshot['source_day'],snapshot,snapshot['scope'],snapshot['queried_at'])
    with reopened.connect() as db:
        if db.execute('SELECT COUNT(*) FROM delivery_finance_history').fetchone()[0]!=history_before:
            raise ValueError('La recarga alteró el historial.')
    known=[d for d in view['days'] if d['last_success']]
    missing=[d['day'] for d in view['days'] if not d['last_success']]
    report={'month':month,'date_basis':'Día operativo de apertura del turno Toteat; no fecha física de entrega',
            'field':'products[].payed','product_id':finance.PRODUCT_ID,'currency':'CLP',
            'complete':not missing and not errors,'known_days':len(known),'expected_days':len(view['days']),
            'records':sql[1],'fee_clp':sql[0],'daily_sum_clp':sum(d['fee'] or 0 for d in view['days']),
            'missing_days':missing,'errors':errors,'empty_verified_days':[d['day'] for d in known if d['records']==0],
            'review_count':view['totals']['review_count'],'last_source_query':view['last_sync'],
            'checks':{'raw_equals_sql_equals_daily_sum':True,'reload_idempotent':True,'payment_ids_unique':True},
            'daily':[{'day':d['day'],'records':d['records'],'fee_clp':d['fee'],'last_success':d['last_success'],'state':d['state']} for d in view['days']]}
    target=directory/(month+'-summary.json');target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');target.chmod(0o600)
    with (directory/(month+'-daily.csv')).open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerow(['Día operativo Toteat','Registros tarifa','Tarifa CLP','Estado consulta'])
        for d in report['daily']:writer.writerow([d['day'],d['records'],'' if d['fee_clp'] is None else d['fee_clp'],d['state']])
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path('private/delivery-history'))
    parser.add_argument('--month',default='2026-09')
    args=parser.parse_args()
    summary=import_projection(args.input,args.output,args.month)
    print(json.dumps({k:v for k,v in summary.items() if k!='daily'},ensure_ascii=False))
