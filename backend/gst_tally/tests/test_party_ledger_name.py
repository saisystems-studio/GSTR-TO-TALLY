from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, TestCase

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTLedgerMapping, GSTParty
from gst_tally.services.party_ledger_name import resolve_party_ledger_name
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.master_builder import build_master, masters_for
from gst_tally.tally.voucher_builder import build_voucher
from gst_tally.tally.json_voucher_builder import build_json_voucher


class PartyLedgerNameResolverTests(SimpleTestCase):
    def test_name_priority_is_independent_of_address(self):
        cases = (
            ({"trade_name": "AMMAN CONSTRUCTIONS", "legal_name": "KANNAYA AMMAYAPPAN", "address": None}, "33CTKPA2881E1ZY", "AMMAN CONSTRUCTIONS"),
            ({"trade_name": "M. SIVAKUMAR", "address": None}, "33FRWPM7826N1ZP", "M. SIVAKUMAR"),
            ({"trade_name": None, "legal_name": "ABC PRIVATE LIMITED"}, "33XXXXXXXXXXXXX", "ABC PRIVATE LIMITED"),
            ({"trade_name": None, "legal_name": None, "party_name": None}, "33XXXXXXXXXXXXX", "33XXXXXXXXXXXXX"),
        )
        for party, gstin, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(resolve_party_ledger_name(party, gstin=gstin), expected)

    def test_acceptance_1_fetched_trade_name_is_used_never_the_gstin(self):
        # Fix-request Acceptance Test 1, verbatim values.
        party = {"trade_name": "EM VEERU & CO", "legal_name": "", "party_name": ""}
        self.assertEqual(resolve_party_ledger_name(party, gstin="33AALFE2101R1ZF"), "EM VEERU & CO")

    def test_acceptance_2_no_name_anywhere_falls_back_to_gstin(self):
        # Fix-request Acceptance Test 2: trade/legal/source name all empty.
        party = {"trade_name": "", "legal_name": "", "party_name": ""}
        self.assertEqual(resolve_party_ledger_name(party, gstin="33AABCT7933K1Z4"), "33AABCT7933K1Z4")

    def test_acceptance_3_lookup_unavailable_uses_source_name_not_gstin(self):
        # Fix-request Acceptance Test 3: no fetched party record at all (lookup
        # unavailable), but the source/imported row has a usable party name.
        source = {"party_name": "ABC TRADERS"}
        self.assertEqual(resolve_party_ledger_name(party=None, source=source, gstin="33XXXXXXXXXXXXX"), "ABC TRADERS")

    def test_gstin_placeholder_party_name_does_not_mask_source_name(self):
        party = {"gstin": "33AALFE2101R1ZF", "trade_name": "33AALFE2101R1ZF", "legal_name": ""}
        source = {"party_name": "EM VEERU & CO"}
        self.assertEqual(resolve_party_ledger_name(party=party, source=source, gstin="33AALFE2101R1ZF"), "EM VEERU & CO")


class PartyLedgerNameFlowTests(TestCase):
    def _voucher_and_master_names(self, gstin, trade_name, legal_name):
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.json", file_type="JSON", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
        )
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="1", invoice_date=date(2025, 4, 1),
            customer_gstin=gstin, taxable_value=Decimal("100.00"), tax_percent=Decimal("18"),
            cgst=Decimal("9.00"), sgst=Decimal("9.00"), igst=Decimal("0.00"),
            invoice_value=Decimal("118.00"), place_of_supply="Tamil Nadu",
        )
        GSTParty.objects.create(
            gstin=gstin, trade_name=trade_name,
            legal_name=legal_name, principal_place_of_business="",
        )
        GSTLedgerMapping.objects.create(
            gstin=gstin, gst_party_name=gstin, tally_ledger_name=gstin,
            mapping_type="BOTH", is_active=True,
        )

        vouchers = normalized_vouchers(batch, {"state": "Tamil Nadu"})
        party_master = next(row for row in masters_for(vouchers) if row["master_type"] == "Party")
        payload = build_json_voucher(vouchers[0], "D")
        voucher_party_name = next(
            row["ledgername"] for row in payload["tallymessage"][0]["ledgerentries"]
            if row["ispartyledger"]
        )
        return vouchers[0]["party"]["name"], party_master, voucher_party_name

    def test_stale_gstin_mapping_does_not_override_available_trade_name(self):
        voucher_name, party_master, serialized_name = self._voucher_and_master_names(
            "33CTKPA2881E1ZY", "AMMAN CONSTRUCTIONS", "KANNAYA AMMAYAPPAN",
        )

        self.assertEqual(voucher_name, "AMMAN CONSTRUCTIONS")
        self.assertEqual(party_master["name"], "AMMAN CONSTRUCTIONS")
        self.assertEqual(party_master["group"], "Sundry Creditors")
        self.assertEqual(serialized_name, "AMMAN CONSTRUCTIONS")

    def test_missing_address_keeps_sivakumar_name_in_master_and_voucher(self):
        voucher_name, party_master, serialized_name = self._voucher_and_master_names(
            "33FRWPM7826N1ZP", "M. SIVAKUMAR", "SIVAKUMAR MUTHIRULAPPAN",
        )

        self.assertEqual(voucher_name, "M. SIVAKUMAR")
        self.assertEqual(party_master["name"], "M. SIVAKUMAR")
        self.assertEqual(party_master["group"], "Sundry Creditors")
        self.assertEqual(serialized_name, "M. SIVAKUMAR")

    def test_voucher_and_master_reuse_source_name_when_saved_party_is_gstin_fallback(self):
        gstin = "33AALFE2101R1ZF"
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.json", file_type="JSON", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
            source_parties={gstin: {"party_name": "EM VEERU & CO"}},
        )
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="1", invoice_date=date(2025, 4, 1),
            customer_gstin=gstin, taxable_value=Decimal("100.00"), tax_percent=Decimal("18"),
            cgst=Decimal("9.00"), sgst=Decimal("9.00"), igst=Decimal("0.00"),
            invoice_value=Decimal("118.00"), place_of_supply="Tamil Nadu",
        )
        GSTParty.objects.create(
            gstin=gstin, trade_name=gstin, legal_name="", lookup_source="GSTIN",
            lookup_status="Sandbox Lookup Failed", party_data_status="Complete",
        )

        vouchers = normalized_vouchers(batch, {"state": "Tamil Nadu"})
        party_master = next(row for row in masters_for(vouchers) if row["master_type"] == "Party")
        payload = build_json_voucher(vouchers[0], "D")
        voucher_party_name = next(
            row["ledgername"] for row in payload["tallymessage"][0]["ledgerentries"]
            if row["ispartyledger"]
        )

        self.assertEqual(vouchers[0]["party"]["name"], "EM VEERU & CO")
        self.assertEqual(party_master["name"], "EM VEERU & CO")
        self.assertEqual(voucher_party_name, "EM VEERU & CO")

    def test_sandbox_party_details_reach_tally_party_master_fields(self):
        gstin = "33ABFFA3666C1ZU"
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.json", file_type="JSON", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
        )
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="1", invoice_date=date(2025, 4, 1),
            customer_gstin=gstin, taxable_value=Decimal("100.00"), tax_percent=Decimal("18"),
            cgst=Decimal("9.00"), sgst=Decimal("9.00"), igst=Decimal("0.00"),
            invoice_value=Decimal("118.00"), place_of_supply="Tamil Nadu",
        )
        GSTParty.objects.create(
            gstin=gstin, trade_name="ABC TRADERS", legal_name="ABC TRADERS PRIVATE LIMITED",
            principal_place_of_business="123 Example Street", state_name="Tamil Nadu",
            pincode="625001", taxpayer_type="Regular", lookup_source="sandbox",
            lookup_status="Fetched", party_data_status="Complete",
        )

        vouchers = normalized_vouchers(batch, {"state": "Tamil Nadu"})
        party_master = next(row for row in masters_for(vouchers) if row["master_type"] == "Party")

        self.assertEqual(party_master["name"], "ABC TRADERS")
        self.assertEqual(party_master["name_source"], "SANDBOX_TRADE_NAME")
        self.assertEqual(party_master["group"], "Sundry Creditors")
        self.assertEqual(party_master["address"], "123 Example Street")
        self.assertEqual(party_master["state"], "Tamil Nadu")
        self.assertEqual(party_master["country"], "India")
        self.assertEqual(party_master["registration_type"], "Regular")
        self.assertEqual(party_master["gstin"], gstin)
        self.assertEqual(party_master["place_of_supply"], "Tamil Nadu")

    def test_step4_matches_sandbox_party_by_normalized_gstin(self):
        gstin = "33AALFE2101R1ZF"
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.json", file_type="JSON", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
        )
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="1", invoice_date=date(2025, 4, 1),
            customer_gstin=" 33aalfe2101r1zf ", taxable_value=Decimal("100.00"),
            tax_percent=Decimal("18"), cgst=Decimal("9.00"), sgst=Decimal("9.00"),
            igst=Decimal("0.00"), invoice_value=Decimal("118.00"),
            place_of_supply="Tamil Nadu",
        )
        GSTParty.objects.create(
            gstin=gstin, trade_name="EM VEERU & CO", legal_name="",
            principal_place_of_business="No.27, Tamil Nadu, 626104",
            state_name="Tamil Nadu", pincode="626104", taxpayer_type="Regular",
            lookup_source="sandbox", lookup_status="Fetched", party_data_status="Complete",
        )

        vouchers = normalized_vouchers(batch, {"state": "Tamil Nadu"})
        party_master = next(row for row in masters_for(vouchers) if row["master_type"] == "Party")

        self.assertEqual(vouchers[0]["party"]["name"], "EM VEERU & CO")
        self.assertEqual(vouchers[0]["party"]["name_source"], "SANDBOX_TRADE_NAME")
        self.assertEqual(vouchers[0]["party"]["gstin"], gstin)
        self.assertEqual(party_master["name"], "EM VEERU & CO")
        self.assertEqual(party_master["name_source"], "SANDBOX_TRADE_NAME")
        self.assertEqual(party_master["gstin"], gstin)


class EndToEndPartyAccountVoucherConsistencyTests(TestCase):
    """The exact end-to-end scenario from the urgent 3-item fix: a real
    purchase invoice for GSTIN 33AALFE2101R1ZF / EM VEERU & CO must produce
    (1) a GST Purchase 12% account ledger with a real GST Rate, (2) a Party
    Master with Name/State/Country/GSTIN, and (3) a Purchase Voucher whose
    PARTYLEDGERNAME is the exact same resolved name as the Party Master --
    never the GSTIN -- checked on the actual generated XML, not just the
    intermediate voucher/master dicts."""

    def test_purchase_invoice_produces_consistent_party_account_and_voucher_xml(self):
        gstin = "33AALFE2101R1ZF"
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.json", file_type="JSON", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
        )
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="INV-1", invoice_date=date(2025, 4, 1),
            customer_gstin=gstin, taxable_value=Decimal("1000.00"), tax_percent=Decimal("12"),
            cgst=Decimal("60.00"), sgst=Decimal("60.00"), igst=Decimal("0.00"),
            invoice_value=Decimal("1120.00"), place_of_supply="Tamil Nadu",
        )
        GSTParty.objects.create(
            gstin=gstin, trade_name="EM VEERU & CO", legal_name="",
            principal_place_of_business="No.27, Tamil Nadu, 626104",
            state_name="Tamil Nadu", pincode="626104", taxpayer_type="Regular",
            lookup_source="sandbox", lookup_status="Fetched", party_data_status="Complete",
        )

        vouchers = normalized_vouchers(batch, {"state": "Tamil Nadu"})
        self.assertEqual(len(vouchers), 1)
        voucher = vouchers[0]
        required_masters = masters_for(vouchers)
        party_master = next(row for row in required_masters if row["master_type"] == "Party")
        account_master = next(row for row in required_masters if row["name"] == "GST Purchase 12%")

        # (1) GST Purchase 12% account ledger: real GST Rate, both flat and
        # nested representations, IGST=12/CGST=6/SGST=6.
        account_xml = ET.fromstring(build_master(account_master, "Test Company")).find(".//LEDGER")
        self.assertEqual(account_xml.get("NAME"), "GST Purchase 12%")
        self.assertEqual(account_xml.findtext("PARENT"), "Purchase Accounts")
        self.assertEqual(account_xml.findtext("RATEOFTAXCALCULATION"), "12")
        nested_rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                        for row in account_xml.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
        self.assertEqual(nested_rates["IGST"], "12")
        self.assertEqual(nested_rates["CGST"], "6")
        self.assertEqual(nested_rates["SGST/UTGST"], "6")

        # (2) Party Master: Name/State/Country/GSTIN, under Sundry Creditors
        # for a GSTR-2A/2B purchase flow.
        self.assertEqual(party_master["name"], "EM VEERU & CO")
        self.assertEqual(party_master["group"], "Sundry Creditors")
        self.assertEqual(party_master["state"], "Tamil Nadu")
        self.assertEqual(party_master["country"], "India")
        self.assertEqual(party_master["gstin"], gstin)
        party_xml = ET.fromstring(build_master(party_master, "Test Company")).find(".//LEDGER")
        self.assertEqual(party_xml.get("NAME"), "EM VEERU & CO")
        self.assertEqual(party_xml.findtext("PARENT"), "Sundry Creditors")
        self.assertEqual(party_xml.findtext("COUNTRYOFRESIDENCE"), "India")
        self.assertEqual(party_xml.findtext("COUNTRYNAME"), "India")
        self.assertEqual(party_xml.findtext("LEDSTATENAME"), "Tamil Nadu")
        self.assertEqual(party_xml.findtext("PARTYGSTIN"), gstin)
        self.assertEqual(party_xml.findtext(".//LEDGSTREGDETAILS.LIST/STATE"), "Tamil Nadu")

        # (3) Purchase Voucher: PARTYLEDGERNAME is the exact same resolved
        # name as the Party Master -- never the GSTIN.
        self.assertEqual(voucher["party"]["name"], "EM VEERU & CO")
        voucher_xml = ET.fromstring(build_voucher(voucher, "Test Company"))
        self.assertEqual(voucher_xml.findtext(".//PARTYLEDGERNAME"), "EM VEERU & CO")
        self.assertNotEqual(voucher_xml.findtext(".//PARTYLEDGERNAME"), gstin)
        party_entry = next(entry for entry in voucher_xml.findall(".//LEDGERENTRIES.LIST")
                           if entry.findtext("ISPARTYLEDGER") == "Yes")
        self.assertEqual(party_entry.findtext("LEDGERNAME"), "EM VEERU & CO")
        # Same exact string as the Party Master name -- opening this ledger
        # from the voucher resolves to the ledger actually created above.
        self.assertEqual(voucher_xml.findtext(".//PARTYLEDGERNAME"), party_master["name"])
