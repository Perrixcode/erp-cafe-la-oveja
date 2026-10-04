"""Descarga privada del PDF oficial; sin token API ni URLs suministradas por la UI."""
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
import hashlib
import os
import ssl

MAX_PDF = 8 * 1024 * 1024
ALLOWED_HOSTS = {'toteatdte.appspot.com'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_receipt(url, directory, opener=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('receipt_host_not_verified')
    if opener is None:
        ca = '/etc/ssl/cert.pem'
        context = ssl.create_default_context(cafile=ca if Path(ca).is_file() else None)
        opener = build_opener(HTTPSHandler(context=context), NoRedirect())
    request = Request(url, headers={'Accept': 'application/pdf'}, method='GET')
    with opener.open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('receipt_http_error')
        data = response.read(MAX_PDF + 1)
    if len(data) > MAX_PDF or not data.startswith(b'%PDF-'):
        raise ValueError('invalid_receipt_pdf')
    digest = hashlib.sha256(data).hexdigest()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (digest + '.pdf')
    if not path.exists():
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
        os.replace(temporary, path)
    return {'filename': path.name, 'sha256': digest, 'bytes': len(data), 'mime_type': 'application/pdf'}
