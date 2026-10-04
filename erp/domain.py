"""Reglas de negocio puras: estados, resumen y texto para copiar."""

from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange

STATUSES = {
    "pendiente": "Pendiente de marcado",
    "marcado_solicitado": "Marcado solicitado",
    "marcado": "Marcado físico",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
}
NEXT_STATUS = {"pendiente": "marcado_solicitado", "marcado_solicitado": "marcado", "marcado": "entregado"}
OUTSTANDING = {"pendiente", "marcado_solicitado", "marcado"}
TO_MARK = {"pendiente", "marcado_solicitado"}
CHANNELS = {"Web/Mercat", "Presencial", "Instagram"}
DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def date_range(value, period, end_value=None):
    try:
        selected = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise DomainError("La fecha debe tener formato AAAA-MM-DD.") from None
    if period == "custom":
        try:
            end = date.fromisoformat(end_value)
        except (ValueError, TypeError):
            raise DomainError("Completa la fecha final del rango.") from None
        start = selected
        if end < start:
            raise DomainError("La fecha final no puede ser anterior a la inicial.")
    elif period == "day":
        start = end = selected
    elif period == "week":
        start = selected - timedelta(days=selected.weekday())
        end = start + timedelta(days=6)
    elif period == "biweekly":
        anchor = date(2026, 1, 5)  # Bloques continuos de 14 días, lunes a domingo.
        start = anchor + timedelta(days=((selected - anchor).days // 14) * 14)
        end = start + timedelta(days=13)
    elif period == "month":
        start = selected.replace(day=1)
        end = selected.replace(day=monthrange(selected.year, selected.month)[1])
    else:
        raise DomainError("Período no válido.")
    return start.isoformat(), end.isoformat()


def order_status(items):
    active = {item["status"] for item in items if item["status"] != "cancelado"}
    if not active:
        return "Cancelado"
    if len(active) == 1:
        return STATUSES[next(iter(active))]
    return "Estados mixtos"


def flatten(orders):
    for order in orders:
        for item in order["items"]:
            if item.get("kind") == "torta":
                yield order, item


def summarize(orders):
    groups = defaultdict(int)
    metrics = {"pending": 0, "mark_requested": 0, "marked": 0, "delivered": 0, "canceled": 0}
    metric_keys = dict(zip(STATUSES, metrics))
    for _, item in flatten(orders):
        metrics[metric_keys[item["status"]]] += item["quantity"]
        if item["status"] in OUTSTANDING:
            groups[(item["flavor"], item["size"]) ] += item["quantity"]
    rows = [{"flavor": k[0], "size": k[1], "kind": "torta", "quantity": v}
            for k, v in sorted(groups.items())]
    return {"rows": rows, "metrics": metrics,
            "outstanding": sum(row["quantity"] for row in rows), "stock": None}


def human_day(value):
    day = date.fromisoformat(value)
    return f"{DAYS[day.weekday()]} {day.day} de {MONTHS[day.month - 1]}"


def stock_summary(rows):
    result = [dict(row, available=None if row["physical"] is None else row["physical"] - row["reserved"]) for row in rows]
    known = bool(result) and all(row["available"] is not None for row in result)
    return {"rows": result, "available": sum(row["available"] for row in result) if known else None,
            "reserved": sum(row["reserved"] for row in result)}


def whatsapp(orders, start, end, stock=None):
    # El texto es local: copiarlo nunca abre ni envía WhatsApp.
    lines = ["DATOS FICTICIOS · PRUEBA LOCAL", "PRODUCTOS POR MARCAR"]
    if start != end:
        lines.append(f"Del {human_day(start)} ({start}) al {human_day(end)} ({end}), inclusive")
    else:
        lines.append(human_day(start).capitalize())
    last_day = None
    pending = [(order, item) for order, item in flatten(orders) if item["status"] in TO_MARK]
    for order, item in pending:
        day, hour = order["pickup_at"].split("T")
        if start != end and day != last_day:
            lines.extend(["", human_day(day).capitalize()])
            last_day = day
        destination = "Local" if order["fulfillment"] == "retiro" else "Despacho"
        lines.append(f"{item['quantity']} × {item['flavor']} {item['size']} · {order['customer']} · {hour} · {destination}")
    if not pending:
        lines.append("Sin productos pendientes de marcar.")
    lines.extend(["", "RESUMEN ENCARGADAS", "Pendientes + solicitadas + marcadas; excluye entregadas y canceladas."])
    for row in summarize(orders)["rows"]:
        lines.append(f"{row['quantity']} × {row['flavor']} {row['size']}")
    if not summarize(orders)["rows"]:
        lines.append("Sin encargadas pendientes de entrega.")
    lines.extend(["", "STOCK DISPONIBLE · CONTEO ACTUAL, INDEPENDIENTE DEL PERÍODO"])
    if not stock or not stock["rows"]:
        lines.append("Desconocido · no se proporcionó stock físico.")
    for row in (stock or {}).get("rows", []):
        physical = "Desconocido" if row["physical"] is None else str(row["physical"])
        available = "Desconocido" if row["available"] is None else str(row["available"])
        lines.append(f"{row['flavor']} {row['size']} · físico: {physical} · ** reserva vitrina: {row['reserved']} · venta entera: {available}")
    lines.extend(["", "** = tortas ENTERAS reservadas manualmente para trozar y mantener vitrina.", "Disponibles = físico menos reserva vitrina. No se descuentan encargos otra vez.", "Solo sabores registrados; sin conteo, disponibilidad desconocida."])
    return "\n".join(lines)
