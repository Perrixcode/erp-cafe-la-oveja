"""Lector local independiente. El helper guarda el token y solo permite un GET fijo."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp.toteat_inbox import Inbox,read_inbox,stamp
from scripts.toteat_open_orders_probe import local_product_ids

ROOT=Path(os.environ.get('OVEJA_DATA_ROOT',Path(__file__).resolve().parents[1]))
INTERVAL=30
AUTH_FAILURES={'keychain_access_required','keychain_unavailable','invalid_credentials'}
def next_delay(failures,retry_after=0):return max(min(300,INTERVAL*2**min(failures,4)),min(3600,max(0,retry_after)))
def cycle(inbox,fetch,ids):
    result=fetch()
    if not isinstance(result,dict) or result.get('ok') is not True:
        result=result if isinstance(result,dict) else {}
        code=result.get('error','invalid_helper_result')
        auth=code in AUTH_FAILURES or result.get('http_status') in (400,401,403)
        status={'state':'access_required' if auth else 'retrying','error':code if code in AUTH_FAILURES|{'http_error','network_error','timeout','response_too_large','redirect_blocked','credential_reflection_blocked'} else 'reader_error',
                'checked_at':stamp(),'http_status':result.get('http_status'),'automatic_scheduling':False}
        previous=read_inbox(inbox.path,False)
        if previous.get('last_success'):status['last_success']=previous['last_success']
        inbox.set_status(status)
        retry=result.get('retry_after',0)
        return auth,retry if type(retry) is int else 0
    inbox.apply(result.get('payload'),result.get('scope',{}),ids)
    return False,0
def main():
    private=ROOT/'private';private.mkdir(exist_ok=True,mode=0o700);os.umask(0o077)
    with (private/'toteat-reader.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        inbox=Inbox(private/'toteat-reader.sqlite3')
        failures=0;last_sales=0
        def fetch():
            if os.environ.get('CREDENTIALS_DIRECTORY'):
                from erp.toteat_transport import fetch as fetch_linux
                from scripts.toteat_sales_worker import configuration
                config=configuration(ROOT)
                if not config:return {'ok':False,'error':'invalid_credentials'}
                try:return fetch_linux(['fetch'],config['scope'])
                except Exception:return {'ok':False,'error':'network_error'}
            process=subprocess.run([str(private/'oveja-toteat-reader'),'fetch'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=26)
            if len(process.stdout)>4*1024*1024:raise ValueError('helper_limit')
            return json.loads(process.stdout)
        while True:
            try:
                ids=local_product_ids(ROOT/'data/erp-demo.sqlite3')
                auth,retry=cycle(inbox,fetch,ids)
                if auth:return 0
                if time.monotonic()-last_sales >= 90:
                    last_sales=time.monotonic()
                    try:
                        from scripts.toteat_sales_worker import sales_cycle
                        sales_cycle(inbox)
                        from scripts.toteat_cancellations import cancellation_cycle
                        cancellation_cycle(inbox)
                    except Exception:
                        inbox.set_sales_state('__reader__',{'state':'review_required','error':'contract_or_local_error','checked_at':stamp(),'automatic_scheduling':False})
                if read_inbox(inbox.path,False).get('state')=='receiving':failures=0;delay=INTERVAL
                else:failures+=1;delay=next_delay(failures,retry)
            except Exception:
                failures+=1;delay=next_delay(failures)
                previous=read_inbox(inbox.path,False)
                inbox.set_status({'state':'review_required','error':'contract_or_local_error','checked_at':stamp(),'last_success':previous.get('last_success'),'automatic_scheduling':False})
            time.sleep(delay)
if __name__=='__main__':raise SystemExit(main())
