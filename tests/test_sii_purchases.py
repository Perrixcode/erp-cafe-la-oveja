"""RCV CSV format regressions; only fictional supplier and invoice data."""
import csv
import io
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from erp import payables as ap, sii_purchases as sii
from erp.domain import DomainError
from erp.store import Store


HEADER = 'Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;Fecha Recepcion;Fecha Acuse;Monto Exento;Monto Neto;Monto IVA Recuperable;Monto Iva No Recuperable;Codigo IVA No Rec.;Monto Total;Monto Neto Activo Fijo;IVA Activo Fijo;IVA uso Comun;Impto. Sin Derecho a Credito;IVA No Retenido;Tabacos Puros;Tabacos Cigarrillos;Tabacos Elaborados;NCE o NDE sobre Fact. de Compra;Codigo Otro Impuesto;Valor Otro Impuesto;Tasa Otro Impuesto'.split(';')


def document(**changes):
    values = {'Nro': '1', 'Tipo Doc': '33', 'Tipo Compra': 'Del Giro',
              'RUT Proveedor': '76.111.111-1', 'Razon Social': 'Proveedor Ficticio QA',
              'Folio': '123', 'Fecha Docto': '31/07/2026',
              'Fecha Recepcion': '01/08/2026', 'Monto Exento': '0',
              'Monto Neto': '10000', 'Monto IVA Recuperable': '1900',
              'Monto Total': '11900', 'NCE o NDE sobre Fact. de Compra': '0'}
    values.update(changes)
    return [values.get(name, '') for name in HEADER]


def csv_file(rows, delimiter=';', encoding='utf-8-sig'):
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=delimiter, lineterminator='\r\n')
    writer.writerow(HEADER)
    writer.writerows(rows)
    return output.getvalue().encode(encoding)


def tax_detail(**changes):
    return document(**{'Nro': '', 'Monto Total': '', 'Monto Exento': '',
                       'Monto Neto': '', 'Monto IVA Recuperable': '',
                       'NCE o NDE sobre Fact. de Compra': '',
                       'Codigo Otro Impuesto': '271', 'Valor Otro Impuesto': '100',
                       'Tasa Otro Impuesto': '18', **changes})


class SiiCsvFormatTests(unittest.TestCase):
    def test_rcv_trailing_separator_preserves_fields_and_issue_date(self):
        for encoding in ('utf-8-sig', 'cp1252'):
            for delimiter in (';', ','):
                for suffix in ([], [''], ['', '', ' ']):
                    with self.subTest(encoding=encoding, delimiter=delimiter, suffix=suffix):
                        values = document(**{'Razon Social': 'Café; "Ficticio", QA', 'Tasa Otro Impuesto': '2'})
                        row = sii.parse(csv_file([values + suffix], delimiter, encoding))[0]
                        self.assertEqual(row['status'], 'new')
                        self.assertEqual(row['tax_id'], '761111111')
                        self.assertEqual(row['name'], 'Café; "Ficticio", QA')
                        self.assertEqual(row['folio'], '123')
                        self.assertEqual(row['amount'], 11900)
                        self.assertEqual(row['issued_on'], '2026-07-31')
                        self.assertEqual(row['original'], dict(zip(HEADER, values)))

    def test_nonempty_surplus_or_missing_columns_remain_blocked(self):
        for values in (document() + ['unexpected'], document() + ['', 'unexpected'], document()[:-1]):
            with self.subTest(columns=len(values)):
                row = sii.parse(csv_file([values]))[0]
                self.assertEqual(row['status'], 'error')
                self.assertIn('columnas', row['issue'])

    def test_extra_internal_cell_does_not_shift_required_fields_back_into_place(self):
        values = document()[:5] + ['unexpected'] + document()[5:]
        row = sii.parse(csv_file([values]))[0]
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['issue'], 'Folio no válido.')

    def test_required_field_validation_is_not_bypassed(self):
        for field, value in (('RUT Proveedor', ''), ('Folio', ''), ('Monto Total', 'abc'), ('Fecha Docto', 'wrong')):
            with self.subTest(field=field):
                row = sii.parse(csv_file([document(**{field: value}) + ['']]))[0]
                self.assertEqual(row['status'], 'error')

    def test_duplicate_rows_and_credit_notes_stay_blocked(self):
        rows = sii.parse(csv_file([document() + [''], document() + ['', ''],
                                  document(**{'Tipo Doc': '61', 'Folio': '456'}) + ['']]))
        self.assertEqual([r['status'] for r in rows], ['conflict', 'conflict', 'unsupported'])

    def test_adjacent_tax_details_attach_without_summing_or_changing_row_numbers(self):
        rows = sii.parse(csv_file([document() + [''], tax_detail() + [''],
                                  tax_detail(**{'Codigo Otro Impuesto': '27'}) + [''],
                                  document(**{'Folio': '124'}) + ['']]))
        self.assertEqual([r['row'] for r in rows], [2, 5])
        self.assertEqual([r['status'] for r in rows], ['new', 'new'])
        self.assertEqual(rows[0]['amount'], 11900)
        self.assertEqual([d['row'] for d in rows[0]['tax_details']], [3, 4])
        self.assertEqual(rows[0]['tax_details'][0]['original']['Valor Otro Impuesto'], '100')

    def test_orphan_mismatched_or_incomplete_tax_rows_remain_errors(self):
        for changes in ({'Folio': '999'}, {'RUT Proveedor': '76222222-2'},
                        {'Fecha Docto': '01/08/2026'}, {'Nro': '2'},
                        {'Monto Neto': '100'}, {'Valor Otro Impuesto': ''},
                        {'Tasa Otro Impuesto': 'bad'}):
            with self.subTest(changes=changes):
                rows = sii.parse(csv_file([document(), tax_detail(**changes) + ['']]))
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[1]['status'], 'error')
        self.assertEqual(sii.parse(csv_file([tax_detail()]))[0]['status'], 'error')

    def test_tax_detail_cannot_be_imported_separately_and_source_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'qa.sqlite3')
            content = csv_file([document() + [''], tax_detail() + ['']])
            batch = sii.upload(store, content, 'RCV_QA.csv', 'QA CSV')
            self.assertEqual(batch['tax_detail_count'], 1)
            self.assertEqual(batch['counts']['new'], 1)
            with self.assertRaises(DomainError):
                sii.confirm(store, batch['id'], dict(rows=[2, 3], confirmed=True), 'QA CSV')
            self.assertEqual(ap.view(store)['invoices'], [])
            self.assertEqual(sii.confirm(store, batch['id'], dict(rows=[2], confirmed=True), 'QA CSV')['imported'], 1)
            self.assertEqual(ap.view(store)['invoices'][0]['amount'], 11900)
            self.assertEqual(sii.preview(store, batch['id'])['counts']['duplicate'], 1)
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT content FROM ap_import_batches').fetchone()[0], content)

    def test_tax_rows_count_toward_file_limit(self):
        with self.assertRaisesRegex(DomainError, '3.000'):
            sii.parse(csv_file([document()] + [tax_detail()] * 3000))

    def test_selection_retry_and_paid_duplicate_across_file_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'qa.sqlite3')
            content = csv_file([document() + [''],
                                document(**{'Nro': '2', 'Folio': '124'}) + ['']])
            batch = sii.upload(store, content, 'RCV_QA.csv', 'QA CSV')
            self.assertEqual(batch['counts']['new'], 2)
            # The exact input stays available as evidence, including its separators.
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT content FROM ap_import_batches WHERE id=?', (batch['id'],)).fetchone()[0], content)
            selected = dict(rows=[2], confirmed=True)
            self.assertEqual(sii.confirm(store, batch['id'], selected, 'QA CSV')['imported'], 1)
            self.assertEqual(sii.confirm(store, batch['id'], selected, 'QA CSV')['already_imported'], 1)
            view = ap.view(store)
            self.assertEqual(len(view['invoices']), 1)
            invoice = view['invoices'][0]
            ap.execute(store, 'reconcile', dict(request_id=str(uuid4()), version=invoice['version'],
                       previous_paid=11900, confirmed=True, reason='Pago previo ficticio QA'), 'QA CSV', invoice['id'])
            alternate = sii.upload(store, csv_file([document()]), 'RCV_OTRO_QA.csv', 'QA CSV')
            self.assertEqual(alternate['rows'][0]['status'], 'duplicate')
            self.assertIn('Pagada', alternate['rows'][0]['issue'])
            with self.assertRaises(DomainError):
                sii.confirm(store, alternate['id'], selected, 'QA CSV')
            self.assertEqual(sii.preview(store, batch['id'])['counts']['new'], 1)
            self.assertEqual(len(ap.view(store)['invoices']), 1)


if __name__ == '__main__':
    unittest.main()
