"""GET acotados para Linux. Credencial entregada por systemd; jamás se devuelve/loguea."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import ssl
from urllib.parse import urlencode
from urllib.request import Request,HTTPRedirectHandler,HTTPSHandler,build_opener
from urllib.error import HTTPError,URLError
from scripts.probe_toteat_comments import ProbeFailure


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
    elif args==['shift-status']:endpoint='shiftstatus';params={}
    else:raise ProbeFailure('read_mode_not_allowed')
    config=credentials();scope={'restaurant_id':config['xir'],'local_id':config['xil']}
    if current and any(scope[k]!=current[k] for k in scope):raise ProbeFailure('scope_mismatch')
    request=Request('https://api.toteat.com/mw/or/1.0/'+endpoint+'?'+urlencode({**config,**params}),headers={'Accept':'application/json'},method='GET')
    open_request=opener or build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context())).open
    try:
        with open_request(request,timeout=20) as response:raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ProbeFailure('response_too_large')
        if config['xapitoken'].encode() in raw:raise ProbeFailure('credential_reflection_blocked')
        payload=json.loads(raw)
        if not isinstance(payload,dict) or payload.get('ok') is not True:raise ProbeFailure('provider_success_not_confirmed')
    except HTTPError as error:
        code=error.code;error.close();raise ProbeFailure('http_error',code) from None
    except (URLError,OSError,TimeoutError):raise ProbeFailure('network_error') from None
    except ValueError:raise ProbeFailure('invalid_response') from None
    return {'ok':True,'scope':scope,'payload':payload}
