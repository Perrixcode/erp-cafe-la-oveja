"""Reglas de negocio puras: estados, resumen y texto para copiar."""

from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange
import re

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


def whatsapp_product(flavor, size):
    people = re.fullmatch(r"(\d+)\s*(?:personas?|pp)", size, flags=re.IGNORECASE)
    label = f"{people[1]}PP" if people else size
    return f"{flavor} *{label}*"


def whatsapp(orders, start, end, stock=None):
    # Formato de copia solamente: no envía mensajes ni cambia stock o pedidos.
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)
            if (first + timedelta(days=n)).weekday() < 5]
    heading_first, heading_last = (days[0], days[-1]) if days else (first, last)
    lines = ["PRODUCTOS POR MARCAR;", f"SEMANA: {heading_first:%d/%m} - {heading_last:%d/%m}"]
    allowed_days = {day.isoformat() for day in days}
    selected = sorted((order for order in orders if order["pickup_at"].split("T")[0] in allowed_days),
                      key=lambda order: order["pickup_at"])
    pending = defaultdict(list)
    summary = {}  # Orden de primera aparición en el período, sin cambiar summarize().
    channels = {"Instagram": "IG", "Presencial": "Local", "Web/Mercat": "Web"}
    for order, item in flatten(selected):
        key = (item["flavor"], item["size"])
        if item["status"] in OUTSTANDING:
            summary[key] = summary.get(key, 0) + item["quantity"]
        if item["status"] in TO_MARK:
            day, hour = order["pickup_at"].split("T")
            product = whatsapp_product(*key)
            channel = channels.get(order["channel"], order["channel"])
            line = f"{product} - {order['customer']} - {hour} hrs - {channel}"
            # Una línea por torta conserva cantidades sin agregar un formato ajeno.
            pending[day].extend([line] * item["quantity"])
    weekdays = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")
    for day in days:
        lines.extend(["", f"*{weekdays[day.weekday()]} {day:%d/%m}*", ""])
        lines.extend(pending[day.isoformat()] or ["“”"])
    lines.extend(["", "RESUMEN ENCARGADAS", ""])
    lines.extend(f"{whatsapp_product(*key)}: {quantity}" for key, quantity in summary.items())
    if not summary:
        lines.append("Sin encargadas pendientes de entrega.")
    lines.extend(["", "STOCK DISPONIBLE:", ""])
    rows = (stock or {}).get("rows", [])
    if not rows:
        lines.append("Desconocido · no se proporcionó stock físico.")
    positions = {key: index for index, key in enumerate(summary)}
    for row in sorted(rows, key=lambda row: positions.get((row["flavor"], row["size"]), len(positions))):
        available = "Desconocido" if row["available"] is None else str(row["available"])
        reserved = "**" if row["reserved"] > 0 else ""
        lines.append(f"{whatsapp_product(row['flavor'], row['size'])}: {available}{reserved}")
    lines.extend(["", "** = Reservada para trozo"])
    return "\n".join(lines)
