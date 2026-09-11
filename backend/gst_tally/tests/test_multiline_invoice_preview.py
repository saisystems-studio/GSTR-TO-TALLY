import json
from io import BytesIO
from django.contrib.auth import get_user_model

from django.test import TestCase
from openpyxl import Workbook

from gst_tally.services.import_service import import_file
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.round_off import resolve_round_off


GSTIN = '33AAACY4945P1ZS'


class MultiLineInvoicePreviewTests(TestCase):
    def source(self, extension, invoice='7', lines=None):
        lines = lines or [('19.00', '0.48', '0.48'), ('40647.00', '3658.23', '3658.23')]
        headers = ['Recipient GSTIN', 'Invoice No', 'Invoice Date', 'Taxable Value', 'CGST', 'SGST', 'IGST', 'Cess', 'Invoice Value']
        rows = [[GSTIN, invoice, '04-04-2025', taxable, cgst, sgst, '0', '0', '47983.41'] for taxable, cgst, sgst in lines]
        if extension == 'xlsx':
            book = Workbook()
            book.active.append(headers)
            for row in rows:
                book.active.append(row)
            stream = BytesIO()
            book.save(stream)
            stream.seek(0)
        elif extension == 'json':
            payload = {'b2b': [{'ctin': GSTIN, 'inv': [{'inum': invoice, 'idt': '04-04-2025', 'val': '47983.41', 'itms': [
                {'itm_det': {'txval': taxable, 'camt': cgst, 'samt': sgst, 'iamt': '0', 'csamt': '0'}}
                for taxable, cgst, sgst in lines
            ]}]}]}
            stream = BytesIO(json.dumps(payload).encode())
        else:
            stream = BytesIO(('\n'.join(','.join(row) for row in [headers, *rows])).encode())
        stream.name = f'multiline.{extension}'
        return stream

    def test_all_formats_retain_lines_and_validate_aggregated_totals(self):
        for extension in ('csv', 'xlsx', 'json'):
            with self.subTest(extension=extension):
                batch = import_file(self.source(extension, invoice=extension), 'GSTR1', '')
                self.assertEqual(batch.invoices.count(), 2)
                self.assertEqual(batch.duplicate_rows, 0)
                vouchers = normalized_vouchers(batch, {'state': 'Tamil Nadu'})
                self.assertEqual(len(vouchers), 1)
                voucher = vouchers[0]
                self.assertEqual(len(voucher['items']), 2)
                self.assertEqual([item['taxable_value'] for item in voucher['items']], ['19.00', '40647.00'])
                self.assertEqual(voucher['taxable_total'], '40666.00')
                self.assertEqual(voucher['cgst'], '3658.71')
                self.assertEqual(voucher['sgst'], '3658.71')
                self.assertEqual(voucher['invoice_total'], '47983.41')
                result = resolve_round_off(voucher)
                self.assertEqual(result['component_total'], '47983.42')
                self.assertEqual(result['round_off'], '-0.01')
                self.assertEqual(result['status'], 'Ready')

    def test_five_identical_source_lines_remain_five_items(self):
        batch = import_file(self.source('csv', lines=[('19.00', '0.48', '0.48')] * 5), 'GSTR1', '')
        self.assertEqual(batch.invoices.count(), 5)
        voucher = normalized_vouchers(batch, {'state': 'Tamil Nadu'})[0]
        self.assertEqual(len(voucher['items']), 5)
        self.assertEqual(voucher['taxable_total'], '95.00')

    def test_prior_upload_duplicate_check_still_skips_all_lines(self):
        user = get_user_model().objects.create_user(username='multiline-preview')
        import_file(self.source('csv'), 'GSTR1', '', user=user)
        repeated = import_file(self.source('csv'), 'GSTR1', '', user=user)
        self.assertEqual(repeated.invoices.count(), 0)
        self.assertEqual(repeated.duplicate_rows, 2)

    def test_different_dates_and_parties_remain_separate(self):
        batch = import_file(self.source('csv'), 'GSTR1', '')
        row = batch.invoices.last()
        row.invoice_date = '2025-04-05'
        row.save()
        self.assertEqual(len(normalized_vouchers(batch, {'state': 'Tamil Nadu'})), 2)
        row.invoice_date = '2025-04-04'
        row.customer_gstin = '33AAACB2894G1ZJ'
        row.save()
        self.assertEqual(len(normalized_vouchers(batch, {'state': 'Tamil Nadu'})), 2)
