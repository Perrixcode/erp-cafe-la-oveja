import base64
from io import BytesIO
from urllib.parse import quote
from unittest.mock import patch
import zipfile

from erp import document_center as dc, document_xlsx as xlsx
from erp.domain import DomainError
import test_document_center as existing


def parts(default_main=False):
    default = xlsx.WORKBOOK_TYPE if default_main else 'application/xml'
    override = '' if default_main else f'<Override PartName="/xl/workbook.xml" ContentType="{xlsx.WORKBOOK_TYPE}"/>'
    return {
        '[Content_Types].xml': f'<Types xmlns="{xlsx.CT}"><Default Extension="xml" ContentType="{default}"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>{override}<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': f'<Relationships xmlns="{xlsx.REL}"><Relationship Id="r1" Type="{xlsx.DOCREL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{xlsx.SHEET}" xmlns:r="{xlsx.DOCREL}"><sheets><sheet name="Prueba" sheetId="1" r:id="r1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{xlsx.REL}"><Relationship Id="r1" Type="{xlsx.DOCREL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{xlsx.SHEET}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Prueba sintética</t></is></c></row></sheetData></worksheet>',
    }


def workbook(values=None):
    out = BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in (values or parts()).items():
            archive.writestr(name, value)
    return out.getvalue()


class DocumentXlsxTests(existing.DocumentCenterTests):
    def xlsx_payload(self, content=None, name='Formato de trozado á.xlsx', **kwargs):
        payload = self.payload(title='Formato Excel QA', **kwargs)
        payload['file'] = {'name': name, 'base64': base64.b64encode(content or workbook()).decode()}
        return payload

    def test_xlsx_exact_roundtrip_name_headers_roles_and_dedup(self):
        data = workbook(); name = 'Formato de trozado á.xlsx'
        payload = self.xlsx_payload(data, name, state='active')
        doc = self.call('/documents', payload=payload, status=201)['id']
        self.assertEqual(self.call('/documents', payload=payload, status=201)['id'], doc)
        self.call('/documents', payload=dict(payload, request_id=existing.rid()), status=409)
        for role in ('partner', 'cashier', 'production'):
            for suffix in ('', '?download=1'):
                response = self.http('/api/document-center/documents/' + doc + '/file' + suffix, role)
                headers = dict(response.response_headers)
                self.assertEqual(response.status, 200); self.assertEqual(response.wfile.getvalue(), data)
                self.assertEqual(headers['Content-Type'], xlsx.MIME)
                self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
                self.assertIn("filename*=UTF-8''" + quote(name, safe=''), headers['Content-Disposition'])
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertTrue(any(k=='Content-Security-Policy' and "default-src 'none'" in v for k,v in response.response_headers))
        self.assertEqual(self.http('/api/document-center/documents/' + doc + '/file').status, 401)
        for role in ('cashier', 'production'):
            self.call('/documents', role=role, payload=self.xlsx_payload(), status=403)
        self.assertEqual(self.count('dc_documents'), 1)

    def test_xlsx_version_does_not_replace_live_pdf_before_publication(self):
        original = existing.pdf('original PDF QA'); doc = self.create(content=original, state='active')
        payload = self.xlsx_payload(); payload['expected_revision'] = 1
        self.call('/documents/' + doc + '/versions', payload=payload)
        self.assertEqual(self.http('/api/document-center/documents/' + doc + '/file', 'cashier').wfile.getvalue(), original)
        self.assertEqual(self.http('/api/document-center/documents/' + doc + '/file?version=2', 'cashier').status, 404)
        self.state(doc, 'active', 2)
        self.assertEqual(self.http('/api/document-center/documents/' + doc + '/file', 'cashier').wfile.getvalue(), workbook())
        self.assertEqual(self.http('/api/document-center/documents/' + doc + '/file?version=1', 'partner').wfile.getvalue(), original)

    def test_xlsx_default_main_content_type_and_normal_workbook(self):
        for default in (True, False):
            self.assertEqual(xlsx.validate(workbook(parts(default))), xlsx.MIME)

    def test_xlsx_invalid_uploads_leave_no_records(self):
        candidates = [b'<html>renamed</html>', workbook({'readme.txt': 'not a workbook'}), workbook()[:-12]]
        values = parts(); del values['xl/worksheets/sheet1.xml']; candidates.append(workbook(values))
        values = parts(); values['xl/workbook.xml'] = '<invalid/>'; candidates.append(workbook(values))
        for content in candidates:
            self.call('/documents', payload=self.xlsx_payload(content), status=415)
        for name in ('fake.pdf', 'macro.xlsm', 'legacy.xls', 'archive.zip'):
            self.call('/documents', payload=self.xlsx_payload(name=name), status=415)
        self.assertEqual(self.count('dc_assets'), 0); self.assertEqual(self.count('dc_documents'), 0)

    def test_xlsx_macros_external_relationships_and_entities_rejected(self):
        candidates = []
        values = parts(); values['xl/vbaProject.bin'] = b'not executed'; candidates.append(values)
        values = parts(); values['[Content_Types].xml'] = values['[Content_Types].xml'].replace(xlsx.WORKBOOK_TYPE, 'application/vnd.ms-excel.sheet.macroEnabled.main+xml'); candidates.append(values)
        values = parts(); values['xl/_rels/workbook.xml.rels'] = values['xl/_rels/workbook.xml.rels'].replace('Target="worksheets/sheet1.xml"', 'Target="https://example.invalid/" TargetMode="External"'); candidates.append(values)
        values = parts(); values['xl/workbook.xml'] = '<!DOCTYPE workbook [<!ENTITY x "blocked">]>' + values['xl/workbook.xml']; candidates.append(values)
        values = parts(); values['xl/workbook.xml'] = values['xl/workbook.xml'].encode('utf-16'); candidates.append(values)
        for values in candidates:
            with self.subTest(parts=list(values)):
                with self.assertRaises(DomainError) as error: xlsx.validate(workbook(values))
                self.assertEqual(error.exception.status, 415)

    def test_xlsx_paths_duplicate_names_and_expansion_limits(self):
        for name in ('../escape.xml', '/absolute.xml', 'xl/../escape.xml', 'xl\\evil.xml', 'XL/WORKBOOK.XML'):
            values = parts(); values[name] = '<x/>'
            with self.assertRaises(DomainError): xlsx.validate(workbook(values))
        data = workbook()
        for constant, limit in [('MAX_EXPANDED', 100), ('MAX_PART', 100), ('MAX_ENTRIES', 2)]:
            with patch.object(xlsx, constant, limit):
                with self.assertRaises(DomainError) as error: xlsx.validate(data)
                self.assertEqual(error.exception.status, 413)
        with self.assertRaises(DomainError) as error: dc.mime_type(b'PK\x03\x04' + b'0' * dc.MAX_FILE)
        self.assertEqual(error.exception.status, 413)


if __name__ == '__main__':
    import unittest
    unittest.main()
