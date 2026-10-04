"""Vista previa manual y efímera del catálogo; no importa ni escribe archivos."""
import json
import math
import re
import unicodedata
import warnings
from collections import Counter, defaultdict
from getpass import getpass, GetPassWarning

if __package__:
    from .toteat_diagnostic import DiagnosticError, query, validate
else:
    from toteat_diagnostic import DiagnosticError, query, validate

SOURCE_CATEGORIES = {
    'Tortas enteras': 'TORTAS ENTERAS - ¡Sin opción de escritura!',
    'Dulces enteros': 'DULCES ENTEROS',
}
TARGETS = tuple(SOURCE_CATEGORIES)
REASONS = {
    'outside_scope':'Fuera de las dos categorías autorizadas',
    'not_selected':'Categoría candidata no seleccionada por el usuario',
    'invalid_record':'Registro no objeto',
    'invalid_category':'Categoría sin nombre/ID válido',
    'category_conflict':'ID de categoría con nombres incompatibles',
    'invalid_id':'ID de producto ausente o inválido',
    'identity_conflict':'Mismo ID de producto con registros diferentes',
    'duplicate':'Repetición idéntica omitida',
    'modifier':'Registro marcado como modificador',
    'invalid_fields':'Campos/tipos requeridos sin confirmar',
    'portion':'Nombre o código indica trozo/porción',
    'ambiguous_format':'Formato individual ambiguo: necesita revisión',
}


def label_key(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())


def valid_text(value, maximum=200, allow_empty=False):
    return isinstance(value,str) and len(value)<=maximum and (allow_empty or bool(value.strip())) and all(unicodedata.category(c)[0]!='C' for c in value)


def identifier(value):
    return valid_text(value,128) and value == value.strip()


def words(value):
    folded=unicodedata.normalize('NFKD',value).casefold()
    return re.findall(r'[a-z0-9]+',''.join(c for c in folded if not unicodedata.combining(c)))


def portion_reason(name, code):
    tokens=words(name)+words(code)
    if any(w in {'trozo','trozos','porcion','slice','slices','rebanada','rebanadas'} for w in tokens):
        return 'portion'
    # "20 porciones" puede describir el rendimiento de un entero; no se
    # transforma en inventario de porciones. Texto plural sin número es ambiguo.
    for pos,word in enumerate(tokens):
        if word=='porciones' and (pos==0 or not tokens[pos-1].isdigit()):
            return 'portion'
    if any(w in {'individual','individuales'} for w in tokens):
        return 'ambiguous_format'
    return None


def inspect_catalog(payload):
    if not isinstance(payload,dict) or type(payload.get('ok')) is not bool:
        raise DiagnosticError('Respuesta sin ok booleano confirmado. No se previsualizó el catálogo.')
    if payload['ok'] is not True:
        raise DiagnosticError('Toteat indicó ok=false. No se muestran productos ni el mensaje libre del proveedor.')
    rows=payload.get('data')
    if not isinstance(rows,list):
        raise DiagnosticError('Toteat indicó ok=true, pero data no es una lista. No se previsualizó.')
    if len(rows)>20000:
        raise DiagnosticError('El catálogo supera el límite de esta vista previa.')
    targets={label_key(source_name):label for label,source_name in SOURCE_CATEGORIES.items()}
    names=defaultdict(set); labels=defaultdict(set); counts=Counter(); identities=defaultdict(list)
    for index,row in enumerate(rows):
        if not isinstance(row,dict):continue
        category_id,category=row.get('categoryId'),row.get('category')
        if identifier(category_id) and valid_text(category):
            names[category_id].add(label_key(category));labels[category_id].add(category);counts[category_id]+=1
        if identifier(row.get('id')):
            identities[row['id']].append(index)
    candidates=[]; conflicts=[]
    for category_id,variants in names.items():
        if not (variants & targets.keys()):continue
        entry={'id':category_id,'labels':sorted(labels[category_id]),'row_count':counts[category_id]}
        if len(variants)!=1:
            conflicts.append(entry)
        else:
            candidates.append(dict(entry,target=targets[next(iter(variants))]))
    candidates.sort(key=lambda c:(TARGETS.index(c['target']),c['id']))
    allowed={c['id'] for c in candidates}; conflicting={c['id'] for c in conflicts}
    identity_conflicts=set(); duplicate_indexes=set()
    for product_id,indexes in identities.items():
        if len(indexes)<2:continue
        try:
            fingerprints={json.dumps(rows[index],ensure_ascii=False,sort_keys=True,allow_nan=False) for index in indexes}
        except (TypeError,ValueError):
            identity_conflicts.add(product_id);continue
        if len(fingerprints)>1:identity_conflicts.add(product_id)
        else:duplicate_indexes.update(indexes[1:])
    entries=[]
    for index,row in enumerate(rows):
        entry={'row_number':index+1,'category_id':None,'reason':None}
        if not isinstance(row,dict):
            entry['reason']='invalid_record';entries.append(entry);continue
        category_id,category=row.get('categoryId'),row.get('category')
        if not identifier(category_id) or not valid_text(category):
            entry['reason']='invalid_category'
        elif category_id in conflicting:
            entry.update(category_id=category_id,reason='category_conflict')
        elif category_id not in allowed:
            entry['reason']='outside_scope'
        else:
            entry['category_id']=category_id
            product_id=row.get('id');name=row.get('name');code=row.get('localCode');numeric_id=row.get('idToteat')
            if identifier(product_id):entry['id']=product_id
            if valid_text(name,240):entry['name']=name
            if not identifier(product_id):entry['reason']='invalid_id'
            elif product_id in identity_conflicts:entry['reason']='identity_conflict'
            elif index in duplicate_indexes:entry['reason']='duplicate'
            elif row.get('isModifier') is True:entry['reason']='modifier'
            elif (row.get('isModifier') is not False or not valid_text(name,240) or
                  not valid_text(code,128,allow_empty=True) or not isinstance(row.get('modifiers'),list) or
                  type(numeric_id) not in (int,float) or not math.isfinite(numeric_id)):
                entry['reason']='invalid_fields'
            else:
                entry['reason']=portion_reason(name,code)
                entry.update(id=product_id,name=name,local_code=code,id_toteat=numeric_id,
                             modifier_options=len(row['modifiers']))
        entries.append(entry)
    return {'received':len(rows),'categories':candidates,'category_conflicts':conflicts,
            'missing_targets':[target for target in TARGETS if not any(c['target']==target for c in candidates)],
            'entries':entries}


def select_preview(snapshot, selected_ids):
    allowed={c['id'] for c in snapshot['categories']}
    if not selected_ids or not set(selected_ids)<=allowed or len(selected_ids)!=len(set(selected_ids)):
        raise DiagnosticError('Selecciona sin repetir únicamente IDs de categorías candidatas mostradas.')
    selected=set(selected_ids);products=[];excluded=[];counts=Counter()
    for entry in snapshot['entries']:
        reason=entry['reason']
        if entry['category_id'] in allowed and entry['category_id'] not in selected:
            reason='not_selected'
        if reason:
            counts[reason]+=1
            if entry['category_id'] in selected:
                excluded.append({'row_number':entry['row_number'],'id':entry.get('id'),
                                 'name':entry.get('name'),'reason':reason})
        elif entry['category_id'] in selected:
            products.append({k:v for k,v in entry.items() if k not in {'row_number','reason'}})
    return {'received':snapshot['received'],'categories':[c for c in snapshot['categories'] if c['id'] in selected],
            'products':products,'excluded_counts':dict(counts),'selected_exclusions':excluded,
            'missing_targets':snapshot['missing_targets'],'category_conflicts':snapshot['category_conflicts']}


def display(value):
    # JSON quoting evita nuevas líneas, secuencias ANSI y confusión entre columnas.
    return json.dumps(value,ensure_ascii=False)


def show_categories(snapshot):
    print(f"\nRecibidos {snapshot['received']} registros. Filtro LOCAL; no se envió parámetro de categoría al servidor.")
    print('Categorías candidatas por coincidencia completa de nombre (mayúsculas/espacios normalizados):')
    for index,category in enumerate(snapshot['categories'],1):
        print(f"{index}. {category['target']} | ID real {display(category['id'])} | nombres recibidos {display(category['labels'])} | {category['row_count']} registros")
    for category in snapshot['category_conflicts']:
        print(f"EXCLUIDA: ID {display(category['id'])} tiene nombres incompatibles {display(category['labels'])}.")
    for target in snapshot['missing_targets']:
        print(f'Sin coincidencia inequívoca: {target}. Nombre esperado: {display(SOURCE_CATEGORIES[target])}. No se sustituyó por otra categoría.')


def choose_ids(snapshot, selection):
    pieces=selection.split(',')
    if not all(re.fullmatch(r'[0-9]{1,4}',piece.strip()) for piece in pieces):
        raise DiagnosticError('Usa los números de la lista separados por coma; no se eligió ninguna categoría.')
    indexes=[int(piece.strip())-1 for piece in pieces]
    if len(indexes)!=len(set(indexes)) or any(i<0 or i>=len(snapshot['categories']) for i in indexes):
        raise DiagnosticError('Selección fuera de la lista o repetida. No se mostró ningún producto.')
    return [snapshot['categories'][i]['id'] for i in indexes]


def show_preview(preview):
    print('\nVISTA PREVIA LOCAL · NO IMPORTADA')
    for category in preview['categories']:
        products=[p for p in preview['products'] if p['category_id']==category['id']]
        print(f"\n{category['target']} | categoryId={display(category['id'])} | {len(products)} productos candidatos")
        print('id | idToteat | código local | nombre | opciones de modificadores')
        for product in products:
            print(' | '.join(display(product[key]) for key in ('id','id_toteat','local_code','name','modifier_options')))
    print(f"\nBalance: {preview['received']} recibidos = {len(preview['products'])} candidatos + {sum(preview['excluded_counts'].values())} excluidos.")
    for reason,count in sorted(preview['excluded_counts'].items()):
        print(f'{count}: {REASONS[reason]}')
    if preview['selected_exclusions']:
        print('\nExcluidos dentro de las categorías seleccionadas, para revisar:')
        for row in preview['selected_exclusions']:
            print(f"Fila {row['row_number']} | id={display(row['id'])} | nombre={display(row['name'])} | {REASONS[row['reason']]}")
    print('\nLa categoría y el nombre permiten preselección, no validan receta, tamaño, stock ni unicidad global de IDs.')
    print('Los productos con opciones no se desglosan ni convierten en modificadores. No se muestran precios, imágenes ni descripciones.')
    print('Nada importado, enviado a GitHub ni guardado en disco. Esta salida contiene catálogo real local: no pegarla en el repositorio público.')


def main():
    print('Vista previa de Tortas enteras y Dulces enteros · lectura manual Toteat')
    print('Una petición GET products?activeProducts=true a api.toteat.com; filtro de categorías local después de recibirla.')
    print('Sin importación, ajustes, archivos ni credenciales persistidas. El proveedor recibe autenticación por query HTTPS.')
    identifiers={key:input(f'{label} ({key}): ').strip() for key,label in [('xir','Restaurante'),('xil','Local'),('xiu','Usuario API')]}
    validate('products',{'activeProducts':'true'},identifiers)
    with warnings.catch_warnings():
        warnings.simplefilter('error',GetPassWarning)
        try:token=getpass('Token API (oculto; solo memoria): ')
        except GetPassWarning:raise DiagnosticError('Se necesita una terminal que oculte la entrada del token.') from None
    if input('Escribe CONSULTAR para ejecutar tú la única consulta GET: ').strip()!='CONSULTAR':
        print('Cancelado. No se envió ninguna consulta.');return
    try:
        snapshot=query('products',{'activeProducts':'true'},identifiers,token,project=inspect_catalog)
    finally:
        token=''
    show_categories(snapshot)
    if not snapshot['categories']:
        print('No hay categorías inequívocas seleccionables. No se mostraron ni importaron productos.');return
    selection=input('Revisa IDs y nombres; elige números separados por coma (vacío = cancelar): ').strip()
    if not selection:
        print('Vista previa cancelada. Sin datos guardados ni importados.');return
    selected=choose_ids(snapshot,selection)
    print('Selección: '+', '.join(display(category_id) for category_id in selected))
    if input('Escribe VER para mostrar estos productos solo en tu terminal: ').strip()!='VER':
        print('Vista previa cancelada. Sin datos guardados ni importados.');return
    show_preview(select_preview(snapshot,selected))


if __name__=='__main__':
    try:main()
    except DiagnosticError as error:print(str(error))
    except (KeyboardInterrupt,EOFError):print('\nCancelado. Sin datos ni credenciales persistidos.')
