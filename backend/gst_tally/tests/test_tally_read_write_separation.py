"""Regression tests for the Tally read/write request separation.

The preflight voucher lookup must be a read (Export) request answered by a read
parser. It must never reuse the master/voucher *import* envelope, and an import
acknowledgement must never be accepted as query data.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase, override_settings

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty, TallyVoucherMapping
from gst_tally.tally.master_builder import build_master
from gst_tally.tally.read_parsers import (classify_envelope, parse_company_query_response, parse_daybook_export,
                                          parse_export_envelope, parse_master_query_response,
                                          parse_voucher_query_response)
from gst_tally.tally.read_requests import (assert_read_request, build_company_query_xml,
                                           build_daybook_query_xml, build_ledger_query_xml,
                                           build_voucher_query_xml)
from gst_tally.tally.response_parser import TallyResponse, parse_import_response
from gst_tally.tally.service import import_batch
from gst_tally.tally.voucher_builder import build_voucher
from gst_tally.tally.voucher_verification import (_resolve_entry_rates, find_voucher,
                                                  query_vouchers, verify_voucher)
from xml.etree import ElementTree as ET


GSTIN = "33AAACB2894G1ZJ"
COMPANY = "SRI MAHALAKSHMI TRADERS,"
COMPANY_GSTIN = "33AFHPM6103Q1Z8"

# Captured verbatim from the live TallyPrime instance. A Day Book export with no
# vouchers in range comes back in Tally's re-importable wrapper: the HEADER says
# "Import Data" and the REPORTNAME says "All Masters", but REQUESTDATA is the
# (empty) export payload. This is a valid empty result, not a failed query.
LIVE_EMPTY_DAY_BOOK = b"""<ENVELOPE>
 <HEADER>
  <TALLYREQUEST>Import Data</TALLYREQUEST>
 </HEADER>
 <BODY>
  <IMPORTDATA>
   <REQUESTDESC>
    <REPORTNAME>All Masters</REPORTNAME>
    <STATICVARIABLES>
     <SVCURRENTCOMPANY>SRI MAHALAKSHMI TRADERS,</SVCURRENTCOMPANY>
    </STATICVARIABLES>
   </REQUESTDESC>
   <REQUESTDATA>   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>"""

# Captured verbatim from the live instance for a report Tally cannot resolve.
LIVE_REJECTED_REPORT = b"<RESPONSE>\n <LINEERROR>Could not find Report &apos;Zzz&apos;!</LINEERROR>\n</RESPONSE>"

IMPORT_ACKNOWLEDGEMENT = (b'<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><DATA>'
                          b'<IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS>'
                          b'<LASTVCHID>12</LASTVCHID></IMPORTRESULT></DATA></BODY></ENVELOPE>')

EMPTY_COLLECTION = (b'<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DESC>'
                    b'</DESC><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>')


def collection_with(vouchers):
    rows = "".join(
        f'<VOUCHER MASTERID="{row.get("master_id", "")}" ALTERID="{row.get("alter_id", "")}">'
        f'<DATE>{row.get("date", "")}</DATE>'
        f'<VOUCHERNUMBER>{row["number"]}</VOUCHERNUMBER>'
        f'<VOUCHERTYPENAME>{row["type"]}</VOUCHERTYPENAME>'
        f'<REFERENCE>{row.get("reference", "")}</REFERENCE>'
        f'<PARTYLEDGERNAME>{row.get("party", "")}</PARTYLEDGERNAME>'
        f'<GUID>{row.get("guid", "")}</GUID>'
        + ''.join(f'<LEDGERENTRIES.LIST><LEDGERNAME>{entry["ledger"]}</LEDGERNAME><AMOUNT>{entry["amount"]}</AMOUNT>'
                  f'<RATEOFINVOICETAX.LIST><RATEOFINVOICETAX>{entry["gst_rate"]}</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>'
                  f'</LEDGERENTRIES.LIST>' for entry in row.get("taxable_allocations", []))
        + '</VOUCHER>' for row in vouchers)
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DESC>'
            f'<STATICVARIABLES><SVCURRENTCOMPANY>{COMPANY}</SVCURRENTCOMPANY></STATICVARIABLES></DESC>'
            f'<DATA><COLLECTION>{rows}</COLLECTION></DATA></BODY></ENVELOPE>').encode()


class ReadRequestBuilderTests(SimpleTestCase):
    """(1)(2)(3) The voucher preflight builders are reads and only reads."""

    def test_voucher_preflight_builders_use_export_request_verbs(self):
        day_book = build_daybook_query_xml(COMPANY, "2025-04-01", "2025-04-30").decode()
        collection = build_voucher_query_xml(COMPANY, "2025-04-01", "2025-04-30").decode()

        self.assertIn("<TALLYREQUEST>Export Data</TALLYREQUEST>", day_book)
        self.assertIn("<REPORTNAME>Day Book</REPORTNAME>", day_book)
        self.assertIn("<TALLYREQUEST>Export</TALLYREQUEST>", collection)
        self.assertIn("<TYPE>Collection</TYPE>", collection)
        self.assertIn("<TYPE>Voucher</TYPE>", collection)

    def test_voucher_preflight_builders_never_emit_import_data(self):
        for builder in (lambda: build_daybook_query_xml(COMPANY, "2025-04-01", "2025-04-30"),
                        lambda: build_voucher_query_xml(COMPANY, "2025-04-01", "2025-04-30"),
                        build_ledger_query_xml, build_company_query_xml):
            payload = builder()
            with self.subTest(builder=builder):
                self.assertNotIn(b"Import Data", payload)
                self.assertFalse(assert_read_request(payload).casefold().startswith("import"))

    def test_write_builders_are_rejected_by_the_read_request_guard(self):
        master = build_master({"master_type": "Charge", "name": "Round Off", "group": "Indirect Expenses"})
        with self.assertRaises(ValueError):
            assert_read_request(master)

    def test_voucher_query_does_not_use_the_all_masters_report(self):
        for payload in (build_daybook_query_xml(COMPANY, "2025-04-01", "2025-04-30"),
                        build_voucher_query_xml(COMPANY, "2025-04-01", "2025-04-30")):
            with self.subTest(payload=payload[:60]):
                self.assertNotIn(b"All Masters", payload)

    def test_read_builders_target_the_requested_company_and_period(self):
        payload = build_voucher_query_xml(COMPANY, date(2025, 4, 1), date(2025, 4, 30)).decode()
        self.assertIn(f"<SVCURRENTCOMPANY>{COMPANY}</SVCURRENTCOMPANY>", payload)
        self.assertIn("<SVFROMDATE>20250401</SVFROMDATE>", payload)
        self.assertIn("<SVTODATE>20250430</SVTODATE>", payload)

    def test_company_query_fetches_active_tally_period_fields(self):
        payload = build_company_query_xml().decode()

        self.assertIn("<COMPUTE>FINANCIALYEARFROM:$$String:##SVFromDate</COMPUTE>", payload)
        self.assertIn("<COMPUTE>FINANCIALYEARTO:$$String:##SVToDate</COMPUTE>", payload)
        self.assertIn("FINANCIALYEARFROM", payload)
        self.assertIn("FINANCIALYEARTO", payload)


class ResponseParserPairingTests(SimpleTestCase):
    """(4)(5)(6)(9) Each request shape is answered by its own parser."""

    def test_day_book_request_is_read_by_the_day_book_parser(self):
        raw = (b'<ENVELOPE><BODY><DATA><DSPVCHDETAILS><DSPVCHNUMBER>PUR-1</DSPVCHNUMBER>'
               b'<DSPVCHTYPE>Purchase</DSPVCHTYPE><DSPVCHLEDACCOUNT>Supplier</DSPVCHLEDACCOUNT>'
               b'<VOUCHERID>9</VOUCHERID></DSPVCHDETAILS></DATA></BODY></ENVELOPE>')

        result = parse_daybook_export(raw, COMPANY)

        self.assertTrue(result["query_valid"])
        self.assertEqual(result["source"], "DAY_BOOK")
        self.assertEqual(result["vouchers"][0]["voucher_number"], "PUR-1")
        self.assertEqual(result["vouchers"][0]["identifier"], "9")

    def test_master_query_is_read_by_the_master_parser(self):
        raw = (b'<ENVELOPE><BODY><DATA><COLLECTION><LEDGER NAME="Supplier">'
               b'<NAME>Supplier</NAME><GSTREGISTRATIONNO>33AAACB2894G1ZJ</GSTREGISTRATIONNO>'
               b'</LEDGER></COLLECTION></DATA></BODY></ENVELOPE>')

        result = parse_master_query_response(raw)

        self.assertTrue(result["query_valid"])
        self.assertIn("supplier", result["names"])
        self.assertEqual(result["gstins"]["33AAACB2894G1ZJ"], "Supplier")

    def test_company_query_parser_maps_active_tally_financial_period(self):
        raw = (b'<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION>'
               b'<COMPANY NAME="SRI MAHALAKSHMI TRADERS,">'
               b'<STATENAME>Tamil Nadu</STATENAME>'
               b'<GSTREGISTRATIONNUMBER>33AFHPM6103Q1Z8</GSTREGISTRATIONNUMBER>'
               b'<FINANCIALYEARFROM>20250401</FINANCIALYEARFROM>'
               b'<FINANCIALYEARTO>20260331</FINANCIALYEARTO>'
               b'</COMPANY></COLLECTION></DATA></BODY></ENVELOPE>')

        result = parse_company_query_response(raw)

        company = result["companies"][0]
        self.assertEqual(company["financial_year_from"], "2025-04-01")
        self.assertEqual(company["financial_year_to"], "2026-03-31")
        self.assertEqual(company["financial_year"], "01 Apr 2025 - 31 Mar 2026")

    def test_company_query_parser_formats_tally_month_name_period_dates(self):
        raw = (b'<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION>'
               b'<COMPANY NAME="SRI MAHALAKSHMI TRADERS,">'
               b'<FINANCIALYEARFROM>1-Apr-25</FINANCIALYEARFROM>'
               b'<FINANCIALYEARTO>31-Mar-26</FINANCIALYEARTO>'
               b'</COMPANY></COLLECTION></DATA></BODY></ENVELOPE>')

        result = parse_company_query_response(raw)

        company = result["companies"][0]
        self.assertEqual(company["financial_year_from"], "2025-04-01")
        self.assertEqual(company["financial_year_to"], "2026-03-31")
        self.assertEqual(company["financial_year"], "01 Apr 2025 - 31 Mar 2026")

    def test_import_response_is_read_by_the_import_parser(self):
        response = parse_import_response(IMPORT_ACKNOWLEDGEMENT)

        self.assertTrue(response.accepted)
        self.assertEqual(response.created, 1)
        self.assertEqual(response.last_vch_id, "12")

    def test_import_acknowledgement_is_rejected_as_a_voucher_query_result(self):
        result = parse_voucher_query_response(IMPORT_ACKNOWLEDGEMENT, COMPANY)

        self.assertFalse(result["query_valid"])
        self.assertEqual(result["kind"], "IMPORT_RESULT")
        self.assertEqual(result["vouchers"], [])
        self.assertIn("import acknowledgement", result["reason"])

    def test_rejected_report_stays_an_explicit_query_failure(self):
        result = parse_daybook_export(LIVE_REJECTED_REPORT, COMPANY)

        self.assertFalse(result["query_valid"])
        self.assertEqual(result["kind"], "ERROR")
        self.assertIn("Could not find Report", result["reason"])

    def test_response_for_another_company_is_a_query_failure(self):
        result = parse_voucher_query_response(collection_with([]), "SOME OTHER COMPANY")

        self.assertFalse(result["query_valid"])
        self.assertIs(result["company_match"], False)
        self.assertIn("but 'SOME OTHER COMPANY' was requested", result["reason"])

    def test_envelope_classification_separates_data_errors_and_acknowledgements(self):
        cases = {"REPORT_EXPORT": LIVE_EMPTY_DAY_BOOK, "ERROR": LIVE_REJECTED_REPORT,
                 "IMPORT_RESULT": IMPORT_ACKNOWLEDGEMENT, "DATA_EXPORT": EMPTY_COLLECTION}
        for expected, raw in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify_envelope(ET.fromstring(raw.decode())), expected)


class VoucherQueryTests(SimpleTestCase):
    def test_query_back_does_not_infer_missing_allocation_rate_from_ledger_name(self):
        entries = [{"ledger": "GST Purchase 5%", "amount": "-190.00", "gst_rate": ""},
                   {"ledger": "GST Purchase 18%", "amount": "-5960.00", "gst_rate": "18",
                    "gst_rate_source": "EXPLICIT_ALLOCATION"}]
        match = {"ledger_entries": entries, "voucher_type": "Purchase", "voucher_number": "1",
                 "guid": "", "date": "20250401"}

        # The supplementary Day Book re-read (see _resolve_entry_rates) is
        # unreachable here (object() has no .post); the missing rate must stay
        # missing rather than being guessed from the "GST Purchase 5%" name.
        resolved = _resolve_entry_rates(object(), COMPANY, match)

        self.assertEqual(resolved[0]["gst_rate"], "")
        self.assertEqual(resolved[1]["gst_rate"], "18")

    def test_query_back_backfills_rate_from_day_book_when_collection_omits_it(self):
        """Verified against a live TallyPrime 7.1 instance: the Voucher
        collection read (VOUCHER_COLLECTION) never returns RATEOFINVOICETAX/
        RATEDETAILS on LEDGERENTRIES.LIST, even though they are listed in its
        FETCH -- every purchase ledger entry comes back with gst_rate == "".
        The standard Day Book export (a full native report, not FETCH-filtered)
        does carry them. _resolve_entry_rates re-reads the same voucher from
        the Day Book (matched by GUID) and backfills just the rate fields.
        """
        entries = [{"ledger": "GST Purchase 5%", "amount": "-190.00", "gst_rate": ""},
                   {"ledger": "GST Purchase 18%", "amount": "-5960.00", "gst_rate": ""}]
        match = {"ledger_entries": entries, "voucher_type": "Purchase", "voucher_number": "264",
                 "guid": "f7f5ec23-3f62-45b3-945f-6e7aed21dbb1-00000227", "date": "20250401"}
        day_book_raw = (
            b'<ENVELOPE><BODY><DATA><TALLYMESSAGE><VOUCHER><DATE>20250401</DATE>'
            b'<GUID>f7f5ec23-3f62-45b3-945f-6e7aed21dbb1-00000227</GUID>'
            b'<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME><VOUCHERNUMBER>264</VOUCHERNUMBER>'
            b'<REFERENCE>1</REFERENCE>'
            b'<LEDGERENTRIES.LIST><LEDGERNAME>GST Purchase 5%</LEDGERNAME><AMOUNT>-190.00</AMOUNT>'
            b'<RATEOFINVOICETAX.LIST><RATEOFINVOICETAX> 5</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>'
            b'</LEDGERENTRIES.LIST>'
            b'<LEDGERENTRIES.LIST><LEDGERNAME>GST Purchase 18%</LEDGERNAME><AMOUNT>-5960.00</AMOUNT>'
            b'<RATEOFINVOICETAX.LIST><RATEOFINVOICETAX> 18</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>'
            b'</LEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE></DATA></BODY></ENVELOPE>')

        class DayBookClient:
            def post(self, _payload):
                return day_book_raw

        resolved = _resolve_entry_rates(DayBookClient(), COMPANY, match)

        self.assertEqual({row["ledger"]: row["gst_rate"] for row in resolved},
                         {"GST Purchase 5%": "5", "GST Purchase 18%": "18"})

    def test_query_back_reads_first_purchase_rate_from_cgst_sgst_rate_details(self):
        raw = (b'<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER MASTERID="77">'
               b'<DATE>20250401</DATE><VOUCHERNUMBER>1</VOUCHERNUMBER>'
               b'<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME><REFERENCE>1</REFERENCE>'
               b'<LEDGERENTRIES.LIST><LEDGERNAME>GST Purchase 5%</LEDGERNAME><AMOUNT>-190.00</AMOUNT>'
               b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD><GSTRATE>2.5</GSTRATE></RATEDETAILS.LIST>'
               b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD><GSTRATE>2.5</GSTRATE></RATEDETAILS.LIST>'
               b'</LEDGERENTRIES.LIST>'
               b'<LEDGERENTRIES.LIST><LEDGERNAME>GST Purchase 18%</LEDGERNAME><AMOUNT>-5960.00</AMOUNT>'
               b'<RATEOFINVOICETAX.LIST><RATEOFINVOICETAX>18</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>'
               b'</LEDGERENTRIES.LIST></VOUCHER></COLLECTION></DATA></BODY></ENVELOPE>')

        result = parse_voucher_query_response(raw)
        entries = {row["ledger"]: row for row in result["vouchers"][0]["ledger_entries"]}

        self.assertEqual(entries["GST Purchase 5%"]["gst_rate"], "5")
        self.assertEqual(entries["GST Purchase 5%"]["gst_rate_source"], "GST_RATE_DETAILS")
        self.assertEqual(entries["GST Purchase 18%"]["gst_rate"], "18")

    """(7)(8)(13) Empty is a result; a present voucher is found; query-back reads."""

    def test_live_empty_day_book_export_returns_zero_vouchers_not_a_failure(self):
        envelope = parse_export_envelope(LIVE_EMPTY_DAY_BOOK, COMPANY, "voucher_preflight_query")

        self.assertTrue(envelope["query_valid"])
        self.assertEqual(envelope["kind"], "REPORT_EXPORT")
        self.assertEqual(parse_daybook_export(LIVE_EMPTY_DAY_BOOK, COMPANY)["vouchers"], [])

    def test_valid_empty_preflight_is_a_successful_query(self):
        class Client:
            def post(self, payload):
                return EMPTY_COLLECTION if b"Collection" in payload else LIVE_EMPTY_DAY_BOOK

        result = query_vouchers(Client(), COMPANY, "2025-04-01", "2025-04-30")

        self.assertTrue(result["query_valid"])
        self.assertEqual(result["vouchers"], [])
        self.assertEqual(result["from_date"], "20250401")
        self.assertEqual(result["to_date"], "20250430")

    def test_existing_voucher_is_detected_with_its_identifiers(self):
        class Client:
            def post(self, payload):
                return collection_with([{"number": "5", "type": "Purchase", "party": "Supplier",
                                         "reference": "PUR-1", "date": "20250401", "master_id": "77",
                                         "alter_id": "3", "guid": "abc-guid"}])

        result = query_vouchers(Client(), COMPANY, "2025-04-01", "2025-04-30")
        match = find_voucher(result["vouchers"], {"invoice_number": "PUR-1", "voucher_type": "Purchase",
                                                  "party": {"name": "Supplier"}})

        self.assertTrue(result["query_valid"])
        self.assertEqual(result["source"], "VOUCHER_COLLECTION")
        self.assertEqual(match["master_id"], "77")
        self.assertEqual(match["alter_id"], "3")
        self.assertEqual(match["guid"], "abc-guid")
        self.assertEqual(match["voucher_number"], "5")

    def test_cancelled_voucher_is_not_treated_as_an_existing_voucher(self):
        vouchers = [{"voucher_number": "5", "voucher_type": "Purchase", "reference": "PUR-1",
                     "party": "Supplier", "guid": "", "master_id": "77", "alter_id": "",
                     "date": "", "identifier": "77", "cancelled": True}]

        self.assertIsNone(find_voucher(vouchers, {"invoice_number": "PUR-1", "voucher_type": "Purchase",
                                                  "party": {"name": "Supplier"}}))

    def test_query_back_after_import_uses_a_read_request(self):
        sent = []

        class Client:
            def post(self, payload):
                sent.append(payload)
                return collection_with([{"number": "5", "type": "Purchase", "reference": "PUR-1",
                                         "party": "Supplier", "master_id": "77"}])

        result = verify_voucher(Client(), COMPANY, {"invoice_number": "PUR-1", "invoice_date": "2025-04-01",
                                                    "voucher_type": "Purchase", "party": {"name": "Supplier"}})

        self.assertTrue(result["found"])
        self.assertEqual(result["identifier"], "77")
        self.assertTrue(sent)
        for payload in sent:
            self.assertNotIn(b"Import Data", payload)
            self.assertFalse(assert_read_request(payload).casefold().startswith("import"))

    def test_both_reads_failing_stays_an_explicit_failure(self):
        class Client:
            def post(self, payload):
                return LIVE_REJECTED_REPORT

        result = query_vouchers(Client(), COMPANY, "2025-04-01", "2025-04-30")

        self.assertFalse(result["query_valid"])
        self.assertEqual(result["source"], "INVALID_QUERY_RESPONSE")
        self.assertIn("Could not find Report", result["reason"])


class RecordingClient:
    """Routes Tally traffic the way the live endpoint does, and records every payload."""

    def __init__(self, existing=None, write_accepted=True):
        self.payloads = []
        # Master writes go over native JSON (import_json), bypassing post()'s
        # XML-string routing entirely -- json_payloads records each one as
        # (payload_dict, object_id), and write_log interleaves both transports
        # in call order so tests can assert relative ordering (e.g. a master
        # repair write happening before the voucher write).
        self.json_payloads = []
        self.write_log = []
        self.existing = list(existing or [])
        self.written = []
        self.write_accepted = write_accepted
        self.last_http_status = 200

    @property
    def read_payloads(self):
        return [payload for payload in self.payloads if b"Import Data" not in payload]

    @property
    def write_payloads(self):
        return [payload for payload in self.payloads if b"Import Data" in payload]

    def _record_json(self, payload, object_id):
        self.json_payloads.append((payload, object_id))
        self.write_log.append(("json", payload))

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            self.write_log.append(("xml", payload))
            if b"<REPORTNAME>Vouchers</REPORTNAME>" in payload and self.write_accepted:
                root = ET.fromstring(payload)
                taxable = [{"ledger": entry.findtext("LEDGERNAME") or "", "amount": entry.findtext("AMOUNT") or "",
                            "gst_rate": entry.findtext("RATEOFINVOICETAX.LIST/RATEOFINVOICETAX") or ""}
                           for entry in root.findall(".//LEDGERENTRIES.LIST")]
                self.written.append({"number": root.findtext(".//VOUCHERNUMBER") or "",
                                     "type": (root.find(".//VOUCHER").get("VCHTYPE") or ""),
                                     "reference": root.findtext(".//REFERENCE") or "",
                                     "party": root.findtext(".//PARTYLEDGERNAME") or "",
                                     "master_id": "101", "alter_id": "2", "guid": "written-guid",
                                     "taxable_allocations": taxable})
            if not self.write_accepted:
                return (b'<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>0</CREATED><ERRORS>1</ERRORS>'
                        b'<LINEERROR>Rejected by Tally</LINEERROR></IMPORTRESULT></DATA></BODY></ENVELOPE>')
            return IMPORT_ACKNOWLEDGEMENT
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.existing + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION

    def import_data(self, payload):
        return parse_import_response(self.post(payload))

    def import_json(self, payload, object_id):
        # The base client has no special per-master tracking, so this just
        # mirrors write_accepted the same way post()'s "Import Data" branch
        # does for XML.
        self._record_json(payload, object_id)
        if not self.write_accepted:
            return TallyResponse(created=0, errors=1, line_error="Rejected by Tally")
        return TallyResponse(created=1)


class IncompletePartyRepairClient(RecordingClient):
    def __init__(self):
        super().__init__()
        self.party_repaired = False

    def import_json(self, payload, object_id):
        self._record_json(payload, object_id)
        metadata = payload["tallymessage"][0]["metadata"]
        if metadata["name"] == "Source Supplier" and metadata["action"] == "alter":
            self.party_repaired = True
        return TallyResponse(altered=1)

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            self.write_log.append(("xml", payload))
            return IMPORT_ACKNOWLEDGEMENT.replace(b"<CREATED>1</CREATED>", b"<CREATED>0</CREATED>").replace(
                b"<ALTERED>0</ALTERED>", b"<ALTERED>1</ALTERED>")
        if b"<TYPE>Object</TYPE>" in payload and b"<SUBTYPE>Ledger</SUBTYPE>" in payload and b"Source Supplier" in payload:
            if not self.party_repaired:
                return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="Source Supplier">'
                        b'<PARENT>Sundry Creditors</PARENT><LEDSTATENAME>Not Applicable</LEDSTATENAME>'
                        b'<COUNTRYNAME>Not Applicable</COUNTRYNAME><GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>'
                        b'</LEDGER></DATA></BODY></ENVELOPE>')
            return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="Source Supplier">'
                    b'<PARENT>Sundry Creditors</PARENT><GSTREGISTRATIONNO>33AAACB2894G1ZJ</GSTREGISTRATIONNO>'
                    b'<LEDSTATENAME>Tamil Nadu</LEDSTATENAME><COUNTRYNAME>India</COUNTRYNAME>'
                    b'<GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>'
                    b'<LEDGSTREGDETAILS.LIST><GSTIN>33AAACB2894G1ZJ</GSTIN>'
                    b'<STATE>Tamil Nadu</STATE><PLACEOFSUPPLY>Tamil Nadu</PLACEOFSUPPLY>'
                    b'</LEDGSTREGDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>')
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.existing + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION


class IncorrectPurchaseLedgerRepairClient(RecordingClient):
    def __init__(self):
        super().__init__()
        self.purchase_repaired = False

    def import_json(self, payload, object_id):
        self._record_json(payload, object_id)
        metadata = payload["tallymessage"][0]["metadata"]
        if metadata["name"] == "GST Purchase 12%" and metadata["action"] == "alter":
            self.purchase_repaired = True
        return TallyResponse(altered=1)

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            self.write_log.append(("xml", payload))
            if b"<REPORTNAME>Vouchers</REPORTNAME>" in payload:
                root = ET.fromstring(payload)
                taxable = [{"ledger": entry.findtext("LEDGERNAME") or "", "amount": entry.findtext("AMOUNT") or "",
                            "gst_rate": entry.findtext("RATEOFINVOICETAX.LIST/RATEOFINVOICETAX") or ""}
                           for entry in root.findall(".//LEDGERENTRIES.LIST")]
                self.written.append({"number": root.findtext(".//VOUCHERNUMBER") or "",
                                     "type": (root.find(".//VOUCHER").get("VCHTYPE") or ""),
                                     "reference": root.findtext(".//REFERENCE") or "",
                                     "party": root.findtext(".//PARTYLEDGERNAME") or "",
                                     "master_id": "101", "alter_id": "2", "guid": "written-guid",
                                     "taxable_allocations": taxable})
                return IMPORT_ACKNOWLEDGEMENT
            return IMPORT_ACKNOWLEDGEMENT.replace(b"<CREATED>1</CREATED>", b"<CREATED>0</CREATED>").replace(
                b"<ALTERED>0</ALTERED>", b"<ALTERED>1</ALTERED>")
        if b"<TYPE>Object</TYPE>" in payload and b"<SUBTYPE>Ledger</SUBTYPE>" in payload and b"GST Purchase 12%" in payload:
            rate = "12" if self.purchase_repaired else "0"
            return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="GST Purchase 12%">'
                    b'<PARENT>Purchase Accounts</PARENT><GSTAPPLICABLE>Applicable</GSTAPPLICABLE>'
                    b'<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY><GSTDETAILS.LIST>'
                    b'<APPLICABLEFROM>20250401</APPLICABLEFROM>'
                    b'<TAXABILITY>Taxable</TAXABILITY><SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>'
                    b'<STATEWISEDETAILS.LIST><STATENAME>Any</STATENAME>'
                    b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD><GSTRATE>' + rate.encode() +
                    b'</GSTRATE></RATEDETAILS.LIST>'
                    b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD><GSTRATE>6</GSTRATE></RATEDETAILS.LIST>'
                    b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD><GSTRATE>6</GSTRATE></RATEDETAILS.LIST>'
                    b'</STATEWISEDETAILS.LIST></GSTDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>')
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.existing + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION


class MissingNestedPurchaseLedgerRepairClient(RecordingClient):
    def __init__(self):
        super().__init__()
        self.purchase_repaired = False

    def import_json(self, payload, object_id):
        self._record_json(payload, object_id)
        metadata = payload["tallymessage"][0]["metadata"]
        if metadata["name"] == "GST Purchase 18%" and metadata["action"] == "alter":
            self.purchase_repaired = True
            gst_details = payload["tallymessage"][0].get("gstdetails") or []
            rows = gst_details[0]["statewisedetails"][0]["ratedetails"] if gst_details else []
            duty_rates = {row["gstratedutyhead"]: row["gstrate"] for row in rows}
            igst_rate = Decimal(str(duty_rates.get("IGST") or "0").strip())
            if igst_rate != Decimal("18"):
                return TallyResponse(created=0, errors=1, line_error="Missing canonical IGST row")
        return TallyResponse(altered=1)

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            self.write_log.append(("xml", payload))
            if b"<REPORTNAME>Vouchers</REPORTNAME>" in payload:
                root = ET.fromstring(payload)
                taxable = [{"ledger": entry.findtext("LEDGERNAME") or "", "amount": entry.findtext("AMOUNT") or "",
                            "gst_rate": entry.findtext("RATEOFINVOICETAX.LIST/RATEOFINVOICETAX") or ""}
                           for entry in root.findall(".//LEDGERENTRIES.LIST")]
                self.written.append({"number": root.findtext(".//VOUCHERNUMBER") or "",
                                     "type": (root.find(".//VOUCHER").get("VCHTYPE") or ""),
                                     "reference": root.findtext(".//REFERENCE") or "",
                                     "party": root.findtext(".//PARTYLEDGERNAME") or "",
                                     "master_id": "101", "alter_id": "2", "guid": "written-guid",
                                     "taxable_allocations": taxable})
            return IMPORT_ACKNOWLEDGEMENT.replace(b"<CREATED>1</CREATED>", b"<CREATED>0</CREATED>").replace(
                b"<ALTERED>0</ALTERED>", b"<ALTERED>1</ALTERED>")
        if b"<TYPE>Object</TYPE>" in payload and b"<SUBTYPE>Ledger</SUBTYPE>" in payload and b"GST Purchase 18%" in payload:
            if not self.purchase_repaired:
                return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="GST Purchase 18%">'
                        b'<PARENT>Purchase Accounts</PARENT><GSTAPPLICABLE>Applicable</GSTAPPLICABLE>'
                        b'<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>'
                        b'<RATEOFTAXCALCULATION>18</RATEOFTAXCALCULATION>'
                        b'<GSTDETAILS.LIST><APPLICABLEFROM>20250401</APPLICABLEFROM>'
                        b'<TAXABILITY>Taxable</TAXABILITY><SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>'
                        b'</GSTDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>')
            return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="GST Purchase 18%">'
                    b'<PARENT>Purchase Accounts</PARENT><GSTAPPLICABLE>Applicable</GSTAPPLICABLE>'
                    b'<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>'
                    b'<RATEOFTAXCALCULATION>18</RATEOFTAXCALCULATION>'
                    b'<GSTDETAILS.LIST><APPLICABLEFROM>20250401</APPLICABLEFROM>'
                    b'<TAXABILITY>Taxable</TAXABILITY><SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>'
                    b'<STATEWISEDETAILS.LIST><STATENAME>Any</STATENAME>'
                    b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD><GSTRATE>18</GSTRATE></RATEDETAILS.LIST>'
                    b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD><GSTRATE>9</GSTRATE></RATEDETAILS.LIST>'
                    b'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD><GSTRATE>9</GSTRATE></RATEDETAILS.LIST>'
                    b'</STATEWISEDETAILS.LIST></GSTDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>')
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.existing + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION


class PermanentlyBrokenPurchaseLedgerClient(RecordingClient):
    """Every write is accepted (ALTERED=1) but the re-read for GST Purchase
    18% always comes back with no nested GST Rate Details, regardless of
    which rate_mode variant was sent -- Strategy A and both Strategy B
    variants must all be exhausted and honestly reported as failed, never
    silently accepted from ALTERED=1 alone."""
    def import_json(self, payload, object_id):
        self._record_json(payload, object_id)
        return TallyResponse(altered=1)

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            self.write_log.append(("xml", payload))
            return IMPORT_ACKNOWLEDGEMENT.replace(b"<CREATED>1</CREATED>", b"<CREATED>0</CREATED>").replace(
                b"<ALTERED>0</ALTERED>", b"<ALTERED>1</ALTERED>")
        if b"<TYPE>Object</TYPE>" in payload and b"<SUBTYPE>Ledger</SUBTYPE>" in payload and b"GST Purchase 18%" in payload:
            return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="GST Purchase 18%">'
                    b'<PARENT>Purchase Accounts</PARENT><GSTAPPLICABLE>Applicable</GSTAPPLICABLE>'
                    b'<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>'
                    b'<RATEOFTAXCALCULATION>18</RATEOFTAXCALCULATION>'
                    b'<GSTDETAILS.LIST><APPLICABLEFROM>20250401</APPLICABLEFROM>'
                    b'<TAXABILITY>Taxable</TAXABILITY><SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>'
                    b'</GSTDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>')
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.existing + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION


class BrokenReadClient(RecordingClient):
    def import_json(self, payload, object_id):
        raise AssertionError("No write may be sent when the preflight query failed")

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            raise AssertionError("No write may be sent when the preflight query failed")
        return LIVE_REJECTED_REPORT


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class Step6PreflightTests(TestCase):
    """(10)(11)(12) Preflight outcome drives per-row classification."""

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

    def run_import(self, client):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            return import_batch(self.batch, client)

    def test_successful_empty_preflight_imports_instead_of_not_attempted(self):
        client = RecordingClient()

        result = self.run_import(client)
        row = next(row for row in result["results"] if row["invoice_no"] == "PUR-1")

        self.assertEqual(result["voucher_preflight"]["request_type"], "Export")
        self.assertTrue(result["voucher_preflight"]["query_valid"])
        self.assertEqual(result["voucher_preflight"]["existing_voucher_count"], 0)
        self.assertEqual(result["summary"]["not_attempted"], 0)
        self.assertTrue(result["actual_write_attempted"])
        self.assertEqual(row["status"], "Imported")
        self.assertEqual(result["summary"]["imported"], 1)

    def test_preflight_reads_never_reuse_the_import_envelope(self):
        client = RecordingClient()

        self.run_import(client)

        self.assertTrue(client.read_payloads)
        for payload in client.read_payloads:
            self.assertNotIn(b"All Masters", payload)
            self.assertFalse(assert_read_request(payload).casefold().startswith("import"))
        self.assertTrue(any(b"<REPORTNAME>Vouchers</REPORTNAME>" in payload for payload in client.write_payloads))

    def test_existing_tally_voucher_is_reported_as_already_imported(self):
        client = RecordingClient(existing=[{"number": "5", "type": "Purchase", "reference": "PUR-1",
                                            "party": "Source Supplier", "date": "20250401",
                                            "master_id": "77", "alter_id": "3", "guid": "abc-guid",
                                            "taxable_allocations": [
                                                {"ledger": "GST Purchase 18%", "amount": "-1000", "gst_rate": "18"},
                                                {"ledger": "Input CGST 9%", "amount": "-90", "gst_rate": "9"},
                                                {"ledger": "Input SGST 9%", "amount": "-90", "gst_rate": "9"},
                                            ]}])

        result = self.run_import(client)
        row = next(row for row in result["results"] if row["invoice_no"] == "PUR-1")

        self.assertEqual(row["status"], "Already Imported")
        self.assertEqual(row["voucher_identifier"], "77")
        self.assertEqual(result["summary"]["not_attempted"], 0)
        self.assertFalse(any(b"<REPORTNAME>Vouchers</REPORTNAME>" in payload for payload in client.write_payloads))
        self.assertTrue(TallyVoucherMapping.objects.filter(source_invoice_number="PUR-1",
                                                           import_status="Imported").exists())

    def test_current_period_ending_before_invoice_date_is_a_clear_preflight_failure(self):
        client = RecordingClient()

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2025, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            result = import_batch(self.batch, client)
        row = next(row for row in result["results"] if row["invoice_no"] == "PUR-1")

        self.assertEqual(result["batch_status"], "TALLY_CURRENT_PERIOD_MISMATCH")
        self.assertEqual(row["status"], "Not Attempted")
        self.assertIn("current period ends on 31-03-2025", row["reason"])
        self.assertFalse(client.write_payloads)

    def test_preflight_failure_produces_not_attempted_and_blocks_writes(self):
        client = BrokenReadClient()

        result = self.run_import(client)
        row = next(row for row in result["results"] if row["invoice_no"] == "PUR-1")

        self.assertEqual(result["batch_status"], "TALLY_QUERY_FAILED")
        self.assertEqual(row["status"], "Not Attempted")
        self.assertEqual(result["summary"]["not_attempted"], 1)
        self.assertFalse(result["actual_write_attempted"])
        self.assertIn("Tally query preflight failed", result["message"])

    def test_existing_incomplete_purchase_party_is_repaired_without_duplicate(self):
        client = IncompletePartyRepairClient()

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters",
                   return_value=({"source supplier": "Source Supplier"}, {}, {
                       "source supplier": {"name": "Source Supplier", "parent": "Sundry Creditors",
                                           "gstin": "", "state": "Not Applicable",
                                           "country": "Not Applicable", "registration_type": "Regular",
                                           "place_of_supply": ""}})):
            result = import_batch(self.batch, client)

        party_master = next(row for row in result["masters"] if row["master_type"] == "Party")
        party_writes = [payload for payload, object_id in client.json_payloads
                        if payload["tallymessage"][0]["metadata"]["name"] == "Source Supplier"]

        self.assertEqual(party_master["status"], "Existing")
        self.assertEqual(party_master["action"], "repaired")
        self.assertEqual(party_master["actual_properties"]["gstin"], GSTIN)
        self.assertEqual(party_master["actual_properties"]["state"], "Tamil Nadu")
        self.assertEqual(party_master["actual_properties"]["country"], "India")
        self.assertEqual(party_master["actual_properties"]["place_of_supply"], "Tamil Nadu")
        self.assertEqual(len(party_writes), 1)
        self.assertEqual(party_writes[0]["tallymessage"][0]["metadata"]["action"], "alter")

    def test_existing_zero_rate_purchase_ledger_is_altered_before_voucher_write(self):
        GSTInvoice.objects.update(tax_percent=Decimal("12"), cgst=Decimal("60"), sgst=Decimal("60"),
                                  invoice_value=Decimal("1120"))
        client = IncorrectPurchaseLedgerRepairClient()

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters",
                   return_value=({"gst purchase 12%": "GST Purchase 12%"}, {}, {})):
            result = import_batch(self.batch, client)

        purchase_master = next(row for row in result["masters"] if row["name"] == "GST Purchase 12%")
        purchase_writes = [payload for payload, object_id in client.json_payloads
                           if payload["tallymessage"][0]["metadata"]["name"] == "GST Purchase 12%"]
        voucher_write_index = next(i for i, entry in enumerate(client.write_log)
                                   if entry[0] == "xml" and b"<REPORTNAME>Vouchers</REPORTNAME>" in entry[1])
        repair_write_index = next(i for i, entry in enumerate(client.write_log)
                                  if entry[0] == "json"
                                  and entry[1]["tallymessage"][0]["metadata"]["name"] == "GST Purchase 12%"
                                  and entry[1]["tallymessage"][0]["metadata"]["action"] == "alter")

        self.assertEqual(purchase_master["status"], "Existing")
        self.assertEqual(purchase_master["action"], "repaired")
        self.assertEqual(purchase_master["actual_properties"]["parent"], "Purchase Accounts")
        self.assertEqual(purchase_master["actual_properties"]["taxability"], "Taxable")
        self.assertEqual(purchase_master["actual_properties"]["gst_rate"], "12")
        self.assertEqual(len(purchase_writes), 1)
        self.assertEqual(purchase_writes[0]["tallymessage"][0]["metadata"]["action"], "alter")
        self.assertLess(repair_write_index, voucher_write_index)

    def test_existing_purchase_ledger_with_outer_rate_but_missing_nested_rates_is_repaired(self):
        """A real Tally Ledger Alteration screen showing "GST Rate: 0%" for
        exactly this shape (outer RATEOFTAXCALCULATION correct, GST Rate
        Details popup/Set-Alter/nested breakdown all empty) proves that
        shape is genuinely broken, not a harmless API read quirk -- see
        _verify_master_properties' GST_RATE_DETAILS_INCOMPLETE check. The
        ledger must be repaired (ALTERed with the nested CGST/SGST/IGST rows
        added), re-read, and only then accepted -- never silently reused."""
        client = MissingNestedPurchaseLedgerRepairClient()

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters",
                   return_value=({"gst purchase 18%": "GST Purchase 18%"}, {}, {})):
            result = import_batch(self.batch, client)

        purchase_master = next(row for row in result["masters"] if row["name"] == "GST Purchase 18%")
        purchase_writes = [payload for payload, object_id in client.json_payloads
                           if payload["tallymessage"][0]["metadata"]["name"] == "GST Purchase 18%"]
        voucher_write_index = next(i for i, entry in enumerate(client.write_log)
                                   if entry[0] == "xml" and b"<REPORTNAME>Vouchers</REPORTNAME>" in entry[1])
        repair_write_index = next(i for i, entry in enumerate(client.write_log)
                                  if entry[0] == "json"
                                  and entry[1]["tallymessage"][0]["metadata"]["name"] == "GST Purchase 18%"
                                  and entry[1]["tallymessage"][0]["metadata"]["action"] == "alter")

        self.assertEqual(purchase_master["status"], "Existing")
        self.assertEqual(purchase_master["action"], "repaired")
        self.assertTrue(purchase_master["verified"])
        # After the repair, the re-read confirms the nested breakdown, not
        # just the outer rate.
        self.assertEqual(purchase_master["actual_properties"]["gst_rates"],
                         {"IGST": "18", "CGST": "9", "SGST/UTGST": "9"})
        self.assertTrue(client.purchase_repaired)
        # Exactly one repair write, containing the correct nested rows --
        # never a duplicate ledger, never left at the broken shape.
        self.assertEqual(len(purchase_writes), 1)
        self.assertEqual(purchase_writes[0]["tallymessage"][0]["metadata"]["action"], "alter")
        self.assertLess(repair_write_index, voucher_write_index)
        self.assertTrue(result["actual_write_attempted"])

    def test_permanently_broken_nested_rates_report_tally_gst_ledger_rate_not_applied(self):
        """When Strategy A and both Strategy B variants are exhausted and the
        nested GST Rate Details still never come back, the master must be
        reported Failed with TALLY_GST_LEDGER_RATE_NOT_APPLIED and the exact
        required message -- never accepted from ALTERED=1 alone, and the
        dependent voucher must not be imported against the broken ledger."""
        client = PermanentlyBrokenPurchaseLedgerClient()

        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters",
                   return_value=({"gst purchase 18%": "GST Purchase 18%"}, {}, {})):
            result = import_batch(self.batch, client)

        purchase_master = next(row for row in result["masters"] if row["name"] == "GST Purchase 18%")
        self.assertEqual(purchase_master["status"], "Failed")
        self.assertEqual(purchase_master["error_code"], "TALLY_GST_LEDGER_RATE_NOT_APPLIED")
        self.assertEqual(purchase_master["message"],
                         "GST Purchase 18% exists, but Tally GST Rate Details are incomplete. "
                         "Expected CGST 9%, SGST 9%, IGST 18%.")
        self.assertEqual(purchase_master["diagnostics"]["expected_rate"], "18")
        self.assertIn("strategy_b_flat_only_reread_rate", purchase_master["diagnostics"])
        self.assertIn("strategy_b_nested_only_reread_rate", purchase_master["diagnostics"])
        purchase_writes = [payload for payload, object_id in client.json_payloads
                           if payload["tallymessage"][0]["metadata"]["name"] == "GST Purchase 18%"]
        self.assertEqual(len(purchase_writes), 3)  # Strategy A + both Strategy B variants
        self.assertTrue(all(payload["tallymessage"][0]["metadata"]["action"] == "alter" for payload in purchase_writes))
