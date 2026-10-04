"""Consulta manual única de órdenes abiertas; salida sin clientes ni valores comerciales."""
import json
import re
from contextlib import closing
import sqlite3
import sys
import unicodedata
import warnings
from getpass import getpass, GetPassWarning
from pathlib import Path

if __package__ in {None,''}:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.toteat_diagnostic import DiagnosticError, query, describe

ROOT=Path(__file__).resolve().parents[1]
PARAMETERS={'listing':'true','det':'true'}


def local_product_ids(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute("SELECT payload_json FROM catalog_products WHERE source='toteat-manual'").fetchall()
    ids={json.loads(row[0])['source_product']['idToteat'] for row in rows}
    if not ids:raise DiagnosticError('No hay catálogo Toteat local verificado para identificar las tortas.')
    return ids


def comment_evidence(order):
    result=[]; remaining=[3000]
    def visit(value,path='$',depth=0):
        remaining[0]-=1
        if remaining[0]<0 or depth>8:return
        if isinstance(value,dict):
            for key,child in list(value.items())[:100]:
                # Los paths tampoco contienen claves dinámicas ni campos de credenciales.
                safe=isinstance(key,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,47}',key) and not re.search(r'token|secret|password|auth|cookie|session|credential|api.?key',key,re.I)
                childpath=path+'.'+(key if safe else '[campo]')
                if safe and re.search(r'comment|comentario|note|observ|instruction',key,re.I):
                    text=child if isinstance(child,str) else ''
                    normalized=''.join(c for c in unicodedata.normalize('NFD',text).lower() if not unicodedata.combining(c))
                    labels=[label for label,pattern in [('nombre',r'\bnombre\b'),('telefono',r'\b(?:telefono|telefonico)\b'),('fecha',r'\bfecha\b'),('horario',r'\b(?:horario|hora)\b'),('estado_pago',r'\b(?:pagado|estado)\b'),('plataforma',r'\bplataforma\b')] if re.search(pattern,normalized)]
                    result.append({'path':childpath,'type':'string' if isinstance(child,str) else type(child).__name__,'nonempty':bool(text.strip()),'afiche_labels_detected':labels})
                visit(child,childpath,depth+1)
        elif isinstance(value,list):
            for item in value[:100]:visit(item,path+'[]',depth+1)
    visit(order)
    unique={json.dumps(item,sort_keys=True):item for item in result}
    return list(unique.values())[:40]


def inspect_open_orders(payload, product_ids):
    if not isinstance(payload,dict) or payload.get('ok') is not True:
        return {'result':'provider_success_not_confirmed','schema':describe(payload),'imported':0}
    rows=payload.get('data')
    if not isinstance(rows,list) or len(rows)>5000:
        raise DiagnosticError('El listado detallado no tiene el formato documentado o excede el límite. No se importó nada.')
    matches=[]; detailed=0; matching_lines=0
    for row in rows:
        if not isinstance(row,dict):raise DiagnosticError('El listado contiene una orden no válida. No se importó nada.')
        document=row.get('document')
        lines=document.get('line') if isinstance(document,dict) else None
        if not isinstance(lines,list):continue
        detailed+=1
        matched=[line for line in lines if isinstance(line,dict) and line.get('isExtra') is False and type(line.get('productCodeToteat')) is int and line['productCodeToteat'] in product_ids]
        if matched:matches.append(row);matching_lines+=len(matched)
    comments=[item for row in matches for item in comment_evidence(row)]
    paths=sorted({item['path'] for item in comments if item['nonempty']})
    return {'result':'read_only_probe','requested':'orderstatus?listing=true&det=true','orders_returned':len(rows),
            'orders_with_documented_detail':detailed,'orders_matching_local_catalog':len(matches),'matching_product_lines':matching_lines,
            'nonempty_comment_paths':paths,'comment_structure_evidence':comments[:40],
            'matching_order_schema':describe(matches[0]) if matches else None,
            'interpretation':'Los nombres de campos y coincidencias son evidencia de lectura; todavía no prueban un agendamiento válido ni cobertura de órdenes cerradas.',
            'imported':0,'persistent_connection':False}


def main():
    ids=local_product_ids(ROOT/'data/erp-demo.sqlite3')
    print('Prueba de lectura de comandas abiertas · GET orderstatus?listing=true&det=true')
    print('Documentación: https://developers.toteat.com/ · no consulta ventas cerradas.')
    print('Una consulta; sin cambios en Toteat, sin guardar respuesta/credenciales ni importar al ERP.')
    print('Puedes usar una comanda existente. Este script no crea una comanda de prueba ni genera ventas.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',GetPassWarning)
        try:
            identifiers={key:getpass(label+' (oculto): ').strip() for key,label in [('xir','Restaurante'),('xil','Local'),('xiu','Usuario API')]}
            token=getpass('Token API (oculto; solo memoria): ')
        except GetPassWarning:raise DiagnosticError('Usa una terminal que oculte la entrada; no se pidió el token con eco.') from None
    if input('Escribe CONSULTAR para ejecutar tú esta lectura única: ').strip()!='CONSULTAR':
        print('Cancelado. Sin consulta ni persistencia.');return
    result=query('orderstatus',PARAMETERS,identifiers,token,project=lambda payload:inspect_open_orders(payload,ids))
    token=''
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('Comparte solo esta salida de estructura. No pegues tokens, capturas de credenciales ni JSON comercial completo.')


if __name__=='__main__':
    try:main()
    except DiagnosticError as error:print(str(error),file=sys.stderr);raise SystemExit(1)
    except (OSError,sqlite3.Error,ValueError,KeyError):print('No se pudo preparar la lectura local. No se muestran datos privados.',file=sys.stderr);raise SystemExit(1)
    except (KeyboardInterrupt,EOFError):print('\nCancelado sin persistir credenciales.')
