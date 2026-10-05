"""Evidencia de pedidos web abiertos. Leer pago no confirma entrega ni programación."""
from decimal import Decimal
from erp.toteat_scheduling import amount


def is_web(channel):
    return isinstance(channel, str) and channel.casefold().strip() == 'webstore'


def text(value, limit=300):
    return value[:limit].strip() if isinstance(value, str) else ''


def project(row):
    if not is_web(row.get('channel')):
        return None
    document = row.get('document') or {}
    customer = document.get('customer') or {}
    if not isinstance(customer, dict): customer = {}
    delivery = customer.get('delivery') or {}
    if not isinstance(delivery, dict): delivery = {}
    result = {'source': 'orderstatus', 'customer': text(customer.get('name'), 160),
              'phone': next((text(customer.get(k), 40) for k in ('phoneNumber', 'phoneNumber1', 'phoneNumber2') if text(customer.get(k), 40)), ''),
              'email': text(customer.get('email'), 160),
              'address': ', '.join(text(delivery.get(k)) for k in ('address', 'officeOrApt', 'city') if text(delivery.get(k))),
              'timing': None, 'fulfillment': None, 'payment': None, 'payment_issue': 'payment_missing',
              'receipt_available': False}
    payments = document.get('payments')
    if not isinstance(payments, list) or not payments: return result
    try:
        unique = {}
        for payment in payments:
            if not isinstance(payment, dict): raise ValueError()
            identifier = payment.get('id')
            if type(identifier) not in (str, int) or not str(identifier): raise ValueError()
            identifier = str(identifier)
            if identifier in unique and unique[identifier] != payment: raise ValueError()
            unique[identifier] = payment
        # Solo el contrato observado de un pago completo; parciales/múltiples se revisan.
        if len(unique) != 1:
            result['payment_issue'] = 'multiple_payments_require_review'
            return result
        identifier, payment = next(iter(unique.items()))
        total, paid = amount(payment.get('amount')), amount(payment.get('amountPaid'))
        lines = document.get('line')
        if not isinstance(lines, list) or not lines: raise ValueError()
        line_total = sum((amount(line.get('amountAfterTax')) for line in lines), Decimal(0))
        forms = payment.get('paymentForms')
        if not isinstance(forms, list) or not forms: raise ValueError()
        form_total = sum((amount(f.get('amount')) for f in forms), Decimal(0))
        if (total <= 0 or paid < total or line_total != total or form_total != paid
                or any(f.get('method') != 'MERCAT' for f in forms)
                or amount((payment.get('discount') or {}).get('amount', 0)) != 0):
            result['payment_issue'] = 'payment_requires_review'
            return result
        result.update(payment={'state': 'paid', 'total': str(total), 'paid': str(paid),
                               'payment_id': identifier, 'source': 'orderstatus', 'method': 'MERCAT'},
                      payment_issue=None, receipt_available=bool(text(payment.get('urlDTE'), 4000)))
    except (ValueError, TypeError, AttributeError):
        result['payment_issue'] = 'payment_requires_review'
    return result
