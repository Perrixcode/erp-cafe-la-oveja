"""Ephemeral loopback backend for the terminal-only V2 UI integration test."""
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4
import json,sys
from app import handler_for
from erp.store import Store
from erp.partner_auth import PartnerAuth
from erp import cash_closures as cl,cash_bags as bags
with TemporaryDirectory(prefix='oveja-v2-ui-test-') as directory:
 root=Path(directory);store=Store(root/'data/erp-demo.sqlite3');server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(store,data_root=root))
 auth=PartnerAuth(root/'private/partner-auth.sqlite3');auth.create('cajera-v2-qa','Cajera QA','Synthetic-only-password-2026','cashier')
 auth.create('admin-v2-qa','Administración QA','Synthetic-admin-password-2026','partner')
 account={'role':'partner','username':'admin-qa','name':'Admin QA'}
 reg=cl.execute(store,'register',dict(request_id=uuid4().hex,confirmed=True,name='Caja QA'),account)['register_id']
 batch=bags.execute(store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=4),account,'http://127.0.0.1:'+str(server.server_port))
 labels=[b['label'] for b in bags.batch_view(store,batch['batch_id'])['bags']]
 thread=Thread(target=server.serve_forever,daemon=True);thread.start()
 print(json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),register=reg,labels=labels)),flush=True)
 try:sys.stdin.read()
 finally:server.shutdown();server.server_close();thread.join()
