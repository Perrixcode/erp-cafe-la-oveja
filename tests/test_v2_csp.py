"""Check actual HTTP security headers on an isolated server, without login or writes."""
from tempfile import TemporaryDirectory
from pathlib import Path
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen
import unittest
from app import handler_for
from erp.store import Store
class CSPTests(unittest.TestCase):
 def test_local_photo_preview_allowed_without_external_script_or_object_sources(self):
  with TemporaryDirectory() as directory:
   root=Path(directory);server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(Store(root/'data/erp-demo.sqlite3'),data_root=root));thread=Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    with urlopen('http://127.0.0.1:'+str(server.server_port)+'/api/health') as response:
     csp=response.headers['Content-Security-Policy'];self.assertIn("img-src 'self' data: blob:;",csp);self.assertIn("script-src 'self';",csp);self.assertIn("object-src 'none';",csp);self.assertNotIn('unsafe-inline',csp)
   finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
