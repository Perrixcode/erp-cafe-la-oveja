"""Mapeo de ventas cerradas y comentarios; sin red ni escrituras en Toteat."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
import unicodedata

MONTHS = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
WEEKDAYS = ['lunes','martes','miercoles','jueves','viernes','sabado','domingo']


def folded(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.casefold()) if not unicodedata.combining(c))


def parse_comment(original, reference_date):
    if not isinstance(original, str):
        raise ValueError('comment_not_text')
    labels = {'nombre y apellido':'customer','nombre completo':'customer','nombre':'customer','cliente':'customer',
              'numero telefonico':'phone','numero de telefono':'phone','telefono':'phone','fono':'phone','celular':'phone',
              'fecha de retiro':'date','fecha':'date','horario':'time','hora de retiro':'time','hora':'time',
              'estado de pago':'payment_note','estado':'payment_note','plataforma':'platform',
              'comentarios':'notes','comentario':'notes'}
    fields = {}; free = []; pending = None; issues = []; warnings = []
    for raw in original.splitlines():
        line = raw.strip()
        if not line:
            continue
        norm = folded(line)
        matched = False
        for label in sorted(labels, key=len, reverse=True):
            match = re.match(r'^' + re.escape(label) + r'(?:\s*:\s*(.*)|\s*)$', norm)
            if not match:
                continue
            field = labels[label]
            value = line.split(':', 1)[1].strip() if ':' in line else ''
            if field in fields:
                issues.append('campo_repetido_' + field)
            if value:
                fields[field] = value
                pending = None
            else:
                pending = field
            matched = True
            break
        if matched:
            continue
        if pending:
            fields[pending] = line
            pending = None
        else:
            free.append(line)
    # El afiche también se usa escribiendo nombre y teléfono en las primeras líneas.
    if 'customer' not in fields and free and re.search(r'[A-Za-zÁÉÍÓÚÑáéíóúñ]', free[0]) and not folded(free[0]).startswith('prueba'):
        fields['customer'] = free.pop(0)
    if 'phone' not in fields and free and re.fullmatch(r'[+\d ()-]{7,25}', free[0]):
        fields['phone'] = free.pop(0)
    customer = fields.get('customer', '').strip()
    phone = fields.get('phone', '').strip()
    if not customer or len(customer) > 160:
        issues.append('nombre_pendiente')
    if not phone:
        warnings.append('telefono_pendiente')
    elif not re.fullmatch(r'[+\d ()-]{7,25}', phone):
        warnings.append('telefono_por_revisar')
    raw_date = folded(fields.get('date', ''))
    weekday = next((n for n, day in enumerate(WEEKDAYS) if re.search(r'\b'+day+r'\b', raw_date)), None)
    clean_date = re.sub(r'^(?:'+'|'.join(WEEKDAYS)+r')\s*,?\s*', '', raw_date)
    pickup_day = None; inferred = False
    try:
        numeric = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{4}))?', clean_date)
        words = re.fullmatch(r'(\d{1,2})\s+(?:de\s+)?('+'|'.join(MONTHS)+r')(?:\s+(?:de\s+)?(\d{4}))?', clean_date)
        if numeric or words:
            match = numeric or words
            day = int(match[1]); month = int(match[2]) if numeric else MONTHS.index(match[2])+1
            year = int(match[3]) if match[3] else reference_date.year
            pickup_day = date(year, month, day)
            if not match[3]:
                inferred = True
                if pickup_day < reference_date:
                    pickup_day = date(year+1, month, day)
                warnings.append('anio_inferido')
        else:
            pickup_day = date.fromisoformat(clean_date)
        if weekday is not None and pickup_day.weekday() != weekday:
            issues.append('dia_semana_no_coincide')
        if pickup_day < reference_date:
            warnings.append('fecha_pasada')
    except (ValueError, TypeError):
        issues.append('fecha_pendiente')
    hour = re.fullmatch(r'([01]?\d|2[0-3]):([0-5]\d)\s*(?:hrs?\.?|horas?)?', folded(fields.get('time','')))
    if not hour:
        issues.append('horario_pendiente')
    pickup = f'{pickup_day.isoformat()}T{int(hour[1]):02d}:{hour[2]}' if pickup_day and hour else None
    return {'customer':customer,'customer_phone':phone,'pickup_at':pickup,'platform':fields.get('platform',''),
            'payment_note':fields.get('payment_note',''),'notes':'\n'.join([fields.get('notes',''),*free]).strip(),
            'original':original,'warnings':warnings,'issues':issues,'year_inferred':inferred,
            'test_marker':bool(re.search(r'(?im)^\s*prueba[^\n]*(?:no considerar|fictici)',original))}


def channel_from_platform(value):
    return {'local':'Presencial','pos':'Presencial','presencial':'Presencial',
            'ig':'Instagram','instagram':'Instagram','web':'Web/Mercat','mercat':'Web/Mercat'}.get(folded(value.strip()),'No informado')


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (int,float,str)):
        raise ValueError('invalid_money')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('invalid_money') from None
    if not result.is_finite():
        raise ValueError('invalid_money')
    return result


def settlement(transaction):
    closed = transaction.get('dateClosed')
    if not isinstance(closed, str) or not closed:
        raise ValueError('order_not_closed')
    datetime.fromisoformat(closed)
    total, paid, discounts = (amount(transaction.get(k)) for k in ('total','payed','discounts'))
    difference = amount(transaction.get('difference'))
    if total < 0 or paid < 0 or str(transaction.get('fiscalType','')).upper() == 'NC' or transaction.get('referencedPayment') or transaction.get('referencedPayment*'):
        raise ValueError('refund_requires_review')
    if difference != 0:
        raise ValueError('balance_requires_review')
    if total == 0 and paid == 0 and discounts < 0:
        state = 'discount_settled'
    elif total > 0 and paid >= total:
        state = 'paid'
    else:
        raise ValueError('payment_not_settled')
    return {'state':state,'total':str(total),'paid':str(paid),'discounts':str(discounts),
            'date_closed':closed,'payment_id':str(transaction['paymentId']),
            'fiscal_id':str(transaction['fiscalId']) if transaction.get('fiscalId') is not None else None}
