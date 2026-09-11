from datetime import date
from unittest import TestCase
from xml.etree import ElementTree as ET

from gst_tally.tally.batch_import import batch_response_is_complete, iter_voucher_batches
from gst_tally.tally.response_parser import TallyResponse


class BatchImportTests(TestCase):
    def voucher(self, number):
        return {
            "invoice_date": "2025-04-04", "invoice_number": str(number), "voucher_type": "Sales",
            "invoice_total": "1180.00", "taxable_total": "1000.00", "cgst": "90.00", "sgst": "90.00",
            "igst": "0.00", "cess": "0.00", "rounding_adjustment": "0.00",
            "party": {"name": "ACME", "gstin": "33ABCDE1234F1Z5", "state": "Tamil Nadu", "country": "India"},
            "items": [{"sales_ledger": "Sales 18%", "taxable_value": "1000.00"}],
            "rate_allocations": [], "tax_allocations": [],
        }

    def test_batches_keep_all_messages_in_one_envelope(self):
        batches = list(iter_voucher_batches([self.voucher(i) for i in range(3)], "Company", batch_size=10))
        self.assertEqual(len(batches), 1)
        root = ET.fromstring(batches[0][1])
        self.assertEqual(len(root.findall(".//REQUESTDATA/TALLYMESSAGE")), 3)

    def test_batch_size_is_respected(self):
        batches = list(iter_voucher_batches([self.voucher(i) for i in range(5)], "Company", batch_size=2))
        self.assertEqual([len(items) for items, _ in batches], [2, 2, 1])

    def test_only_exact_clean_acknowledgement_is_complete(self):
        self.assertTrue(batch_response_is_complete(TallyResponse(created=3), 3))
        self.assertFalse(batch_response_is_complete(TallyResponse(created=2, errors=1), 3))
