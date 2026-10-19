"""Isolated loopback demo. Creates only synthetic data in a NEW temporary folder."""
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import make_server
from erp.partner_auth import PartnerAuth
from erp.cash_bags_import import import_manifest
from erp.store import Store


def main():
    directory=Path(tempfile.mkdtemp(prefix='cash-bags-demo-'))
    server=make_server(directory/'synthetic.sqlite3',0,seed=True)
    auth=PartnerAuth(directory/'partner-auth.sqlite3')
    for username,role in [('bolsas-socio-qa','partner'),('bolsas-caja-qa','cashier')]:
        auth.create(username,'Cuenta ficticia de bolsas', 'Demo-bolsas-ficticia-2026',role)
    for name in ['cash-bags-preprinted-000001-000020.json','cash-bags-preprinted-000021-000040.json']:
        manifest=json.loads((ROOT/'erp/data'/name).read_text())
        with Store(directory/'synthetic.sqlite3').connect() as db:
            import_manifest(db,manifest,'Importación ficticia QA')
    print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_address[1])+'/?module=cash','directory':str(directory),'synthetic':True}),flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
