"""Carga ficticia inicial, una sola vez. Las fechas quedan persistidas."""
import json
from datetime import timedelta
from pathlib import Path

from erp.domain import DomainError, NEXT_STATUS


def seed_demo(store, today):
    if store.operating_mode() == 'toteat-local':
        return
    with store.connect() as db:
        if db.execute("SELECT value FROM metadata WHERE key='demo_seed_date'").fetchone():
            return
    fixture = json.loads((Path(__file__).parent.parent / "fixtures/orders_demo.json").read_text())
    for source in fixture["orders"]:
        data = dict(source)
        day = today + timedelta(days=data.pop("day_offset"))
        data["pickup_at"] = day.isoformat() + "T" + data.pop("time")
        status = data.pop("demo_status", "pendiente")
        try:
            order = store.create(data, "Carga demo")
        except DomainError as error:
            if error.status != 409:
                raise
            continue
        for item in order["items"]:
            current = "pendiente"
            while current != status:
                current = NEXT_STATUS[current]
                order = store.transition(order["id"], item["id"], current, "Carga demo", "Estado ficticio de ejemplo", order["version"])
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES('demo_seed_date', ?)", (today.isoformat(),))

