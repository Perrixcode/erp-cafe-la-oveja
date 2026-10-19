"""Conservative, layout-aware suggestions. No inferred payment or stock movement."""
from decimal import Decimal
import re
import unicodedata
import json
from erp import payables as ap
from erp.domain import DomainError


def folded(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c)).lower()


def number(value):
    value=value.strip().strip('$|[]() ')
    if not re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,6})?',value):raise ValueError('ambiguous number')
    return Decimal(value.replace('.','').replace(',','.'))


def integer_money(value):
    amount=number(value)
    if amount!=amount.to_integral_value():raise ValueError('fractional total')
    return ap.integer(int(amount),'Importe')


def checked_tax_id(value):
    normalized=ap.tax_id(value)
    digits=normalized[:-1]
    check=11-sum(int(n)*(2+i%6) for i,n in enumerate(reversed(digits)))%11
    expected='0' if check==11 else 'K' if check==10 else str(check)
    if normalized[-1:]!=expected:raise ValueError('RUT checksum')
    return normalized


def reading(text):
    if text.lstrip().startswith('{'):
        try:
            value=json.loads(text)
            if isinstance(value,dict) and value.get('reader_version') and isinstance(value.get('text'),str):return value
        except (ValueError,TypeError):pass
    return {'raw_text':text,'text':text,'line_readings':[]}


def suggest(text):
    evidence=reading(text);text=evidence['text']
    result={};plain=folded(text).replace('：',':')
    def unique(pattern,key,convert=lambda x:x,source=text):
        found=set(re.findall(pattern,source,re.I|re.M))
        if len(found)==1:
            try:result[key]=convert(next(iter(found)))
            except (ValueError,DomainError):pass
    money=r'(\d{1,3}(?:\.\d{3})+|\d+)'
    unique(r'^\s*(?:folio|factura\s*(?:electronica)?\s*(?:n[º°o.]*)?|n[º°o’\'])\s*[:#.-]?\s*(\d{1,20})\s*[|\]]?\s*$','folio',source=plain)
    unique(r'^\s*(?:monto\s+total|total\s+a\s+pagar|total)\s*[:$ ]+'+money+r'\s*(?:CLP)?\s*[|\])]?$','amount',integer_money,text)
    for field,label in [('net',r'monto\s+neto'),('tax',r'monto\s+i\.?v\.?a\.?(?:\s*\(19%\))?'),('exempt',r'monto\s+exento')]:
        unique(r'^\s*'+label+r'\s*[:$ ]+'+money+r'\s*[|\])]?$' ,field,lambda x:ap.integer(int(x.replace('.','')),field,0),plain)
    for field,label in [('issued_on',r'(?:fecha\s+(?:de\s+)?emision|emision)'),('due_on',r'(?:fecha\s+(?:de\s+)?vencimiento|vencimiento)')]:
        unique(r'^\s*'+label+r'\s*:?\s*(\d{2}[-/]\d{2}[-/]\d{4})\s*$' ,field,lambda x:ap.day('-'.join(reversed(re.split('[-/]',x))),'Fecha'),plain)
    if re.search(r'factura\s*(?:afecta\s*)?electronica',plain):result['document_type']='invoice'
    if re.search(r'factura\s+exenta',plain):result['document_type']='exempt'
    unique(r'^\s*forma\s+(?:de\s+)?pago\s*:?\s*(contado|credito)\s*$','condition',lambda x:'cash' if x=='contado' else 'credit',plain)
    # Issuer RUT only above the first customer/address block; no customer RUT guessing.
    header=re.split(r'cliente\s*:',text,flags=re.I)[0][:1500]
    unique(r'R\.?U\.?T\.?\s*:\s*(\d{1,2}\.?\d{3}\.?\d{3}-[\dkK])','issuer_tax_id',checked_tax_id,header)
    names=re.findall(r'^\s*([\wÁÉÍÓÚáéíóúñÑ &.,-]{2,80}\b(?:SpA|Ltda\.?|S\.A\.))\s*$',header,re.M|re.I)
    if len(set(names))==1:result['issuer_name']=names[0].strip()
    lines=[]
    # Layout-preserving OCR: columns separated by tabs or >=2 spaces. A single
    # sentence is never split into guessed product/amount columns.
    seen_lines=set()
    for raw in text.splitlines():
        cells=[x.strip(' |') for x in re.split(r'\t| {2,}',raw.strip()) if x.strip(' |')]
        if len(cells)<5:continue
        code=cells[0] if re.fullmatch(r'[A-Za-z0-9_-]{3,30}',cells[0]) else ''
        if not code:continue
        description=cells[1]
        if not re.search('[a-zA-Záéíóú]',description):continue
        numeric=[]
        for cell in cells[2:]:
            try:numeric.append((cell,number(cell)))
            except ValueError:pass
        partial='\t' in raw and len(cells)==5 and '?' in cells[2:]
        if partial:
            def maybe(value):
                try:return number(value)
                except ValueError:return None
            qty,price,amount=(maybe(c) for c in cells[2:])
        else:
            if len(numeric)<3:continue
            qty,price=numeric[0][1],numeric[1][1];amount=numeric[-1][1]
        if any(v is not None and v<=0 for v in (qty,price,amount)) or (amount is not None and amount!=amount.to_integral_value()):continue
        formats=re.findall(r'(?<![\w])(?:(?:caja\s+)?\d+\s*x\s*\d+\s*(?:kilos?|kg|g|litros?|l)|(?<![\w])(?:bid[oó]n\s+)?\d+(?:[.,]\d+)?\s*(?:kilos?|kg|g|litros?))(?![\w])',description,re.I)
        line=dict(code=code,description=description,format=' · '.join(formats),quantity=format(qty,'f') if qty is not None else '',unit_price=format(price,'f') if price is not None else '',discount='0',amount_clp=int(amount) if amount is not None else '',needs_review=True)
        matches=[r for r in evidence.get('line_readings',[]) if r.get('normalized')==description]
        if len(matches)==1:line['reading']=matches[0]
        if partial:line['warning']='Hay cifras de baja confianza: completa los campos vacíos mirando el original.'
        elif qty*price!=amount:line['warning']='Cantidad, precio e importe no coinciden; revisar columnas o descuento.'
        identity=(line['code'],line['description'],line['quantity'],line['unit_price'],line['amount_clp'])
        if identity not in seen_lines:lines.append(line);seen_lines.add(identity)
    if lines:result['lines']=lines
    return result
