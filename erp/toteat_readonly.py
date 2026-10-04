"""Preparación sin red ni credenciales. No afirma conocer el schema remoto."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

BASE_URL = "https://api.toteat.com/mw/or/1.0/"
ENDPOINTS = {"orderstatus", "sales", "products", "inventorystate"}


def request_plan(endpoint, external_id=None):
    if endpoint not in ENDPOINTS:
        raise ValueError("Endpoint de lectura no permitido.")
    params = {}
    if endpoint == "orderstatus":
        if not external_id or not isinstance(external_id, str):
            raise ValueError("orderstatus requiere un identificador externo oic.")
        params = {"det": "false", "oic": external_id}
    return {
        "mode": "PLAN_ONLY_NO_NETWORK",
        "method": "GET",
        "url": BASE_URL + endpoint + ("?" + urlencode(params) if params else ""),
        "credentials": "NO_CONFIGURADAS",
        "schema": "DESCONOCIDO: verificar con respuesta real autorizada y sanitizada",
        "executable": False,
    }


def read_remote(*_args, **_kwargs):
    raise RuntimeError("Acceso remoto deshabilitado. Requiere autorización específica y handoff seguro de credenciales; este prototipo no hace llamadas Toteat.")


def map_real_response(_payload):
    raise NotImplementedError("Mapeo real pendiente: no se conoce el esquema Toteat. El fixture interno no lo representa.")


def load_internal_example():
    return json.loads((Path(__file__).parent.parent / "fixtures/orders_demo.json").read_text())


def main():
    parser = argparse.ArgumentParser(description="Plan de lectura Toteat. Nunca realiza solicitudes.")
    parser.add_argument("--endpoint", choices=sorted(ENDPOINTS), default="orderstatus")
    parser.add_argument("--oic", default="DEMO-EXTERNO-001")
    args = parser.parse_args()
    print(json.dumps(request_plan(args.endpoint, args.oic), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

