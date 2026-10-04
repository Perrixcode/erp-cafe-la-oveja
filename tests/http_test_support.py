"""Autenticación HTTP real, únicamente en directorios temporales de pruebas."""
import json
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, Request
from erp.partner_auth import PartnerAuth

PASSWORD = 'Clave-ficticia-QA-2026'
def authenticated_opener(server, directory, role='partner', username='socio-http-qa'):
    auth = PartnerAuth(directory/'partner-auth.sqlite3')
    auth.create(username,'Cuenta ficticia '+role,PASSWORD,role)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    url = 'http://127.0.0.1:'+str(server.server_address[1])
    request = Request(url+'/api/partner/login',data=json.dumps({'username':username,'password':PASSWORD}).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'})
    with opener.open(request,timeout=3) as response:
        assert response.status == 200
    return opener
