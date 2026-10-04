"""Incorporar un catálogo clasificado desde private/, sin red ni autenticación."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp.catalog_import import load_catalog, validate_catalog
from erp.domain import DomainError
from erp.store import Store

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='JSON clasificado dentro de private/ (ignorado por Git)')
    parser.add_argument('--db', type=Path, default=ROOT / 'data/erp-demo.sqlite3')
    parser.add_argument('--actor', default='Operador local')
    parser.add_argument('--apply', action='store_true', help='Aplicar transacción aditiva; sin esta opción solo valida el archivo')
    args = parser.parse_args()
    try:
        if not args.input.resolve().is_relative_to((ROOT / 'private').resolve()):
            raise DomainError('Guarda el catálogo privado dentro de private/, excluido de Git.')
        if not args.db.resolve().is_relative_to((ROOT / 'data').resolve()) or args.db.suffix != '.sqlite3':
            raise DomainError('Usa una base .sqlite3 dentro de data/, excluida de Git.')
        payload = load_catalog(args.input)
        products = validate_catalog(payload)
        if args.apply:
            result = Store(args.db).import_catalog(payload, args.actor)
            print(json.dumps(result, ensure_ascii=False))
            print('Incorporación manual local. Pedidos e historial conservados. Sin sincronización ni movimientos de stock.')
        else:
            print(f'Archivo válido: {len(products)} productos clasificados. No se abrió ni modificó SQLite. Usa --apply para incorporar; allí se comprobarán conflictos con los datos existentes.')
    except (DomainError, OSError):
        # No imprimir ruta ni contenido comercial/credencial ante errores de archivos.
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, DomainError) else 'No se pudo leer o escribir el archivo local.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
