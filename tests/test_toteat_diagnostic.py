"""Diagnóstico de conexión: solo red simulada y credencial ficticia."""
import contextlib
import io
import json
import unittest
import ssl
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from scripts.toteat_diagnostic import DiagnosticError, NoRedirect, describe, main, query, connection_error, tls_context

FAKE = 'FICTICIO-NO-ES-CREDENCIAL'
IDS = {'xir':'DEMO','xil':'DEMO','xiu':'DEMO'}


class Response(io.BytesIO):
    status=200


class DiagnosticTests(unittest.TestCase):
    def test_fixed_https_get_and_private_shape(self):
        calls=[]
        def transport(request,timeout):
            calls.append(request)
            self.assertEqual(timeout,15)
            return Response(json.dumps([{'name':'PERSONA FICTICIA','secret':FAKE,'price':100}]).encode())
        result=query('products',{'activeProducts':'true'},IDS,FAKE,transport)
        self.assertEqual(len(calls),1)
        request=calls[0]; parsed=urlsplit(request.full_url)
        self.assertEqual(request.method,'GET')
        self.assertEqual((parsed.scheme,parsed.netloc,parsed.path),('https','api.toteat.com','/mw/or/1.0/products'))
        self.assertEqual(parse_qs(parsed.query)['xapitoken'],[FAKE])
        self.assertEqual({k:v for k,v in result.items() if k != 'schema'},{'root_type':'array','root_count':1,'sample_field_types':['string','string','number'],'sample_field_count':3})
        self.assertNotIn(FAKE,str(result)); self.assertNotIn('PERSONA',str(result))

    def test_errors_never_expose_url_token_or_payload(self):
        for failure in [HTTPError('https://example.invalid/?xapitoken='+FAKE,401,FAKE,None,io.BytesIO(FAKE.encode())), URLError(FAKE),RuntimeError(FAKE)]:
            def transport(*args,**kwargs): raise failure
            with self.assertRaises(DiagnosticError) as caught:
                query('products',{'activeProducts':'true'},IDS,FAKE,transport)
            self.assertNotIn(FAKE,str(caught.exception))
            self.assertNotIn('https://',str(caught.exception))

    def test_redirect_never_forwards_credentials(self):
        with self.assertRaises(DiagnosticError):
            NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid/')

    def test_invalid_scope_and_large_range_never_call_network(self):
        for endpoint,params in [('sale',{}),('products',{'activeProducts':'true','other':'x'}),('sales',{'ini':'20261001','end':'20261003'}),('orderstatus',{'det':'false','oic':'../outside'})]:
            with patch('scripts.toteat_diagnostic.build_opener') as opener:
                with self.assertRaises(DiagnosticError): query(endpoint,params,IDS,FAKE)
                opener.assert_not_called()

    def test_cancel_is_no_network_no_persistence(self):
        with patch('builtins.input',side_effect=['1','DEMO','DEMO','DEMO','CANCELAR']), patch('scripts.toteat_diagnostic.getpass',return_value=FAKE), patch('scripts.toteat_diagnostic.query') as send, patch('builtins.open') as disk, contextlib.redirect_stdout(io.StringIO()) as output:
            main()
            send.assert_not_called(); disk.assert_not_called()
            self.assertIn('No se envió nada',output.getvalue())
            self.assertNotIn(FAKE,output.getvalue())

    def test_user_confirmation_required_before_one_request(self):
        with patch('builtins.input',side_effect=['1','DEMO','DEMO','DEMO','CONSULTAR']), patch('scripts.toteat_diagnostic.getpass',return_value=FAKE), patch('scripts.toteat_diagnostic.query',return_value={'root_type':'array'}) as send, patch('builtins.open') as disk, contextlib.redirect_stdout(io.StringIO()) as output:
            main()
            send.assert_called_once(); disk.assert_not_called()
            self.assertNotIn(FAKE,output.getvalue())

    def test_invalid_and_oversized_json_sanitized(self):
        for payload in [FAKE.encode(), b' '*(2*1024*1024+1)]:
            with self.assertRaises(DiagnosticError) as caught:
                query('products',{'activeProducts':'true'},IDS,FAKE,lambda *a,**kw:Response(payload))
            self.assertNotIn(FAKE,str(caught.exception))

    def test_tls_fallback_keeps_certificate_and_hostname_verification(self):
        context=tls_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_connection_classification_is_safe_and_actionable(self):
        cert=ssl.SSLCertVerificationError(1,FAKE);cert.verify_code=20
        for error,expected in [(URLError(cert),'TLS_CERTIFICATE_VERIFY_FAILED'),(URLError(socket.gaierror(-2,FAKE)),'DNS_ERROR'),(URLError(TimeoutError(FAKE)),'CONNECTION_TIMEOUT'),(URLError(ConnectionRefusedError(FAKE)),'CONNECTION_REFUSED'),(URLError(ssl.SSLError(FAKE)),'TLS_ERROR')]:
            text=connection_error(error)
            self.assertIn(expected,text)
            self.assertNotIn(FAKE,text)
        self.assertIn('verify_code=20',connection_error(cert))

    def test_schema_names_lengths_flags_and_sensitive_redaction(self):
        result=describe({'products':[{'id':'FICTICIO-ID','name':'NO_PUBLICAR_PRODUCTO','categoryId':'FICTICIO-CAT','token':FAKE}], 'meta':{'count':1}, 'success':True})
        text=json.dumps(result)
        self.assertIn('categoryId',text);self.assertIn('first_item_schema',text)
        self.assertNotIn(FAKE,text);self.assertNotIn('FICTICIO-ID',text);self.assertNotIn('NO_PUBLICAR_PRODUCTO',text)
        self.assertEqual(result['provider_flag_interpretation'],'reports_success_payload_not_validated')
        fields=result['schema']['fields'];self.assertEqual(fields[0]['count'],1);self.assertTrue(fields[2]['flag'])
        self.assertEqual(describe({'success':False})['provider_flag_interpretation'],'reports_failure')
        error=describe({'error':True,'code':'UNAUTHORIZED','message':FAKE})
        self.assertIn('UNAUTHORIZED',json.dumps(error));self.assertNotIn(FAKE,json.dumps(error))

    def test_schema_dynamic_keys_and_depth_are_bounded(self):
        value={'contains private words':'VALUE_NOT_PRINTED','products':[{'name':'VALUE_NOT_PRINTED'}]*100}
        text=json.dumps(describe(value))
        self.assertNotIn('contains private words',text);self.assertNotIn('VALUE_NOT_PRINTED',text)
        for _ in range(20):value={'children':[value]}
        self.assertIn('truncated',json.dumps(describe(value)))


if __name__=='__main__': unittest.main()
