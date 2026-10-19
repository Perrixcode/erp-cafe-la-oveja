"""Explicit one-day GET refresh. Uses existing configured scope and systemd credential.

No daemon, no credential configuration, no movements, no balances. Run only after release approval.
"""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from erp import cash_control, cash_control_sources
from erp.store import Store
from erp.toteat_transport import fetch
from scripts.probe_toteat_comments import ProbeFailure
from scripts.toteat_sales_worker import configuration


def refresh(root,day,endpoints=('collection',),reader=fetch):
    config=configuration(root)
    if not config or config.get('transport')!='systemd':raise ValueError('existing_systemd_integration_required')
    parsed=date.fromisoformat(day)
    if parsed<date(2026,10,16):raise ValueError('before_control_start')
    store=Store(root/'data/erp-demo.sqlite3');cash_control.initialize(store)
    results=[]
    for endpoint in endpoints:
        if endpoint not in {'collection','accountingmovements'}:raise ValueError('endpoint_not_allowed')
        mode='collection-one-day' if endpoint=='collection' else 'accounting-one-day'
        try:
            result=reader([mode,parsed.strftime('%Y%m%d')],config['scope'])
            results.append(cash_control_sources.record(store,day,endpoint,result['payload'],config['scope']))
        except ProbeFailure as error:
            reason={'provider_not_authorized':'Toteat no autoriza accountingmovements con la credencial existente; no se ampliaron permisos.',
                    'http_error':'El proveedor rechazó la consulta HTTP. Revisión requerida.',
                    'network_error':'La lectura de origen no pudo completarse.'}.get(error.code,'Lectura sin éxito confirmado: '+error.code)
            results.append(cash_control_sources.record(store,day,endpoint,scope=config['scope'],error=reason))
            # An authentication or throttling error ends the run; no repeated requests or fallback credential.
            if error.code=='provider_not_authorized' or error.http_status in {401,403,429}:break
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day',required=True);parser.add_argument('--include-accounting',action='store_true')
    args=parser.parse_args();root=Path(os.environ['OVEJA_DATA_ROOT'])
    print(json.dumps(refresh(root,args.day,('collection','accountingmovements') if args.include_accounting else ('collection',)),ensure_ascii=False))

if __name__=='__main__':main()
