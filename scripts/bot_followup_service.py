"""Servicio local de seguimiento. Solo socket Unix con permisos del sistema."""
import argparse
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.ovejita_followup import FollowupError, prepare, read_histories, resolve


def make_service(database, socket_path):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *_):
            pass  # No nombres, motivos ni contenido de comprobantes en logs.

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16384 or self.headers.get('Content-Type') != 'application/json':
                    raise FollowupError('Solicitud no válida.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise FollowupError('Solicitud no válida.')
                if self.path == '/follow-up':
                    result = resolve(database, data)
                elif self.path == '/history':
                    result = {'rows': read_histories(database, data.get('ids'))}
                else:
                    raise FollowupError('Ruta no disponible.', 404)
                status = 200
            except FollowupError as error:
                status, result = error.status, {'error': str(error)}
            except (ValueError, TypeError, UnicodeError):
                status, result = 400, {'error': 'Solicitud no válida.'}
            except sqlite3.Error:
                status, result = 503, {'error': 'Seguimiento ocupado. Vuelve a intentar en unos segundos.'}
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    prepare(database)
    service = socketserver.ThreadingUnixStreamServer(str(socket_path), Handler)
    service.daemon_threads = True
    os.chmod(socket_path, 0o660)
    return service


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--socket', required=True)
    args = parser.parse_args()
    # El directorio RuntimeDirectory exclusivo se limpia por systemd al detener.
    socket_path = Path(args.socket)
    if socket_path.is_socket():
        socket_path.unlink()
    with make_service(args.database, socket_path) as server:
        server.serve_forever()
