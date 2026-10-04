"""Adaptador WSGI del mismo router probado en local. Un proceso, varias hebras."""
from email.message import Message
from http import HTTPStatus
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
from app import handler_for
from erp.store import Store


class WSGITransport:
    def __init__(self,environ):
        self.command=environ['REQUEST_METHOD']
        self.path=environ.get('PATH_INFO','/')+('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')
        self.headers=Message()
        for key,value in environ.items():
            if key.startswith('HTTP_'):self.headers[key[5:].replace('_','-')]=str(value)
        for key in ('CONTENT_TYPE','CONTENT_LENGTH'):
            if environ.get(key):self.headers[key.replace('_','-')]=str(environ[key])
        self.server=SimpleNamespace(server_address=('127.0.0.1',int(environ.get('SERVER_PORT','8010'))))
        self.rfile=environ['wsgi.input'];self.wfile=BytesIO();self.response_headers=[];self.status=500

    def send_response(self,status):self.status=status
    def send_header(self,name,value):self.response_headers.append((name,value))
    def end_headers(self):pass


def create_app(data_root,public_origin):
    root=Path(data_root).resolve()
    handler=handler_for(Store(root/'data/erp-demo.sqlite3'),data_root=root,public_origin=public_origin,transport=WSGITransport)
    def application(environ,start_response):
        request=handler(environ)
        if request.command not in ('GET','POST','PUT'):
            request.send(405,{'error':'Método no permitido.'})
        else:request.safe_dispatch()
        start_response(f'{request.status} {HTTPStatus(request.status).phrase}',request.response_headers)
        return [request.wfile.getvalue()]
    return application


def from_environment():
    return create_app(os.environ['OVEJA_DATA_ROOT'],os.environ['OVEJA_PUBLIC_ORIGIN'])
