"""Reject incomplete HTTP bodies before parsing a valid JSON prefix."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json, unittest

from app import handler_for
from erp.domain import DomainError
from erp.store import Store
from erp.wsgi import WSGITransport


class BodyLengthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='oveja-json-body-qa-')
        root=Path(cls.temp.name)
        with patch('app.PartnerAuth'):
            cls.handler=handler_for(Store(root/'data/erp-demo.sqlite3'),data_root=root,
                                    public_origin='https://erp.example.test',transport=WSGITransport)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def request(self, body, declared=None):
        return self.handler({'REQUEST_METHOD':'POST','PATH_INFO':'/unused',
            'HTTP_HOST':'erp.example.test','HTTP_ORIGIN':'https://erp.example.test',
            'HTTP_X_ERP_LOCAL':'1','CONTENT_TYPE':'application/json',
            'CONTENT_LENGTH':str(len(body) if declared is None else declared),'wsgi.input':BytesIO(body)})

    def test_complete_utf8_body_uses_byte_length(self):
        payload={'note':'café'};body=json.dumps(payload,ensure_ascii=False).encode()
        self.assertEqual(self.request(body).body(),payload)

    def test_complete_json_prefix_with_missing_padding_is_rejected(self):
        request=self.request(b'{"confirmed":true}   ',declared=64001)
        with self.assertRaisesRegex(DomainError,'Solicitud incompleta'):request.body(maximum=1024*1024)

    def test_truncated_json_is_rejected_before_parsing(self):
        with self.assertRaisesRegex(DomainError,'Solicitud incompleta'):self.request(b'{"confirmed":',declared=30).body()

    def test_exact_declared_maximum_is_allowed(self):
        body=b'{"confirmed":true}';self.assertEqual(self.request(body).body(maximum=len(body)),{'confirmed':True})

    def test_oversized_declared_body_is_rejected_without_reading(self):
        request=self.request(b'{}',declared=5*1024*1024+1)
        with self.assertRaisesRegex(DomainError,'demasiado grande'):request.body(maximum=5*1024*1024)
        self.assertEqual(request.rfile.tell(),0)

    def test_complete_but_invalid_json_still_fails(self):
        with self.assertRaisesRegex(DomainError,'JSON no válido'):self.request(b'{bad}').body()


if __name__=='__main__':unittest.main()
