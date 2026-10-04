"""Diagnóstico opcional ejecutado por el usuario. GET efímero, sin persistencia."""
import json
import re
import ssl
import socket
import sys
import warnings
from pathlib import Path
from datetime import datetime
from getpass import getpass, GetPassWarning
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener

HOST = 'https://api.toteat.com/mw/or/1.0/'
LIMIT = 2 * 1024 * 1024


class DiagnosticError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DiagnosticError('Redirección bloqueada; no se reenviaron credenciales.')


def tls_context():
    # Python.org macOS puede no tener instalado su enlace CA. Usar el bundle
    # público del sistema en ese caso mantiene CA y hostname verificados.
    defaults = ssl.get_default_verify_paths()
    system_ca = Path('/etc/ssl/cert.pem')
    if sys.platform == 'darwin' and defaults.cafile is None and system_ca.is_file():
        return ssl.create_default_context(cafile=str(system_ca))
    return ssl.create_default_context()


def connection_error(error):
    reason = error.reason if isinstance(error,URLError) else error
    if isinstance(reason,ssl.SSLCertVerificationError):
        code = getattr(reason,'verify_code',None)
        suffix = f' (verify_code={code})' if type(code) is int else ''
        return 'TLS_CERTIFICATE_VERIFY_FAILED'+suffix+': no se pudo validar la cadena de confianza. No desactives TLS.'
    if isinstance(reason,(TimeoutError,socket.timeout)):
        return 'CONNECTION_TIMEOUT: se agotó el tiempo de conexión.'
    if isinstance(reason,socket.gaierror):
        return 'DNS_ERROR: no se pudo resolver api.toteat.com.'
    if isinstance(reason,ConnectionRefusedError):
        return 'CONNECTION_REFUSED: el destino rechazó la conexión.'
    if isinstance(reason,ConnectionResetError):
        return 'CONNECTION_RESET: la conexión se interrumpió.'
    if isinstance(reason,ssl.SSLError):
        return 'TLS_ERROR: falló la negociación HTTPS verificada.'
    return 'NETWORK_ERROR: no se pudo completar la conexión HTTPS. Sin detalles sensibles.'


def validate(endpoint, parameters, identifiers):
    if endpoint not in {'products','sales','orderstatus'}:
        raise DiagnosticError('Solo products, sales y orderstatus están permitidos.')
    if set(identifiers) != {'xir','xil','xiu'} or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',v) for v in identifiers.values()):
        raise DiagnosticError('Identificadores no válidos.')
    expected = {'products':{'activeProducts'},'sales':{'ini','end'},'orderstatus':{'det','oic'}}[endpoint]
    if set(parameters) != expected:
        raise DiagnosticError('Parámetros no permitidos.')
    if endpoint == 'products' and parameters['activeProducts'] != 'true':
        raise DiagnosticError('Este diagnóstico consulta solo productos activos.')
    if endpoint == 'orderstatus' and (parameters['det'] != 'false' or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',parameters['oic'])):
        raise DiagnosticError('Usa un identificador de pedido existente válido.')
    if endpoint == 'sales':
        try:
            start,end = [datetime.strptime(parameters[k],'%Y%m%d') for k in ('ini','end')]
            if any(not re.fullmatch(r'\d{8}',parameters[k]) for k in ('ini','end')) or not 0 <= (end-start).days <= 1:
                raise ValueError
        except (ValueError,TypeError):
            raise DiagnosticError('Fechas AAAAMMDD; rango inicial de cero a un día de diferencia.') from None


def describe(value):
    """Esquema acotado, nombres seguros y flags explícitos. Sin datos de ejemplo."""
    def kind(v):
        return 'null' if v is None else 'boolean' if isinstance(v,bool) else 'object' if isinstance(v,dict) else 'array' if isinstance(v,list) else 'number' if isinstance(v,(int,float)) else 'string'
    flags = {'success','ok','error','haserror','issuccess','has_error','is_success'}
    sensitive = re.compile(r'token|secret|password|authorization|cookie|session|credential|api.?key|customer|cliente|email|phone|telefono|address|direccion|\brut\b',re.I)
    error_codes = {'UNAUTHORIZED','FORBIDDEN','INVALID_TOKEN','INVALID_CREDENTIALS','AUTHENTICATION_FAILED','ACCESS_DENIED','NOT_FOUND','RATE_LIMITED'}
    remaining = [240]
    def schema(item,depth=0):
        remaining[0] -= 1
        node = {'type':kind(item)}
        if isinstance(item,(list,dict)): node['count']=len(item)
        if depth >= 6 or remaining[0] <= 0:
            if isinstance(item,(list,dict)): node['truncated']=True
            return node
        if isinstance(item,list) and item:
            node['first_item_schema']=schema(item[0],depth+1)
        if isinstance(item,dict):
            fields=[]
            for index,(name,child) in enumerate(list(item.items())[:24]):
                if remaining[0] <= 0: break
                is_sensitive = bool(sensitive.search(str(name)))
                safe = isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,47}',name)) and not is_sensitive
                entry={'name':name if safe else f'[redacted_field_{index+1}]'}
                entry.update({'type':'redacted'} if is_sensitive else schema(child,depth+1))
                if safe and type(child) is bool and name.lower() in flags: entry['flag']=child
                if safe and name.lower() in {'code','errorcode','error_code'} and isinstance(child,str) and child.upper() in error_codes:
                    entry['known_error_code']=child.upper()
                fields.append(entry)
            node['fields']=fields
            if len(fields)<len(item):node['truncated']=True
        return node
    result = {'root_type':kind(value)}
    if isinstance(value,(list,dict)):
        result['root_count'] = len(value)
    sample = value[0] if isinstance(value,list) and value else value
    if isinstance(sample,dict):
        result['sample_field_types'] = [kind(v) for v in list(sample.values())[:40]]
        result['sample_field_count'] = len(sample)
    result['schema']=schema(value)
    if isinstance(value,dict):
        failed = any(value.get(k) is False for k in ('success','ok','isSuccess','is_success')) or any(value.get(k) is True for k in ('error','hasError','has_error'))
        positive = any(value.get(k) is True for k in ('success','ok','isSuccess','is_success'))
        result['provider_flag_interpretation']='reports_failure' if failed else 'reports_success_payload_not_validated' if positive else 'no_known_success_flag'
    return result


def query(endpoint, parameters, identifiers, token, open_url=None):
    validate(endpoint,parameters,identifiers)
    if not isinstance(token,str) or not token.strip() or len(token)>4096:
        raise DiagnosticError('Credencial vacía o demasiado larga.')
    # Toteat muestra autenticación por query. La URL vive únicamente en memoria.
    url = HOST + endpoint + '?' + urlencode({**parameters,**identifiers,'xapitoken':token})
    request = Request(url,headers={'Accept':'application/json'},method='GET')
    opener = open_url or build_opener(NoRedirect(),HTTPSHandler(context=tls_context())).open
    try:
        with opener(request,timeout=15) as response:
            if response.status != 200:
                raise DiagnosticError('Respuesta HTTP no exitosa.')
            body = response.read(LIMIT+1)
            if len(body)>LIMIT:
                raise DiagnosticError('Respuesta demasiado grande para este diagnóstico; no se guardó.')
            try:
                value = json.loads(body)
            except (ValueError,UnicodeDecodeError):
                raise DiagnosticError('La respuesta no contiene JSON válido; no se mostró ni guardó.') from None
            return describe(value)
    except HTTPError as error:
        code = error.code
        error.close()
        raise DiagnosticError(f'Toteat respondió HTTP {code}. No se muestran URL, credenciales ni contenido.') from None
    except (URLError,TimeoutError,OSError) as error:
        raise DiagnosticError(connection_error(error)) from None
    except DiagnosticError:
        raise
    except Exception:
        raise DiagnosticError('La consulta no se completó. No se muestran detalles sensibles.') from None


def main():
    print('Diagnóstico manual Toteat · una consulta GET · destino api.toteat.com')
    print('No guarda credenciales ni respuesta. No crea ventas, pedidos, stock ni webhooks.')
    print('La autenticación viaja por query HTTPS según la configuración proporcionada; el proveedor puede registrar su URL.')
    print('Elige 1 products (catálogo activo), 2 sales (rango pequeño), 3 orderstatus (pedido existente).')
    endpoint = {'1':'products','2':'sales','3':'orderstatus'}.get(input('Opción: ').strip())
    if endpoint is None: raise DiagnosticError('Opción no válida. No se realizó ninguna consulta.')
    if endpoint == 'products': parameters={'activeProducts':'true'}
    elif endpoint == 'sales':
        print('Semántica inclusiva/exclusiva de fin aún no verificada. No se asumirá cobertura completa.')
        parameters={'ini':input('Inicio AAAAMMDD: ').strip(),'end':input('Fin AAAAMMDD: ').strip()}
    else: parameters={'det':'false','oic':input('ID externo de un pedido existente: ').strip()}
    identifiers={key:input(f'{label} ({key}): ').strip() for key,label in [('xir','Restaurante'),('xil','Local'),('xiu','Usuario API')]}
    validate(endpoint,parameters,identifiers)
    with warnings.catch_warnings():
        warnings.simplefilter('error',GetPassWarning)
        try:
            token=getpass('Token API (oculto; solo memoria): ')
        except GetPassWarning:
            raise DiagnosticError('Se necesita una terminal que oculte la entrada. No se solicitó el token con eco.') from None
    print(f'Preparado: GET {endpoint} en api.toteat.com. Una solicitud, sin seguir redirecciones.')
    if input('Escribe CONSULTAR para ejecutar tú esta consulta: ').strip() != 'CONSULTAR':
        print('Cancelado. No se envió nada.'); return
    result=query(endpoint,parameters,identifiers,token)
    token=''
    print('Respuesta JSON recibida. Esquema sin datos: nombres seguros, tipos, tamaños de arrays y flags explícitos conocidos.')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('Esto no acredita mapeo, importación ni integración del ERP. No se guardó el catálogo ni datos comerciales.')


if __name__ == '__main__':
    try:
        main()
    except DiagnosticError as error:
        print(str(error))
    except (KeyboardInterrupt,EOFError):
        print('\nCancelado sin persistir credenciales.')
