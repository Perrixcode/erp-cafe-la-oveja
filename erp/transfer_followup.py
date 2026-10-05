"""Cliente del seguimiento local; no accede a la base ni credenciales del bot."""
from http.client import HTTPConnection, HTTPException
import json
import socket
from erp.domain import DomainError


def call(socket_path, route, data):
    if not socket_path:
        raise DomainError('El seguimiento del bot todavía no está conectado.', 503)
    connection = HTTPConnection('localhost', timeout=7)
    try:
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(7)
        connection.sock.connect(str(socket_path))
        connection.request('POST', route, json.dumps(data).encode(), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        content = response.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise ValueError()
        result = json.loads(content)
        if response.status != 200:
            raise DomainError(result.get('error', 'No se pudo guardar el seguimiento.'), response.status)
        return result
    except (OSError, ValueError, HTTPException):
        raise DomainError('El seguimiento está temporalmente fuera de servicio. No se confirmó el cambio; reintenta la misma operación.', 503) from None
    finally:
        connection.close()


def histories(socket_path, rows):
    result = {}
    for start in range(0, len(rows), 500):
        for row in call(socket_path, '/history', {'ids': [r['id'] for r in rows[start:start + 500]]})['rows']:
            result[row['id']] = row
    return result
