"""GET acotados para Linux. Credencial entregada por systemd; jamás se devuelve/loguea."""
from datetime import datetime
import json
import fcntl
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlencode
from urllib.request import Request,HTTPRedirectHandler,HTTPSHandler,build_opener
from urllib.error import HTTPError,URLError
from scripts.probe_toteat_comments import ProbeFailure

_next_request=0.0


def paced_open(open_request,request):
    """Preserve the existing order-status reader's pacing."""
    global _next_request
    delay=_next_request-time.monotonic()
    if delay>0:time.sleep(delay)
    try:return open_request(request,timeout=20)
    finally:_next_request=time.monotonic()+2


def paced_financial_open(open_request,request):
    """Shared <=3/minute budget for documented financial endpoints."""
    root=Path(os.environ.get('OVEJA_DATA_ROOT','/var/lib/oveja-erp'))/'private'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/'toteat-get-budget.lock').open('a+') as budget:
        fcntl.flock(budget,fcntl.LOCK_EX)
        budget.seek(0)
        try:last=float(budget.read() or 0)
        except ValueError:last=0
        delay=max(0,min(21,last+21-time.time()))
        if delay:time.sleep(delay)
        try:return open_request(request,timeout=20)
        finally:
            budget.seek(0);budget.truncate();budget.write(str(time.time()));budget.flush()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def credentials():
    try:
        directory=Path(os.environ['CREDENTIALS_DIRECTORY'])
        value=json.loads((directory/'toteat').read_text())
        if any(not isinstance(value.get(k),str) or not re.fullmatch(r'[0-9]+',value[k]) for k in ('xir','xil','xiu')):raise ValueError()
        token=value['xapitoken']
        if not isinstance(token,str) or not token or any(c.isspace() for c in token):raise ValueError()
        return {k:value[k] for k in ('xir','xil','xiu','xapitoken')}
    except (OSError,KeyError,ValueError):raise ProbeFailure('credential_configuration_required') from None


def fetch(arguments,current=None,opener=None):
    args=list(arguments);mode=args[0] if args else ''
    endpoint='orderstatus'
    if args==['fetch']:params={'listing':'true','det':'true'}
    elif mode=='detail' and len(args)==2 and re.fullmatch(r'[0-9]{1,30}',args[1]):params={'ic':args[1],'det':'true'}
    elif mode=='sales-one-day' and len(args)==2:
        try:datetime.strptime(args[1],'%Y%m%d')
        except ValueError:raise ProbeFailure('invalid_sales_day') from None
        if len(args[1])!=8:raise ProbeFailure('invalid_sales_day')
        endpoint='sales';params={'ini':args[1],'end':args[1]}
    elif mode in {'collection-one-day','accounting-one-day'} and len(args)==2:
        try:
            if len(args[1])!=8 or datetime.strptime(args[1],'%Y%m%d').strftime('%Y%m%d')!=args[1]:raise ValueError()
        except ValueError:raise ProbeFailure('invalid_financial_day') from None
        endpoint='collection' if mode=='collection-one-day' else 'accountingmovements'
        params={'date':args[1]} if endpoint=='collection' else {'initial_date':args[1],'final_date':args[1],'include_sales':'false'}
    elif args==['shift-status']:endpoint='shiftstatus';params={}
    else:raise ProbeFailure('read_mode_not_allowed')
    config=credentials();scope={'restaurant_id':config['xir'],'local_id':config['xil']}
    if current and any(scope[k]!=current[k] for k in scope):raise ProbeFailure('scope_mismatch')
    request=Request('https://api.toteat.com/mw/or/1.0/'+endpoint+'?'+urlencode({**config,**params}),headers={'Accept':'application/json'},method='GET')
    open_request=opener or build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context())).open
    try:
        paced=paced_open if endpoint=='orderstatus' else paced_financial_open
        with (open_request(request,timeout=20) if opener else paced(open_request,request)) as response:raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ProbeFailure('response_too_large')
        if config['xapitoken'].encode() in raw:raise ProbeFailure('credential_reflection_blocked')
        payload=json.loads(raw)
        if not isinstance(payload,dict) or payload.get('ok') is not True:
            message=payload.get('msg') if isinstance(payload,dict) else None
            if isinstance(message,dict) and message.get('texto')=='Not Authorized':raise ProbeFailure('provider_not_authorized',200)
            raise ProbeFailure('provider_success_not_confirmed')
    except HTTPError as error:
        code=error.code
        retry=error.headers.get('Retry-After','') if error.headers else ''
        error.close();failure=ProbeFailure('http_error',code)
        failure.retry_after=min(3600,int(retry)) if retry.isdigit() else 300
        raise failure from None
    except (URLError,OSError,TimeoutError):raise ProbeFailure('network_error') from None
    except ValueError:raise ProbeFailure('invalid_response') from None
    return {'ok':True,'scope':scope,'payload':payload}
