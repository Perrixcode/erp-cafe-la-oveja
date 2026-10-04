import contextlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from scripts.toteat_diagnostic import DiagnosticError,query
from scripts.toteat_open_orders_probe import PARAMETERS,inspect_open_orders,main

FAKE='FICTICIO-TOKEN-DE-PRUEBA'
IDS={'xir':'DEMO','xil':'DEMO','xiu':'DEMO'}


def payload(comment=True):
    row={'orderId':90001,'channel':'pos','orderStatus':201,'document':{'customer':{'name':'CLIENTE_FICTICIO_NO_MOSTRAR','phoneNumber':'000000000'},'line':[{'lineNumber':1,'productCodeToteat':9001,'productCode':'DEMO-PRODUCT','quantity':1,'isExtra':False}]}}
    if comment:row['document']['line'][0]['comment']='Nombre y apellido: CLIENTE_FICTICIO_NO_MOSTRAR; Número telefónico: 000000000; Fecha: lunes 5 de octubre; Horario: 18:00; Estado: Pagado; Plataforma: Local'
    return {'ok':True,'data':[row]}


class ProbeTests(unittest.TestCase):
    def test_documented_get_scope_single_call(self):
        calls=[]
        class Response(io.BytesIO):status=200
        def transport(request,timeout):
            calls.append(request);return Response(json.dumps(payload()).encode())
        result=query('orderstatus',PARAMETERS,IDS,FAKE,transport,project=lambda p:inspect_open_orders(p,{9001}))
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].method,'GET')
        url=urlsplit(calls[0].full_url);params=parse_qs(url.query)
        self.assertEqual(url.path,'/mw/or/1.0/orderstatus')
        self.assertEqual(params['listing'],['true']);self.assertEqual(params['det'],['true'])
        self.assertNotIn('ic',params);self.assertNotIn('oic',params)
        self.assertEqual(result['imported'],0);self.assertFalse(result['persistent_connection'])

    def test_matching_catalog_and_comment_paths_without_business_values(self):
        result=inspect_open_orders(payload(),{9001})
        self.assertEqual(result['orders_matching_local_catalog'],1)
        self.assertEqual(result['nonempty_comment_paths'],['$.document.line[].comment'])
        self.assertEqual(set(result['comment_structure_evidence'][0]['afiche_labels_detected']),{'nombre','telefono','fecha','horario','estado_pago','plataforma'})
        text=json.dumps(result)
        for private in ('CLIENTE_FICTICIO_NO_MOSTRAR','000000000','DEMO-PRODUCT','90001','18:00'):self.assertNotIn(private,text)

    def test_missing_comment_not_fabricated_and_other_products_not_matched(self):
        self.assertEqual(inspect_open_orders(payload(False),{9001})['nonempty_comment_paths'],[])
        self.assertEqual(inspect_open_orders(payload(),{9999})['orders_matching_local_catalog'],0)
        self.assertEqual(inspect_open_orders({'ok':True,'data':[]},{9001})['orders_returned'],0)

    def test_invalid_listing_and_success_flags(self):
        for data in ({},None,'unknown',[False]):
            with self.assertRaises(DiagnosticError):inspect_open_orders({'ok':True,'data':data},{9001})
        self.assertEqual(inspect_open_orders({'ok':False,'msg':FAKE},{9001})['result'],'provider_success_not_confirmed')
        self.assertNotIn(FAKE,json.dumps(inspect_open_orders({'ok':False,'msg':FAKE},{9001})))

    def test_cancel_does_not_query_or_write(self):
        with patch('scripts.toteat_open_orders_probe.local_product_ids',return_value={9001}),patch('scripts.toteat_open_orders_probe.getpass',side_effect=['DEMO','DEMO','DEMO',FAKE]),patch('builtins.input',return_value='CANCELAR'),patch('scripts.toteat_open_orders_probe.query') as send,patch('builtins.open') as write,contextlib.redirect_stdout(io.StringIO()):
            main();send.assert_not_called();write.assert_not_called()

    def test_confirmation_calls_only_documented_read_no_files(self):
        with patch('scripts.toteat_open_orders_probe.local_product_ids',return_value={9001}),patch('scripts.toteat_open_orders_probe.getpass',side_effect=['DEMO','DEMO','DEMO',FAKE]),patch('builtins.input',return_value='CONSULTAR'),patch('scripts.toteat_open_orders_probe.query',return_value={'imported':0}) as send,patch('builtins.open') as write,contextlib.redirect_stdout(io.StringIO()) as output:
            main();send.assert_called_once();write.assert_not_called()
            self.assertEqual(send.call_args.args[0:2],('orderstatus',PARAMETERS))
            self.assertNotIn(FAKE,output.getvalue())


if __name__=='__main__':unittest.main()
