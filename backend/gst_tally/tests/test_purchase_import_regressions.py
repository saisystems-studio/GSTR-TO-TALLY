from datetime import date
from decimal import Decimal
from io import BytesIO

from django.test import TestCase

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.services.gstr2a_parser import parse as parse_gstr2a
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.service import import_outcome
from gst_tally.tally.validators import validate_voucher


GSTIN = "33AAACB2894G1ZJ"


class PurchaseImportRegressionTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2a.csv", file_type="CSV", gst_return_type="GSTR2A",
            company_gstin="33AFHPM6103Q1Z8",
            source_parties={GSTIN: {"party_name": "Source Supplier"}},
        )
        GSTParty.objects.create(gstin=GSTIN, trade_name="Fetched Supplier")

    def add_row(self, source_row_number, taxable, cgst, sgst, *, round_off="0.06", other_charges="25"):
        return GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="INV-1", invoice_date=date(2026, 8, 1),
            customer_gstin=GSTIN, taxable_value=Decimal(taxable), tax_percent=Decimal("18"),
            cgst=Decimal(cgst), sgst=Decimal(sgst), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("177026.06"), place_of_supply="33",
            round_off=Decimal(round_off), other_charges=Decimal(other_charges),
            source_line={"source_row_number": source_row_number, "Taxable Value": taxable},
        )

    def test_duplicate_persisted_source_row_is_aggregated_once(self):
        self.add_row(2, "100000", "9000", "9000")
        self.add_row(3, "50000", "4500", "4500")
        self.add_row(3, "50000", "4500", "4500")

        voucher = normalized_vouchers(self.batch, {"state": "Tamil Nadu"})[0]

        self.assertEqual(voucher["taxable_total"], "150000.00")
        self.assertEqual(voucher["cgst"], "13500.00")
        self.assertEqual(len(voucher["items"]), 2)

    def test_identical_legitimate_lines_with_distinct_source_rows_are_preserved(self):
        self.add_row(2, "75000", "6750", "6750")
        self.add_row(3, "75000", "6750", "6750")

        voucher = normalized_vouchers(self.batch, {"state": "Tamil Nadu"})[0]

        self.assertEqual(voucher["taxable_total"], "150000.00")
        self.assertEqual(len(voucher["items"]), 2)

    def test_invoice_level_roundoff_and_charges_are_applied_once(self):
        self.add_row(2, "100000", "9000", "9000")
        self.add_row(3, "50000", "4500", "4500")

        voucher = normalized_vouchers(self.batch, {"state": "Tamil Nadu"})[0]
        validation = validate_voucher(voucher)

        self.assertEqual(voucher["source_round_off"], "0.06")
        self.assertEqual(voucher["other_charges"], "25.00")
        # Component Total = Taxable + CGST + SGST + IGST + Cess + Other Charges (150000+13500+13500+25).
        # The source_round_off column above is informational only; Round Off is never
        # detected from it, only from the gap against the source Invoice Value.
        self.assertEqual(validation["calculated_total"], "177025.00")
        self.assertEqual(validation["difference"], "-1.06")

    def test_mismatch_diagnostics_include_every_requested_component(self):
        self.add_row(2, "100000", "9000", "9000")
        voucher = normalized_vouchers(self.batch, {"state": "Tamil Nadu"})[0]
        validation = validate_voucher(voucher)

        self.assertEqual(validation["diagnostics"], {
            "invoice_number": "INV-1", "gstin": GSTIN,
            "taxable_total": "100000.00", "cgst_total": "9000.00",
            "sgst_total": "9000.00", "igst_total": "0.00", "cess_total": "0.00",
            "other_charges_total": "25.00", "round_off": "0.00", "round_off_source": "None",
            "calculated_invoice_total": "118025.00",
            "source_invoice_value": "177026.06", "difference": "-59001.06",
        })


class SourceRowRegressionTests(TestCase):
    def test_whitespace_only_csv_row_is_ignored(self):
        source = BytesIO(
            b"GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Invoice Value\n"
            b"   ,  ,  ,  ,  \n"
            b"33AAACB2894G1ZJ,INV-1,01-08-2026,100,118\n"
        )

        rows, _ = parse_gstr2a(source)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["invoice_no"], "INV-1")


class ImportOutcomeTests(TestCase):
    def test_success_partial_and_failed_contracts(self):
        self.assertEqual(import_outcome({"total": 2, "imported": 2, "already_imported": 0, "failed": 0, "invalid": 0, "skipped": 0}),
                         {"total": 2, "imported": 2, "failed": 0, "skipped": 0, "status": "success"})
        # skipped and invalid are non-overlapping buckets (see service.py's Step 6 counts),
        # so both are now reported in full rather than one being netted against the other.
        self.assertEqual(import_outcome({"total": 3, "imported": 1, "already_imported": 1, "failed": 0, "invalid": 1, "skipped": 1}),
                         {"total": 3, "imported": 2, "failed": 1, "skipped": 1, "status": "partial_success"})
        self.assertEqual(import_outcome({"total": 2, "imported": 0, "already_imported": 0, "failed": 1, "invalid": 1, "skipped": 1}),
                         {"total": 2, "imported": 0, "failed": 2, "skipped": 1, "status": "failed"})

    def test_validation_failed_and_tally_failed_are_both_counted_as_failed(self):
        # service.py sets counts["failed"] = counts["tally_failed"] for backward
        # compatibility (see import_batch); import_outcome sums "failed" + "validation_failed".
        self.assertEqual(import_outcome({"total": 4, "imported": 1, "already_imported": 0,
                                         "validation_failed": 2, "tally_failed": 1, "failed": 1, "skipped": 0}),
                         {"total": 4, "imported": 1, "failed": 3, "skipped": 0, "status": "partial_success"})
