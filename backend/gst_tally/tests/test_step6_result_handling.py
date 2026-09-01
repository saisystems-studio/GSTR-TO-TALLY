"""Step 6 result-handling regression tests.

Three genuinely different outcomes must never collapse into one generic
"failure": an actual Tally rejection (Failed), a voucher already confirmed to
exist in the currently open Tally company (Already Imported), and a source
data mismatch that was never sent to Tally at all (Validation Failed).
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from xml.etree import ElementTree as ET

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty, TallyVoucherMapping
from gst_tally.tally.response_parser import TallyResponse, parse_import_response
from gst_tally.tally.service import (_step6_outcome, _tally_error_detail, _write_failure_reason,
                                     correct_invoice_value, import_batch, save_voucher_correction,
                                     prepare_master_results, voucher_preview)


GSTIN = "33AAACB2894G1ZJ"
COMPANY = "SRI MAHALAKSHMI TRADERS,"
COMPANY_GSTIN = "33AFHPM6103Q1Z8"

VOUCHER = {"invoice_number": "7", "voucher_type": "Purchase",
           "party": {"name": "YUVRAJ FIREWORKS PRIVATE LIMITED"},
           "rate_allocations": [{"gst_rate": "5", "account_ledger": "GST Purchase 5%"},
                                {"gst_rate": "18", "account_ledger": "GST Purchase 18%"}],
           "tax_allocations": [{"ledger": "Input CGST 2.5%"}, {"ledger": "Input SGST 2.5%"},
                               {"ledger": "Input CGST 9%"}, {"ledger": "Input SGST 9%"}],
           "rounding_adjustment": "-0.01", "other_charges": "0"}

# Captured live from TallyPrime for the actual Invoice 7 rejection: a well-formed
# acknowledgement envelope with EXCEPTIONS=1 and no LINEERROR/EXCEPTIONDESC/description
# at all -- Tally gave nothing more specific for this particular request.
MINIMAL_EXCEPTION_RESPONSE = TallyResponse(
    created=0, altered=0, deleted=0, errors=0, exceptions=1, cancelled=0,
    error="Tally reported 1 exception(s); full response captured for diagnosis",
    raw="<RESPONSE><CREATED>0</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED><LASTVCHID>0</LASTVCHID>"
        "<LASTMID>0</LASTMID><COMBINED>0</COMBINED><IGNORED>0</IGNORED><ERRORS>0</ERRORS>"
        "<CANCELLED>0</CANCELLED><EXCEPTIONS>1</EXCEPTIONS></RESPONSE>")


class WriteFailureReasonTests(SimpleTestCase):
    """Section 1: expose the exact Tally reason when Tally gave one; never show
    only the generic exception count when something more specific is available."""

    def test_lineerror_is_surfaced_verbatim_not_the_generic_exception_count(self):
        response = TallyResponse(created=0, errors=0, exceptions=1,
                                 line_error="Ledger 'Input CGST 9%' does not exist.",
                                 error="Ledger 'Input CGST 9%' does not exist.")
        reason = _write_failure_reason(response, VOUCHER)
        self.assertEqual(reason, "Tally Import Failed: Ledger 'Input CGST 9%' does not exist.")

    def test_exception_description_is_preferred_over_generic_count(self):
        response = TallyResponse(created=0, errors=0, exceptions=1,
                                 exception_text="Voucher totals do not balance.",
                                 error="Voucher totals do not balance.")
        reason = _write_failure_reason(response, VOUCHER)
        self.assertEqual(reason, "Tally Import Failed: Voucher totals do not balance.")

    def test_minimal_response_with_no_text_still_gets_an_actionable_reason(self):
        reason = _write_failure_reason(MINIMAL_EXCEPTION_RESPONSE, VOUCHER)

        self.assertNotEqual(reason, "Tally reported 1 exception(s); full response captured for diagnosis")
        self.assertNotIn("Tally reported 1 exception(s); full response captured for diagnosis", reason)
        self.assertIn("Tally Import Failed", reason)
        self.assertIn("CREATED=0", reason); self.assertIn("EXCEPTIONS=1", reason)
        self.assertIn("EXCEPTIONS=1 without LINEERROR", reason)
        self.assertIn("no ledger cause was inferred", reason)

    def test_tally_error_detail_captures_every_required_counter(self):
        response = TallyResponse(created=0, altered=0, deleted=0, errors=0, exceptions=1, cancelled=0,
                                 last_vch_id="0", last_mid="0", combined=0, ignored=0, raw="<RESPONSE/>")
        detail = _tally_error_detail(response, VOUCHER)

        for field in ("created", "altered", "deleted", "last_vch_id", "last_mid", "combined",
                     "ignored", "errors", "cancelled", "exceptions", "line_error", "exception_text"):
            self.assertIn(field, detail)
        self.assertEqual(detail["requested_voucher_number"], "7")
        self.assertEqual(detail["source_invoice_number"], "7")
        self.assertEqual(detail["voucher_type"], "Purchase")
        self.assertEqual(detail["party_ledger"], "YUVRAJ FIREWORKS PRIVATE LIMITED")
        self.assertIn("GST Purchase 5%", detail["accounting_ledgers"])
        self.assertIn("Input CGST 9%", detail["accounting_ledgers"])
        self.assertIn("Round Off", detail["accounting_ledgers"])
        self.assertIn("raw_response", detail)


class Step6OutcomeTests(SimpleTestCase):
    """Section: Step 6 final summary distinguishes all six statuses and never
    shows Import Successful with a Tally failure, nor generic Import Failed
    when some vouchers succeeded."""

    def test_partial_import_message_matches_the_specified_wording(self):
        counts = {"imported": 25, "already_imported": 1, "validation_failed": 2, "tally_failed": 1,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        import_status, message = _step6_outcome(counts)

        self.assertEqual(import_status, "Partial Import")
        self.assertEqual(message, "25 vouchers imported successfully. 1 was already present in Tally. "
                                  "2 require data correction and 1 was rejected by Tally.")

    def test_all_imported_is_import_successful(self):
        counts = {"imported": 3, "already_imported": 0, "validation_failed": 0, "tally_failed": 0,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        self.assertEqual(_step6_outcome(counts)[0], "Import Successful")

    def test_any_tally_failure_blocks_import_successful(self):
        counts = {"imported": 5, "already_imported": 0, "validation_failed": 0, "tally_failed": 1,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        import_status, _ = _step6_outcome(counts)
        self.assertNotEqual(import_status, "Import Successful")

    def test_partial_success_is_not_reported_as_generic_import_failed(self):
        counts = {"imported": 1, "already_imported": 0, "validation_failed": 1, "tally_failed": 0,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        import_status, _ = _step6_outcome(counts)
        self.assertEqual(import_status, "Partial Import")
        self.assertNotEqual(import_status, "Import Failed")

    def test_nothing_succeeded_is_import_failed(self):
        counts = {"imported": 0, "already_imported": 0, "validation_failed": 2, "tally_failed": 1,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        self.assertEqual(_step6_outcome(counts)[0], "Import Failed")

    def test_all_eligible_already_verified_is_import_verified_not_import_successful(self):
        """Spec section U: zero new imports plus every eligible voucher verified
        present is a distinct outcome from a fresh Import Successful."""
        counts = {"imported": 0, "already_imported": 28, "validation_failed": 0, "tally_failed": 0,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        import_status, message = _step6_outcome(counts)
        self.assertEqual(import_status, "Import Verified")
        self.assertEqual(message, "28 vouchers are already present in Tally. No duplicate vouchers were created.")

    def test_mixed_new_and_already_imported_stays_import_successful(self):
        counts = {"imported": 5, "already_imported": 2, "validation_failed": 0, "tally_failed": 0,
                  "unknown": 0, "skipped": 0, "not_attempted": 0, "waiting_for_tally_period": 0, "invalid": 0}
        self.assertEqual(_step6_outcome(counts)[0], "Import Successful")


def collection_with(vouchers):
    def entries(row):
        return "".join(
            f'<LEDGERENTRIES.LIST><LEDGERNAME>{entry["ledger"]}</LEDGERNAME><AMOUNT>{entry["amount"]}</AMOUNT>'
            f'<RATEOFINVOICETAX.LIST><RATEOFINVOICETAX>{entry.get("gst_rate", "")}</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>'
            f'</LEDGERENTRIES.LIST>' for entry in row.get("taxable_allocations", []))
    rows = "".join(
        f'<VOUCHER MASTERID="{row.get("master_id", "")}" ALTERID="{row.get("alter_id", "")}">'
        f'<DATE>{row.get("date", "")}</DATE><VOUCHERNUMBER>{row["number"]}</VOUCHERNUMBER>'
        f'<VOUCHERTYPENAME>{row["type"]}</VOUCHERTYPENAME><REFERENCE>{row.get("reference", "")}</REFERENCE>'
        f'<PARTYLEDGERNAME>{row.get("party", "")}</PARTYLEDGERNAME><GUID>{row.get("guid", "")}</GUID>{entries(row)}</VOUCHER>'
        for row in vouchers)
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DESC>'
            f'<STATICVARIABLES><SVCURRENTCOMPANY>{COMPANY}</SVCURRENTCOMPANY></STATICVARIABLES></DESC>'
            f'<DATA><COLLECTION>{rows}</COLLECTION></DATA></BODY></ENVELOPE>').encode()


EMPTY_COLLECTION = (b'<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DESC>'
                    b'</DESC><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>')
LIVE_EMPTY_DAY_BOOK = (b'<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>'
                       b'<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>'
                       b'<REQUESTDATA></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>')


class ScriptedClient:
    """A Tally double whose voucher write response is scripted per invoice number,
    and whose preflight/query-back reads are driven from a live-vouchers list that
    the test controls directly -- never from anything written locally."""

    def __init__(self, live_vouchers=None, write_responses=None):
        self.payloads = []
        self.live_vouchers = list(live_vouchers or [])
        self.write_responses = write_responses or {}
        self.written = []
        self.last_http_status = 200

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            if b"<REPORTNAME>Vouchers</REPORTNAME>" not in payload:
                return b'<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED></IMPORTRESULT></DATA></BODY></ENVELOPE>'
            number = ET.fromstring(payload).findtext(".//VOUCHERNUMBER") or ""
            raw = self.write_responses.get(number)
            if raw is None:
                root = ET.fromstring(payload)
                entries = [{"ledger": entry.findtext("LEDGERNAME") or "", "amount": entry.findtext("AMOUNT") or "",
                            "gst_rate": entry.findtext("RATEOFINVOICETAX.LIST/RATEOFINVOICETAX") or ""}
                           for entry in root.findall(".//LEDGERENTRIES.LIST")]
                self.written.append({"number": number, "type": ET.fromstring(payload).find(".//VOUCHER").get("VCHTYPE"),
                                     "reference": ET.fromstring(payload).findtext(".//REFERENCE") or "",
                                     "party": ET.fromstring(payload).findtext(".//PARTYLEDGERNAME") or "",
                                     "master_id": f"m-{number}", "alter_id": "1", "guid": f"g-{number}",
                                     "taxable_allocations": entries})
                return (b'<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED>'
                        b'<ERRORS>0</ERRORS><LASTVCHID>' + f"m-{number}".encode() + b'</LASTVCHID></IMPORTRESULT></DATA></BODY></ENVELOPE>')
            return raw
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.live_vouchers + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION

    def import_data(self, payload):
        return parse_import_response(self.post(payload))

    def import_json(self, payload, object_id):
        # Master writes now go over native JSON; these tests are exercising
        # voucher flows and don't inspect the master payload itself, so a
        # scripted always-succeeds response mirrors the old XML path's
        # unconditional "CREATED=1" fallback for non-voucher writes.
        return TallyResponse(created=1)


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class Step6IntegrationTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="PUR-1", invoice_date=date(2025, 4, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180"), place_of_supply="33", source_line={"source_row_number": 2})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": COMPANY, "company": COMPANY,
                       "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}

    def run_import(self, client):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            return import_batch(self.batch, client)

    def test_minimal_rejection_response_gets_an_actionable_reason_not_generic_text(self):
        client = ScriptedClient(write_responses={"PUR-1": MINIMAL_EXCEPTION_RESPONSE.raw.encode()})

        result = self.run_import(client)
        row = next(r for r in result["results"] if r["invoice_no"] == "PUR-1")

        self.assertEqual(row["status"], "Tally Failed")
        self.assertNotEqual(row["reason"], "Tally reported 1 exception(s); full response captured for diagnosis")
        self.assertIn("Tally Import Failed", row["reason"])
        self.assertIsNotNone(row.get("tally_error"))
        self.assertEqual(row["tally_error"]["exceptions"], 1)
        self.assertEqual(result["summary"]["tally_failed"], 1)
        self.assertEqual(result["summary"]["validation_failed"], 0)
        self.assertEqual(result["import_status"], "Import Failed")

    def test_specific_lineerror_is_shown_verbatim(self):
        rejection = (b'<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>0</CREATED><ERRORS>0</ERRORS>'
                    b'<EXCEPTIONS>1</EXCEPTIONS><LINEERROR>Ledger &apos;GST Purchase 18%&apos; does not exist.'
                    b'</LINEERROR></IMPORTRESULT></DATA></BODY></ENVELOPE>')
        client = ScriptedClient(write_responses={"PUR-1": rejection})

        result = self.run_import(client)
        row = next(r for r in result["results"] if r["invoice_no"] == "PUR-1")

        self.assertEqual(row["reason"], "Tally Import Failed: Ledger 'GST Purchase 18%' does not exist.")

    def test_already_imported_requires_a_live_match_a_stale_local_mapping_alone_is_not_enough(self):
        key_source = "|".join([COMPANY_GSTIN.casefold(), "sales", "pur-1", "2025-04-01", GSTIN.casefold()])
        import hashlib
        stale_key = hashlib.sha256(key_source.encode()).hexdigest()
        TallyVoucherMapping.objects.create(idempotency_key=stale_key, batch=self.batch, invoice_id=1,
                                           source_invoice_number="PUR-1", party_gstin=GSTIN,
                                           tally_company=COMPANY, tally_voucher_identifier="999",
                                           import_status="Imported")
        # The live company has NO matching voucher -- the preflight query returns nothing.
        client = ScriptedClient(live_vouchers=[])

        result = self.run_import(client)
        row = next(r for r in result["results"] if r["invoice_no"] == "PUR-1")

        self.assertNotEqual(row["status"], "Already Imported")
        self.assertEqual(row["status"], "Imported")
        self.assertTrue(any(b"<REPORTNAME>Vouchers</REPORTNAME>" in p for p in client.payloads if b"Import Data" in p))

    def test_voucher_confirmed_live_is_already_imported_and_not_resent(self):
        client = ScriptedClient(live_vouchers=[{"number": "5", "type": "Purchase", "reference": "PUR-1",
                                                "party": "Source Supplier", "master_id": "77",
                                                "taxable_allocations": [{"ledger": "GST Purchase 18%", "amount": "-1000", "gst_rate": "18"},
                                                                         {"ledger": "Input CGST 9%", "amount": "-90", "gst_rate": "9"},
                                                                         {"ledger": "Input SGST 9%", "amount": "-90", "gst_rate": "9"}]}])

        result = self.run_import(client)
        row = next(r for r in result["results"] if r["invoice_no"] == "PUR-1")

        self.assertEqual(row["status"], "Already Imported")
        self.assertEqual(result["summary"]["already_imported"], 1)
        self.assertFalse(any(b"<REPORTNAME>Vouchers</REPORTNAME>" in p for p in client.payloads if b"Import Data" in p))

    def test_existing_voucher_must_pass_source_verification_before_already_imported(self):
        client = ScriptedClient(live_vouchers=[{"number": "5", "type": "Purchase", "reference": "PUR-1",
                                                "party": "Source Supplier", "master_id": "411"}])
        mismatch = {"found": False, "query_valid": True, "identifier": "411",
                    "reason": "Voucher exists, but taxable allocations differ",
                    "verification_difference": {"expected_gst_rates": ["18"], "actual_gst_rates": []}}
        with patch("gst_tally.tally.service.verify_voucher", return_value=mismatch):
            result = self.run_import(client)
        row = next(r for r in result["results"] if r["invoice_no"] == "PUR-1")
        self.assertEqual(row["status"], "Verification Failed")
        self.assertEqual(row["voucher_identifier"], "411")
        self.assertEqual(result["summary"]["already_imported"], 0)
        self.assertEqual(result["summary"]["verification_failed"], 1)
        self.assertFalse(any(b"<REPORTNAME>Vouchers</REPORTNAME>" in p for p in client.payloads if b"Import Data" in p))

    def test_purchase_probe_failure_does_not_stop_later_eligible_invoices(self):
        GSTInvoice.objects.all().delete()
        for number in ("1", "2", "3"):
            GSTInvoice.objects.create(
                import_batch=self.batch, invoice_no=number, invoice_date=date(2025, 4, 1),
                customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
                cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("1180"), place_of_supply="33",
                source_line={"source_row_number": int(number) + 1})
        client = ScriptedClient()
        checks = [
            {"found": False, "query_valid": True, "identifier": "411", "reason": "rate mismatch",
             "expected_purchase_ledgers": ["GST Purchase 18%"], "actual_purchase_ledgers": [],
             "expected_tax_ledgers": ["Input CGST 9%", "Input SGST 9%"], "actual_tax_ledgers": [],
             "expected_gst_rates": ["18"], "actual_gst_rates": [],
             "verification_difference": {"missing_purchase_allocations": [{"ledger": "GST Purchase 18%", "gst_rate": "18"}]}},
            {"found": True, "query_valid": True, "identifier": "412", "voucher_number": "2", "reason": "confirmed"},
            {"found": True, "query_valid": True, "identifier": "413", "voucher_number": "3", "reason": "confirmed"},
        ]
        with patch("gst_tally.tally.service.verify_voucher", side_effect=checks):
            result = self.run_import(client)
        rows = {row["invoice_no"]: row for row in result["results"]}
        self.assertEqual(rows["1"]["status"], "Verification Failed")
        self.assertEqual(rows["1"]["expected_gst_rates"], ["18"])
        self.assertEqual(rows["1"]["actual_gst_rates"], [])
        self.assertIn("missing_purchase_allocations", rows["1"]["verification_diagnostics"])
        self.assertEqual(rows["2"]["status"], "Imported")
        self.assertEqual(rows["3"]["status"], "Imported")
        self.assertEqual(result["summary"]["not_attempted"], 0)
        self.assertEqual(len(client.written), 3)


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class LedgerNameReuseIntegrationTests(TestCase):
    """Spec V/W: an existing Tally ledger spelled without spaces/@ is the same
    master and must be reused (never duplicated), and the voucher sent to Tally
    must reference the ledger's *actual* existing spelling."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="PUR-1", invoice_date=date(2025, 4, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180"), place_of_supply="33", source_line={"source_row_number": 2})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": COMPANY, "company": COMPANY,
                       "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}
        # Existing Tally ledgers spelled without spaces/@ -- normalized-equivalent
        # to the canonical "GST Purchase 18%"/"Input CGST 9%"/"Input SGST 9%".
        self.variant_names = {"gstpurchase18%": "GSTPurchase18%",
                              "inputcgst9%": "InputCGST@9%", "inputsgst9%": "InputSGST@9%"}

    def run_import(self, client):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=(self.variant_names, {})):
            return import_batch(self.batch, client)

    def test_variant_spelled_ledger_is_reused_not_recreated(self):
        client = ScriptedClient()

        result = self.run_import(client)

        created_names = {ET.fromstring(p).find(".//LEDGER").get("NAME")
                         for p in client.payloads if b"<REPORTNAME>All Masters</REPORTNAME>" in p and b"<LEDGER " in p}
        self.assertFalse(created_names & {"GST Purchase 18%", "Input CGST 9%", "Input SGST 9%"})
        statuses = {m["name"]: m["status"] for m in result["masters"]}
        self.assertEqual(statuses["GSTPurchase18%"], "Existing")
        self.assertEqual(statuses["InputCGST@9%"], "Existing")
        self.assertEqual(statuses["InputSGST@9%"], "Existing")

    def test_voucher_sent_to_tally_references_the_actual_existing_ledger_spelling(self):
        client = ScriptedClient()

        self.run_import(client)

        voucher_payload = next(p for p in client.payloads if b"<REPORTNAME>Vouchers</REPORTNAME>" in p)
        names = {node.findtext("LEDGERNAME") for node in ET.fromstring(voucher_payload).findall(".//LEDGERENTRIES.LIST")}
        self.assertIn("GSTPurchase18%", names)
        self.assertIn("InputCGST@9%", names)
        self.assertIn("InputSGST@9%", names)
        self.assertNotIn("GST Purchase 18%", names)
        self.assertNotIn("Input CGST 9%", names)
        self.assertNotIn("Input SGST 9%", names)


@override_settings(TALLY_DRY_RUN=False, GST_LOOKUP_PROVIDER="")
class ReviewFixCorrectionTests(TestCase):
    """Section 3: Review/Fix corrects the declared Invoice Value only, and only
    on request -- it never auto-converts a large mismatch into Round Off, and it
    revalidates so the row is eligible for Step 6 only once it is truly Ready."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu"},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        # Shaped exactly like Invoice 55: two rate rows, internally consistent tax
        # math, but a declared Invoice Value that is off by a large, round amount.
        for row_number, taxable, cgst, rate in ((34, "101017", "9091.53", "18"), (35, "56250", "7875.00", "28")):
            GSTInvoice.objects.create(
                import_batch=self.batch, invoice_no="55", invoice_date=date(2025, 4, 25),
                customer_gstin=GSTIN, taxable_value=Decimal(taxable), tax_percent=Decimal(rate),
                cgst=Decimal(cgst), sgst=Decimal(cgst), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("119200.06"), place_of_supply="33",
                source_line={"source_row_number": row_number})
        self.company_status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                               "company_open": True, "company_name": COMPANY, "company": COMPANY,
                               "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                               "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                               "can_import": True, "message": "ok"}

    def _preview_row(self):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            from gst_tally.tally.service import voucher_preview
            return next(v for v in voucher_preview(self.batch)["vouchers"] if v["invoice_number"] == "55")

    def test_large_mismatch_is_validation_failed_before_any_correction(self):
        row = self._preview_row()
        self.assertEqual(row["status"], "Review Required")
        self.assertEqual(row["round_off"], "0.00")
        self.assertEqual(row["difference"], "-72000.00")
        self.assertIn("Invoice total mismatch. Difference ₹72000.00 is too large to be treated as Round Off.",
                      row["reason"])

    def test_apply_suggested_value_never_touches_taxable_or_gst_fields(self):
        before = list(GSTInvoice.objects.filter(import_batch=self.batch).values(
            "taxable_value", "cgst", "sgst", "igst", "cess"))

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            with self.assertRaisesRegex(ValueError, "invoice mismatch"):
                correct_invoice_value(self.batch, GSTIN, "55", date(2025, 4, 25), use_suggested=True)

        after = list(GSTInvoice.objects.filter(import_batch=self.batch).values(
            "taxable_value", "cgst", "sgst", "igst", "cess"))
        self.assertEqual(before, after)
        self.assertTrue(all(row.invoice_value == Decimal("119200.06")
                            for row in GSTInvoice.objects.filter(import_batch=self.batch)))

    def test_apply_suggested_value_revalidates_to_ready(self):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            with self.assertRaises(ValueError):
                correct_invoice_value(self.batch, GSTIN, "55", date(2025, 4, 25), use_suggested=True)

    def test_manual_edit_to_a_value_that_still_mismatches_stays_validation_failed(self):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            result = correct_invoice_value(self.batch, GSTIN, "55", date(2025, 4, 25), invoice_value=Decimal("1.00"))

        self.assertEqual(result["corrected_voucher"]["status"], "Review Required")

    def test_manual_edit_to_the_correct_value_becomes_ready(self):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            with self.assertRaisesRegex(ValueError, "between -1.00 and 1.00"):
                correct_invoice_value(self.batch, GSTIN, "55", date(2025, 4, 25), invoice_value=Decimal("-72000.00"))

    def test_step4_company_verification_is_unaffected_by_an_invoice_total_mismatch(self):
        # Invoice 55 (set up in setUp) is a genuine invoice-total mismatch -- Step 4
        # company verification must still reach MATCHED, and the mismatch must
        # still surface, untouched, for review at the voucher-preview stage.
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            result = prepare_master_results(self.batch)
            from gst_tally.tally.service import voucher_preview
            preview = voucher_preview(self.batch)

        self.assertTrue(result["company_verified"])
        self.assertEqual(result["verification_code"], "MATCHED")
        self.assertEqual(result["verification"], "MATCHED")
        self.assertTrue(result["ready_for_master_check"])
        # Display value passes through exactly as returned by the connection --
        # normalization is for comparison only, never for what's shown.
        self.assertEqual(result["company"]["company_name"], COMPANY)
        self.assertEqual(result["company"]["gstin"], COMPANY_GSTIN)

        row = next(v for v in preview["vouchers"] if v["invoice_number"] == "55")
        self.assertEqual(row["status"], "Review Required")
        self.assertEqual(row["party_gstin"], GSTIN)
        self.assertEqual(row["calculated_invoice_total"], row["component_total"])
        self.assertEqual(row["source_invoice_value"], "119200.06")
        self.assertEqual(row["difference"], "-72000.00")
        for field in ("taxable_total", "cgst", "sgst", "igst", "cess", "other_charges", "round_off"):
            self.assertIn(field, row)

    def test_voucher_preview_exposes_party_name_separately_from_party_gstin(self):
        gstin = "33AALFE2101R1ZF"
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={gstin: {"party_name": "SOURCE EM VEERU"}},
        )
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="77", invoice_date=date(2025, 4, 25),
            customer_gstin=gstin, taxable_value=Decimal("1000.00"), tax_percent=Decimal("18"),
            cgst=Decimal("90.00"), sgst=Decimal("90.00"), igst=Decimal("0.00"), cess=Decimal("0.00"),
            invoice_value=Decimal("1180.00"), place_of_supply="33",
            source_line={"source_row_number": 7},
        )
        GSTParty.objects.create(
            gstin=gstin, trade_name="EM VEERU & CO", legal_name="EM VEERU AND COMPANY",
            principal_place_of_business="No.27, Tamil Nadu, 626104",
            state_name="Tamil Nadu", pincode="626104", taxpayer_type="Regular",
            lookup_source="sandbox", lookup_status="Fetched", party_data_status="Complete",
        )

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            preview = voucher_preview(batch)

        row = next(v for v in preview["vouchers"] if v["invoice_number"] == "77")
        self.assertEqual(row["party_name"], "EM VEERU & CO")
        self.assertEqual(row["party_gstin"], gstin)
        self.assertEqual(row["party_legal_name"], "EM VEERU AND COMPANY")
        self.assertEqual(row["party_address"], "No.27, Tamil Nadu, 626104")
        self.assertEqual(row["party_state"], "Tamil Nadu")
        self.assertEqual(row["party_pincode"], "626104")
        self.assertEqual(row["party"]["name"], "EM VEERU & CO")
        self.assertEqual(row["party"]["gstin"], gstin)
        self.assertEqual(row["party"]["name_source"], "SANDBOX_TRADE_NAME")

    def test_step4_company_payload_includes_active_tally_financial_period(self):
        self.company_status.update(
            financial_year_from="2025-04-01",
            financial_year_to="2026-03-31",
            financial_year="01 Apr 2025 - 31 Mar 2026",
            financial_year_available=True,
            financial_year_error="",
            company_read={
                "requested_company": COMPANY,
                "detected_company": COMPANY,
                "company_open": True,
                "state": "Tamil Nadu",
                "gstin": COMPANY_GSTIN,
                "financial_year_from": "2025-04-01",
                "financial_year_to": "2026-03-31",
                "financial_year": "01 Apr 2025 - 31 Mar 2026",
                "financial_year_available": True,
                "financial_year_error": "",
            },
        )

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            result = prepare_master_results(self.batch)

        self.assertEqual(result["company"]["financial_year_from"], "2025-04-01")
        self.assertEqual(result["company"]["financial_year_to"], "2026-03-31")
        self.assertEqual(result["company"]["financial_year"], "01 Apr 2025 - 31 Mar 2026")
        self.assertTrue(result["company"]["financial_year_available"])
        self.assertEqual(result["company"]["financial_year_error"], "")
        self.assertEqual(result["financial_year_from"], "2025-04-01")
        self.assertEqual(result["financial_year_to"], "2026-03-31")
        self.assertEqual(result["financial_year"], "01 Apr 2025 - 31 Mar 2026")
        self.assertEqual(result["company_read"]["financial_year"], "01 Apr 2025 - 31 Mar 2026")

    def test_invoice_value_correction_preserves_source_and_records_audit(self):
        source_values = list(GSTInvoice.objects.filter(import_batch=self.batch).values_list("invoice_value", flat=True))
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.company_status):
            result = save_voucher_correction(
                self.batch, GSTIN, "55", date(2025, 4, 25), "invoice_value", Decimal("191200.06"), "suggested")
        self.assertEqual(result["corrected_voucher"]["status"], "Ready")
        self.assertEqual(result["corrected_voucher"]["difference"], "0.00")
        self.assertEqual(result["correction_audit"]["source_invoice_value"], "119200.06")
        self.assertEqual(result["correction_audit"]["corrected_invoice_value"], "191200.06")
        self.assertEqual(result["correction_audit"]["correction_source"], "suggested")
        self.assertEqual(source_values, list(GSTInvoice.objects.filter(import_batch=self.batch).values_list("invoice_value", flat=True)))

    def test_unknown_invoice_raises(self):
        with self.assertRaises(ValueError):
            correct_invoice_value(self.batch, GSTIN, "does-not-exist", date(2025, 4, 25), invoice_value=Decimal("1"))


@override_settings(TALLY_DRY_RUN=False, GST_LOOKUP_PROVIDER="")
class Step4MasterSummaryTests(TestCase):
    """Step 4's master summary must count masters that already exist in Tally
    as ready/usable, and live Step 4 must create/repair missing or invalid
    masters before voucher preview."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="PUR-1", invoice_date=date(2025, 4, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180"), place_of_supply="33",
            source_line={"source_row_number": 2})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": COMPANY, "company": COMPANY,
                       "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}

    def write_master_ok(self, _client, master, _company):
        return TallyResponse(created=1, raw=f"<RESPONSE><CREATED>1</CREATED><MASTER>{master['name']}</MASTER></RESPONSE>")

    def reread_master_ok(self, name, _company="", _client=None):
        if name == "Source Supplier":
            return {"exists": True, "name": name, "parent": "Sundry Creditors", "gstin": GSTIN,
                    "state": "Tamil Nadu", "country": "India", "registration_type": "Regular",
                    "place_of_supply": "Tamil Nadu", "pincode": ""}
        if name == "GST Purchase 18%":
            return {"exists": True, "name": name, "parent": "Purchase Accounts",
                    "gst_applicable": "Applicable", "taxability": "Taxable", "supply_type": "Goods",
                    "gst_rate": "18", "outer_gst_rate": "18", "gst_rate_details_popup_exists": True,
                    "set_alter_gst_rate_details": "Yes", "gst_rates": {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"}}
        if name.startswith("Input CGST"):
            return {"exists": True, "name": name, "parent": "Duties & Taxes",
                    "duty_type": "GST", "tax_type": "CGST", "gst_rate": "9",
                    "rounding_method": "Not Applicable"}
        if name.startswith("Input SGST"):
            return {"exists": True, "name": name, "parent": "Duties & Taxes",
                    "duty_type": "GST", "tax_type": "SGST/UTGST", "gst_rate": "9",
                    "rounding_method": "Not Applicable"}
        return {"exists": True, "name": name, "parent": "Indirect Expenses"}

    def test_masters_that_already_exist_in_tally_count_as_existing_not_ready_to_create(self):
        # First read what Step 4 considers required, with nothing reported as
        # existing yet -- every row should still show up, just not-yet-existing.
        with self.settings(TALLY_DRY_RUN=True), \
             patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            none_existing = prepare_master_results(self.batch)

        required = none_existing["masters"]
        self.assertTrue(required, "this fixture must require at least one master")
        self.assertTrue(all(row["status"] == "Ready to Create" for row in required))

        # Now report every required master as already present in Tally (by its
        # exact name) -- Step 4 must recognize each one as Existing/usable.
        existing_names = {row["name"].casefold(): row["name"] for row in required}
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=(existing_names, {}, {})), \
             patch("gst_tally.tally.service.ledger_details", side_effect=self.reread_master_ok):
            all_existing = prepare_master_results(self.batch, client=object())

        rows = all_existing["masters"]
        self.assertEqual(len(rows), len(required))
        self.assertTrue(all(row["status"] == "Existing" for row in rows),
                        [row["status"] for row in rows])
        self.assertTrue(all(row["message"] == "Matching Tally master already exists" for row in rows))

        # Explicit per-category summary: existing masters count toward ready,
        # not toward failed/created, and required must equal the actual row count.
        summary = all_existing["master_summary"]
        self.assertEqual(sum(category["required"] for category in summary.values()), len(rows))
        self.assertEqual(sum(category["existing"] for category in summary.values()), len(rows))
        self.assertEqual(sum(category["ready"] for category in summary.values()), len(rows))
        self.assertEqual(sum(category["created"] for category in summary.values()), 0)
        self.assertEqual(sum(category["failed"] for category in summary.values()), 0)
        for category in summary.values():
            self.assertEqual(category["ready"], category["required"])

    def test_a_partially_existing_batch_shows_a_mixed_ready_and_pending_count(self):
        with self.settings(TALLY_DRY_RUN=True), \
             patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            required = prepare_master_results(self.batch)["masters"]
        self.assertGreater(len(required), 1, "this fixture must require more than one master to test a mix")

        first_only = {required[0]["name"].casefold(): required[0]["name"]}
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=(first_only, {}, {})), \
             patch("gst_tally.tally.service._write_master", side_effect=self.write_master_ok), \
             patch("gst_tally.tally.service.ledger_details", side_effect=self.reread_master_ok):
            result = prepare_master_results(self.batch, client=object())

        statuses = [row["status"] for row in result["masters"]]
        self.assertEqual(statuses.count("Existing"), 1)
        self.assertEqual(statuses.count("Created"), len(required) - 1)

    def test_unreadable_master_lookup_falls_back_to_ready_to_create_without_crashing(self):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", side_effect=RuntimeError("ODBC down")), \
             patch("gst_tally.tally.service._write_master", side_effect=self.write_master_ok), \
             patch("gst_tally.tally.service.ledger_details", side_effect=self.reread_master_ok):
            result = prepare_master_results(self.batch, client=object())

        self.assertTrue(result["masters"])
        self.assertTrue(all(row["status"] == "Created" for row in result["masters"]))
        self.assertTrue(result["ready"])

    def test_step4_creates_missing_masters_and_counts_verified_before_preview(self):
        written = []

        def write_master(_client, master, _company):
            written.append(master["name"])
            return self.write_master_ok(_client, master, _company)

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {}, {})), \
             patch("gst_tally.tally.service._write_master", side_effect=write_master), \
             patch("gst_tally.tally.service.ledger_details", side_effect=self.reread_master_ok):
            result = prepare_master_results(self.batch, client=object())

        rows = result["masters"]
        self.assertTrue(rows)
        self.assertTrue(all(row["status"] == "Created" for row in rows), [row["status"] for row in rows])
        self.assertTrue(all(row["verified"] for row in rows))
        self.assertEqual(set(written), {row["name"] for row in rows})
        summary = result["master_summary"]
        self.assertEqual(sum(category["required"] for category in summary.values()), len(rows))
        self.assertEqual(sum(category["created"] for category in summary.values()), len(rows))
        self.assertEqual(sum(category["ready"] for category in summary.values()), len(rows))
        self.assertEqual(sum(category["failed"] for category in summary.values()), 0)

    def test_step4_repairs_invalid_existing_master_and_counts_it_ready(self):
        dry_run_required = None
        with self.settings(TALLY_DRY_RUN=True), \
             patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            dry_run_required = prepare_master_results(self.batch)["masters"]
        existing_names = {row["name"].casefold(): row["name"] for row in dry_run_required}
        reads = {"GST Purchase 18%": 0}
        repaired = []

        def reread_master(name, _company="", _client=None):
            if name == "GST Purchase 18%":
                reads[name] += 1
                if reads[name] == 1:
                    return {"exists": True, "name": name, "parent": "Purchase Accounts",
                            "gst_applicable": "Applicable", "taxability": "Taxable", "supply_type": "Goods",
                            "gst_rate": "0", "outer_gst_rate": "0", "gst_rates": {}}
            return self.reread_master_ok(name, _company, _client)

        def write_master(_client, master, _company):
            repaired.append((master["name"], master.get("action")))
            return TallyResponse(altered=1, raw="<RESPONSE><ALTERED>1</ALTERED><ERRORS>0</ERRORS></RESPONSE>")

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=(existing_names, {}, {})), \
             patch("gst_tally.tally.service._write_master", side_effect=write_master), \
             patch("gst_tally.tally.service.ledger_details", side_effect=reread_master):
            result = prepare_master_results(self.batch, client=object())

        purchase = next(row for row in result["masters"] if row["name"] == "GST Purchase 18%")
        self.assertEqual(purchase["status"], "Existing")
        self.assertEqual(purchase["action"], "repaired")
        self.assertTrue(purchase["verified"])
        self.assertIn(("GST Purchase 18%", "Alter"), repaired)
        self.assertEqual(result["master_summary"]["accounts"]["ready"], result["master_summary"]["accounts"]["required"])

    def test_step4_uses_strategy_b_when_first_repair_leaves_nested_rate_details_missing(self):
        """Regression: Step 4 (prepare_master_results/_ensure_master_rows)
        previously stopped after a single repair write -- if that write left
        the outer rate correct but the nested GST Rate Details still absent
        (GST_RATE_DETAILS_INCOMPLETE), the master was reported Failed
        immediately instead of trying Strategy B's nested_only/flat_only
        variants like Step 6 does. Reproduces "Accounts 0/4, Failed 4" for
        GST Purchase 5/12/18/28%."""
        dry_run_required = None
        with self.settings(TALLY_DRY_RUN=True), \
             patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            dry_run_required = prepare_master_results(self.batch)["masters"]
        existing_names = {row["name"].casefold(): row["name"] for row in dry_run_required}
        reads = {"GST Purchase 18%": 0}
        repaired = []

        def reread_master(name, _company="", _client=None):
            if name == "GST Purchase 18%":
                reads[name] += 1
                # Read 1 (pre-repair check) and read 2 (right after Strategy
                # A's own repair write) both still show the broken shape --
                # outer rate already correct, nested GST Rate Details
                # confirmed absent -- so only Strategy B's own write (read 3)
                # actually fixes it.
                if reads[name] <= 2:
                    return {"exists": True, "name": name, "parent": "Purchase Accounts",
                            "gst_applicable": "Applicable", "taxability": "Taxable", "supply_type": "Goods",
                            "gst_rate": "18", "outer_gst_rate": "18", "gst_rate_details_popup_exists": False,
                            "set_alter_gst_rate_details": "No", "gst_rates": {}}
                return {"exists": True, "name": name, "parent": "Purchase Accounts",
                        "gst_applicable": "Applicable", "taxability": "Taxable", "supply_type": "Goods",
                        "gst_rate": "18", "outer_gst_rate": "18", "gst_rate_details_popup_exists": True,
                        "set_alter_gst_rate_details": "Yes", "gst_rates": {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"}}
            return self.reread_master_ok(name, _company, _client)

        def write_master(_client, master, _company, rate_mode="both"):
            repaired.append((master["name"], master.get("action")))
            return TallyResponse(altered=1, raw="<RESPONSE><ALTERED>1</ALTERED><ERRORS>0</ERRORS></RESPONSE>")

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=(existing_names, {}, {})), \
             patch("gst_tally.tally.service._write_master", side_effect=write_master), \
             patch("gst_tally.tally.service.ledger_details", side_effect=reread_master):
            result = prepare_master_results(self.batch, client=object())

        purchase = next(row for row in result["masters"] if row["name"] == "GST Purchase 18%")
        self.assertEqual(purchase["status"], "Existing", purchase.get("message"))
        self.assertEqual(purchase["action"], "repaired")
        self.assertTrue(purchase["verified"])
        self.assertEqual(purchase["actual_properties"]["gst_rates"], {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"})
        # Never a duplicate ledger -- every write is an ALTER of the exact name.
        self.assertTrue(all(name == "GST Purchase 18%" for name, _action in repaired))
        self.assertTrue(all(action == "Alter" for _name, action in repaired))
        self.assertGreaterEqual(len(repaired), 2)
        self.assertEqual(result["master_summary"]["accounts"]["failed"], 0)
        self.assertEqual(result["master_summary"]["accounts"]["ready"], result["master_summary"]["accounts"]["required"])


@override_settings(TALLY_DRY_RUN=False, GST_LOOKUP_PROVIDER="sandbox")
class SandboxVoucherEligibilityTests(TestCase):
    """Regression for every voucher becoming Skipped under the sandbox GST
    lookup provider: a party that was genuinely resolved (name known, valid
    GSTIN) must not be blocked purely because the sandbox provider's own
    `state_name` field came back blank -- state is always derivable from the
    GSTIN itself, exactly as `transaction_type` already does elsewhere."""

    GSTIN = "33AABCT7933K1Z4"
    COMPANY_GSTIN = "33AFHPM6103Q1Z8"
    COMPANY = "SRI MAHALAKSHMI TRADERS"

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr1.xlsx", file_type="EXCEL", gst_return_type="GSTR1",
            company_gstin=self.COMPANY_GSTIN,
            company_details={"company_name": self.COMPANY, "gstin": self.COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": self.COMPANY},
            source_parties={self.GSTIN: {"party_name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED"}})
        # A party the sandbox provider resolved a name for, but did not (or
        # could not) separately report a state for.
        GSTParty.objects.create(gstin=self.GSTIN, trade_name="RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
                                state_name="", lookup_status="Success", party_data_status="Complete")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="1", invoice_date=date(2025, 4, 1),
            customer_gstin=self.GSTIN, taxable_value=Decimal("6150.00"), tax_percent=Decimal("18"),
            cgst=Decimal("541.15"), sgst=Decimal("541.15"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("7232.30"), place_of_supply="33",
            source_line={"source_row_number": 2})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": self.COMPANY, "company": self.COMPANY,
                       "company_gstin": self.COMPANY_GSTIN, "gstin": self.COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}

    def test_a_valid_zero_difference_voucher_is_ready_not_skipped(self):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status):
            preview = voucher_preview(self.batch)

        row = next(v for v in preview["vouchers"] if v["invoice_number"] == "1")
        self.assertEqual(row["difference"], "0.00")
        self.assertEqual(row["status"], "Ready", row["reason"])
        self.assertTrue(row["import_eligible"])
        self.assertEqual(row["skip_reason_code"], "")
        self.assertEqual(preview["summary"]["eligible"], 1)
        self.assertEqual(preview["summary"]["skipped"], 0)

    def test_sandbox_name_fallback_is_ready_with_a_warning_not_skipped(self):
        # No local GSTParty row and no source-file party name either -- the
        # sandbox lookup could not enrich a name at all. GSTIN is still a
        # valid fallback identity (state/country/registration type are all
        # separately derivable) -- this must be Ready with an informational
        # warning, never Skipped. Sandbox lookup is optional enrichment, not
        # a voucher eligibility gate.
        other_gstin = "33ABCDE1234F1Z5"
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="2", invoice_date=date(2025, 4, 2),
            customer_gstin=other_gstin, taxable_value=Decimal("1000.00"), tax_percent=Decimal("18"),
            cgst=Decimal("90.00"), sgst=Decimal("90.00"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180.00"), place_of_supply="33",
            source_line={"source_row_number": 3})

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status):
            preview = voucher_preview(self.batch)

        row = next(v for v in preview["vouchers"] if v["invoice_number"] == "2")
        self.assertTrue(row["status"].startswith("Ready"), row["reason"])
        self.assertTrue(row["import_eligible"])
        self.assertEqual(row["warning_code"], "SANDBOX_PARTY_DETAILS_UNAVAILABLE")
        self.assertIn("Sandbox taxpayer details are unavailable", row["reason"])
        self.assertEqual(row["skip_reason_code"], "")
        self.assertEqual(row["status"], "Ready with GSTIN Fallback")

    def test_invalid_gstin_is_still_a_real_blocking_case(self):
        # Sandbox being optional enrichment never excuses a genuinely invalid GSTIN.
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="3", invoice_date=date(2025, 4, 3),
            customer_gstin="NOTAGSTIN", taxable_value=Decimal("1000.00"), tax_percent=Decimal("18"),
            cgst=Decimal("90.00"), sgst=Decimal("90.00"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180.00"), place_of_supply="33",
            source_line={"source_row_number": 4})

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status):
            preview = voucher_preview(self.batch)

        row = next(v for v in preview["vouchers"] if v["invoice_number"] == "3")
        self.assertFalse(row["import_eligible"])
        self.assertNotEqual(row["status"], "Ready")
        self.assertTrue(row["reason"])

    def test_sandbox_fallback_does_not_mask_a_genuine_total_mismatch(self):
        # A real invoice-total mismatch must still be Needs Attention even
        # when the party itself is a valid sandbox/GSTIN fallback.
        other_gstin = "33ABCDE1234F1Z5"
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="4", invoice_date=date(2025, 4, 4),
            customer_gstin=other_gstin, taxable_value=Decimal("1000.00"), tax_percent=Decimal("18"),
            cgst=Decimal("90.00"), sgst=Decimal("90.00"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("5000.00"), place_of_supply="33",
            source_line={"source_row_number": 5})

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status):
            preview = voucher_preview(self.batch)

        row = next(v for v in preview["vouchers"] if v["invoice_number"] == "4")
        self.assertEqual(row["status"], "Review Required")
        self.assertFalse(row["import_eligible"])
