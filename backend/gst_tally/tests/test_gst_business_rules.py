from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from gst_tally.tally.json_master_builder import TALLY_ANY, TALLY_APPLICABLE, TALLY_NOT_APPLICABLE, build_json_master
from gst_tally.tally.connection import ledger_details
from gst_tally.tally.mappings import registration_type_for
from gst_tally.tally.master_builder import build_master
from gst_tally.tally.service import _verify_master_properties
from gst_tally.tally.return_mapping import SUPPORTED_RATES, extract_rate_from_name, normalize_ledger_key
from gst_tally.tally.validators import STATE_CODES, state_name, valid_gstin


class LedgerNameNormalizationTests(SimpleTestCase):
    """AB.4-9: spelling/spacing/@ variants must resolve to the same identity."""

    def test_gst_sales_variants_are_equivalent(self):
        variants = ["GST Sales 18%", "GSTSales18%", "GST SALES@18%", "GSTSales@18%", "GST Sales @ 18 %"]
        keys = {normalize_ledger_key(v) for v in variants}
        self.assertEqual(len(keys), 1)

    def test_gst_sales_at_18_extracts_rate(self):
        self.assertEqual(extract_rate_from_name("GSTSales@18%"), Decimal("18"))

    def test_gst_purchase_spaced_percent_extracts_rate(self):
        self.assertEqual(extract_rate_from_name("GST Purchase @ 12 %"), Decimal("12"))

    def test_cgst_at_2_5_extracts_rate(self):
        self.assertEqual(extract_rate_from_name("CGST@2.5%"), Decimal("2.5"))

    def test_sgst_at_2_5_extracts_rate(self):
        self.assertEqual(extract_rate_from_name("SGST@2.5%"), Decimal("2.5"))

    def test_input_cgst_at_2_5_extracts_rate(self):
        self.assertEqual(extract_rate_from_name("InputCGST@2.5%"), Decimal("2.5"))

    def test_input_sgst_at_2_5_extracts_rate(self):
        self.assertEqual(extract_rate_from_name("InputSGST@2.5%"), Decimal("2.5"))

    def test_cgst_sgst_igst_and_input_variants_all_normalize_equal(self):
        cases = [
            (["CGST2.5%", "CGST 2.5%", "CGST@2.5%"], "CGST2.5%"),
            (["SGST9%", "SGST 9%", "SGST@9%"], "SGST9%"),
            (["IGST18%", "IGST 18%", "IGST@18%"], "IGST18%"),
            (["InputCGST9%", "Input CGST 9%", "InputCGST@9%"], "INPUTCGST9%"),
            (["InputSGST14%", "Input SGST 14%", "InputSGST@14%"], "INPUTSGST14%"),
            (["InputIGST28%", "Input IGST 28%", "InputIGST@28%"], "INPUTIGST28%"),
        ]
        for variants, expected_key in cases:
            with self.subTest(expected_key=expected_key):
                self.assertEqual({normalize_ledger_key(v) for v in variants}, {expected_key})

    def test_normalization_does_not_collide_different_rates(self):
        self.assertNotEqual(normalize_ledger_key("CGST 9%"), normalize_ledger_key("CGST 14%"))


class StateCodeTests(SimpleTestCase):
    """AB.25-26: complete GST state/UT code resolution, not just Tamil Nadu."""

    def test_33_is_tamil_nadu(self):
        self.assertEqual(state_name("33"), "Tamil Nadu")

    def test_representative_other_state_codes_resolve(self):
        self.assertEqual(state_name("32"), "Kerala")
        self.assertEqual(state_name("29"), "Karnataka")
        self.assertEqual(state_name("36"), "Telangana")
        self.assertEqual(state_name("27"), "Maharashtra")
        self.assertEqual(state_name("24"), "Gujarat")
        self.assertEqual(state_name("09"), "Uttar Pradesh")
        self.assertEqual(state_name("07"), "Delhi")

    def test_all_38_state_ut_codes_plus_special_codes_are_mapped(self):
        expected_codes = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}
        self.assertEqual(set(STATE_CODES.keys()), expected_codes)


class RegistrationTypeTests(SimpleTestCase):
    """AB.23-24: Regular for a valid GSTIN, Unregistered otherwise."""

    def test_valid_gstin_without_fetched_taxpayer_type_is_regular(self):
        self.assertEqual(registration_type_for("33ABFFA3666C1ZU", None), "Regular")

    def test_valid_gstin_uses_fetched_taxpayer_type_when_present(self):
        class Party:
            taxpayer_type = "Composition"
        self.assertEqual(registration_type_for("33ABFFA3666C1ZU", Party()), "Composition")

    def test_blank_gstin_is_unregistered(self):
        self.assertEqual(registration_type_for("", None), "Unregistered")

    def test_invalid_gstin_is_unregistered(self):
        self.assertEqual(registration_type_for("NOT-A-GSTIN", None), "Unregistered")

    def test_valid_gstin_helper(self):
        self.assertTrue(valid_gstin("33ABFFA3666C1ZU"))
        self.assertFalse(valid_gstin(""))
        self.assertFalse(valid_gstin("33ABFFA3666C1Z"))


class PartyMasterPayloadTests(SimpleTestCase):
    """AB.19-24: GSTR-1 party -> Sundry Debtors, GSTR-2/2B party -> Sundry
    Creditors, Regular/Unregistered registration, and Country/Pincode set."""

    def party_master(self, group, registration_type, gstin=""):
        return {"master_type": "Party", "name": "AAKASH CONSTRUCTION", "group": group,
                "gstin": gstin, "state": "Tamil Nadu", "pincode": "625001",
                "registration_type": registration_type}

    def test_regular_party_xml_has_gstin_country_pincode_and_registration_type(self):
        master = self.party_master("Sundry Debtors", "Regular", "33ABFFA3666C1ZU")
        xml = ET.fromstring(build_master(master))
        ledger = xml.find(".//LEDGER")
        self.assertEqual(ledger.get("NAME"), "AAKASH CONSTRUCTION")
        self.assertEqual(ledger.findtext("PARENT"), "Sundry Debtors")
        self.assertEqual(ledger.findtext("GSTREGISTRATIONNO"), "33ABFFA3666C1ZU")
        self.assertEqual(ledger.findtext("COUNTRYNAME"), "India")
        self.assertEqual(ledger.findtext("COUNTRYOFRESIDENCE"), "India")
        self.assertEqual(ledger.findtext("PINCODE"), "625001")
        self.assertEqual(ledger.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(ledger.findtext(".//LEDMAILINGDETAILS.LIST/STATE"), "Tamil Nadu")
        self.assertEqual(ledger.findtext(".//LEDMAILINGDETAILS.LIST/COUNTRY"), "India")
        self.assertEqual(ledger.findtext(".//LEDMAILINGDETAILS.LIST/PINCODE"), "625001")
        self.assertEqual(ledger.findtext(".//LEDGSTREGDETAILS.LIST/GSTIN"), "33ABFFA3666C1ZU")
        self.assertEqual(ledger.findtext(".//LEDGSTREGDETAILS.LIST/STATE"), "Tamil Nadu")
        self.assertEqual(ledger.findtext(".//LEDGSTREGDETAILS.LIST/PLACEOFSUPPLY"), "Tamil Nadu")

    def test_tallyprime_dated_party_details_are_read_back(self):
        class Client:
            def post(self, _payload):
                return b'''<ENVELOPE><BODY><DATA><TALLYMESSAGE>
                    <LEDGER NAME="AAKASH CONSTRUCTION"><PARENT>Sundry Creditors</PARENT>
                    <PARTYGSTIN>33ABFFA3666C1ZU</PARTYGSTIN>
                    <LEDMAILINGDETAILS.LIST><STATE>Tamil Nadu</STATE><COUNTRY>India</COUNTRY><PINCODE>625001</PINCODE></LEDMAILINGDETAILS.LIST>
                    <LEDGSTREGDETAILS.LIST><GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE><STATE>Tamil Nadu</STATE><PLACEOFSUPPLY>Tamil Nadu</PLACEOFSUPPLY></LEDGSTREGDETAILS.LIST>
                    </LEDGER></TALLYMESSAGE></DATA></BODY></ENVELOPE>'''
        actual = ledger_details("AAKASH CONSTRUCTION", client=Client())
        self.assertEqual((actual["state"], actual["country"], actual["pincode"]), ("Tamil Nadu", "India", "625001"))
        self.assertEqual(actual["registration_type"], "Regular")
        self.assertEqual(actual["place_of_supply"], "Tamil Nadu")

    def test_blank_tally_place_of_supply_is_not_inferred_from_state(self):
        class Client:
            def post(self, _payload):
                return b'''<ENVELOPE><BODY><DATA><TALLYMESSAGE><LEDGER NAME="AAKASH CONSTRUCTION">
                    <LEDSTATENAME>Tamil Nadu</LEDSTATENAME>
                    <LEDGSTREGDETAILS.LIST><STATE>Tamil Nadu</STATE><PLACEOFSUPPLY></PLACEOFSUPPLY></LEDGSTREGDETAILS.LIST>
                    </LEDGER></TALLYMESSAGE></DATA></BODY></ENVELOPE>'''

        actual = ledger_details("AAKASH CONSTRUCTION", client=Client())

        self.assertEqual(actual["state"], "Tamil Nadu")
        self.assertEqual(actual["place_of_supply"], "")

    def test_unregistered_party_has_no_gstin_but_has_registration_type(self):
        master = self.party_master("Sundry Creditors", "Unregistered", "")
        xml = ET.fromstring(build_master(master))
        ledger = xml.find(".//LEDGER")
        self.assertEqual(ledger.findtext("PARENT"), "Sundry Creditors")
        self.assertIsNone(ledger.find("GSTREGISTRATIONNO"))
        self.assertEqual(ledger.findtext("GSTREGISTRATIONTYPE"), "Unregistered")

    def test_json_party_master_mirrors_the_xml_fields(self):
        master = self.party_master("Sundry Creditors", "Regular", "33ABFFA3666C1ZU")
        master["address"] = "12 Main Road, Chennai"
        master["place_of_supply"] = "Tamil Nadu"
        payload = build_json_master(master, "Company D")["tallymessage"][0]
        self.assertEqual(payload["parent"], "Sundry Creditors")
        self.assertEqual(payload["gstregistrationno"], "33ABFFA3666C1ZU")
        self.assertEqual(payload["partygstin"], "33ABFFA3666C1ZU")
        self.assertEqual(payload["ledstatename"], "Tamil Nadu")
        self.assertEqual(payload["countryname"], "India")
        self.assertEqual(payload["gstregistrationtype"], "Regular")
        self.assertEqual(payload["pincode"], "625001")
        self.assertEqual(payload["ledmailingdetails"][0]["mailingname"], "AAKASH CONSTRUCTION")
        self.assertEqual(payload["ledmailingdetails"][0]["address"][-1], "12 Main Road, Chennai")
        self.assertEqual(payload["ledmailingdetails"][0]["state"], "Tamil Nadu")
        self.assertEqual(payload["ledmailingdetails"][0]["country"], "India")
        self.assertEqual(payload["ledgstregdetails"][0]["gstregistrationtype"], "Regular")
        self.assertEqual(payload["ledgstregdetails"][0]["gstin"], "33ABFFA3666C1ZU")
        self.assertEqual(payload["ledgstregdetails"][0]["state"], "Tamil Nadu")
        self.assertEqual(payload["ledgstregdetails"][0]["placeofsupply"], "Tamil Nadu")
        self.assertEqual(payload["ledgstregdetails"][0]["isothterritoryassessee"], "No")
        self.assertEqual(payload["ledgstregdetails"][0]["considerpurchaseforexport"], "No")
        self.assertEqual(payload["ledgstregdetails"][0]["istransporter"], "No")
        self.assertEqual(payload["ledgstregdetails"][0]["iscommonparty"], "No")

    def test_existing_incomplete_party_requires_repair_of_same_ledger(self):
        master = self.party_master("Sundry Creditors", "Regular", "33AABCT7933K1Z4")
        master["place_of_supply"] = "Tamil Nadu"
        actual = {"name": "AAKASH CONSTRUCTION", "parent": "Sundry Creditors",
                  "gstin": "", "state": "Not Applicable", "country": "Not Applicable",
                  "registration_type": "Regular", "place_of_supply": ""}

        verification = _verify_master_properties(master, actual)
        repair = ET.fromstring(build_master({**master, "action": "Alter"})).find(".//LEDGER")

        self.assertFalse(verification["valid"])
        self.assertEqual(repair.get("NAME"), "AAKASH CONSTRUCTION")
        self.assertEqual(repair.get("ACTION"), "Alter")
        self.assertEqual(repair.findtext(".//LEDGSTREGDETAILS.LIST/GSTIN"), "33AABCT7933K1Z4")

    def test_party_alter_sends_simple_fields_and_dated_details_for_live_tally_repair(self):
        """TallyPrime 7.1 keeps simple top-level fields such as
        COUNTRYOFRESIDENCE/PARTYGSTIN during a ledger ALTER, while the dated
        collections carry the newer GST registration and mailing details."""
        master = self.party_master("Sundry Creditors", "Regular", "33ABFFA3666C1ZU")
        master["place_of_supply"] = "Tamil Nadu"
        master["address"] = "12 Main Road, Chennai"

        alter = ET.fromstring(build_master({**master, "action": "Alter"})).find(".//LEDGER")

        self.assertEqual(alter.findtext("LEDSTATENAME"), "Tamil Nadu")
        self.assertEqual(alter.findtext("PARTYGSTIN"), "33ABFFA3666C1ZU")
        self.assertEqual(alter.findtext("GSTREGISTRATIONNO"), "33ABFFA3666C1ZU")
        self.assertEqual(alter.findtext("ADDRESS.LIST/ADDRESS"), "12 Main Road, Chennai")
        self.assertEqual(alter.findtext("COUNTRYOFRESIDENCE"), "India")
        self.assertIsNone(alter.find("COUNTRYNAME"))
        self.assertEqual(alter.findtext("PINCODE"), "625001")
        self.assertEqual(alter.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(alter.findtext(".//LEDMAILINGDETAILS.LIST/STATE"), "Tamil Nadu")
        self.assertEqual(alter.findtext(".//LEDMAILINGDETAILS.LIST/COUNTRY"), "India")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/STATE"), "Tamil Nadu")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/PLACEOFSUPPLY"), "Tamil Nadu")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/GSTIN"), "33ABFFA3666C1ZU")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/ISOTHTERRITORYASSESSEE"), "No")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/CONSIDERPURCHASEFOREXPORT"), "No")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/ISTRANSPORTER"), "No")
        self.assertEqual(alter.findtext(".//LEDGSTREGDETAILS.LIST/ISCOMMONPARTY"), "No")

    def test_party_create_still_sends_both_legacy_scalars_and_dated_aggregates(self):
        master = self.party_master("Sundry Creditors", "Regular", "33ABFFA3666C1ZU")

        create = ET.fromstring(build_master(master)).find(".//LEDGER")

        self.assertEqual(create.get("ACTION"), "Create")
        self.assertEqual(create.findtext("LEDSTATENAME"), "Tamil Nadu")
        self.assertEqual(create.findtext("COUNTRYNAME"), "India")
        self.assertEqual(create.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertIsNotNone(create.find("LEDGSTREGDETAILS.LIST"))
        self.assertIsNotNone(create.find("LEDMAILINGDETAILS.LIST"))

    def test_json_party_alter_sends_simple_fields_and_dated_details_like_xml_builder(self):
        master = self.party_master("Sundry Creditors", "Regular", "33ABFFA3666C1ZU")
        master["place_of_supply"] = "Tamil Nadu"

        alter = build_json_master({**master, "action": "Alter"}, "Company D")["tallymessage"][0]
        create = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(alter["metadata"]["action"], "alter")
        self.assertEqual(alter["ledstatename"], "Tamil Nadu")
        self.assertEqual(alter["partygstin"], "33ABFFA3666C1ZU")
        self.assertEqual(alter["gstregistrationno"], "33ABFFA3666C1ZU")
        self.assertEqual(alter["pincode"], "625001")
        self.assertEqual(alter["countryofresidence"], "India")
        self.assertNotIn("countryname", alter)
        self.assertEqual(alter["gstregistrationtype"], "Regular")
        self.assertEqual(alter["ledgstregdetails"][0]["state"], "Tamil Nadu")
        self.assertEqual(alter["ledgstregdetails"][0]["gstin"], "33ABFFA3666C1ZU")
        self.assertEqual(alter["ledgstregdetails"][0]["isothterritoryassessee"], "No")
        self.assertEqual(alter["ledgstregdetails"][0]["considerpurchaseforexport"], "No")
        self.assertEqual(alter["ledgstregdetails"][0]["istransporter"], "No")
        self.assertEqual(alter["ledgstregdetails"][0]["iscommonparty"], "No")
        self.assertEqual(create["metadata"]["action"], "create")
        self.assertEqual(create["ledstatename"], "Tamil Nadu")
        self.assertEqual(create["countryname"], "India")


class TaxableAccountLedgerMasterTests(SimpleTestCase):
    def _master(self, name, group, rate, action="Create", applicable_from=""):
        return {
            "master_type": "Purchase" if group == "Purchase Accounts" else "Sales",
            "name": name,
            "group": group,
            "gst_rate": rate,
            "supply_type": "Goods",
            "action": action,
            "applicable_from": applicable_from,
        }

    def _xml_ledger(self, name, group, rate, action="Create", applicable_from=""):
        return ET.fromstring(build_master(self._master(name, group, rate, action, applicable_from))).find(".//LEDGER")

    def test_required_purchase_and_sales_account_masters_carry_tally_rate_details(self):
        expected = {
            ("Purchase Accounts", "GST Purchase", "1"): {"CGST": "0.5", "SGST/UTGST": "0.5", "IGST": "1"},
            ("Purchase Accounts", "GST Purchase", "5"): {"CGST": "2.5", "SGST/UTGST": "2.5", "IGST": "5"},
            ("Purchase Accounts", "GST Purchase", "18"): {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"},
            ("Purchase Accounts", "GST Purchase", "40"): {"CGST": "20", "SGST/UTGST": "20", "IGST": "40"},
            ("Sales Accounts", "GST Sales", "5"): {"CGST": "2.5", "SGST/UTGST": "2.5", "IGST": "5"},
            ("Sales Accounts", "GST Sales", "18"): {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"},
        }

        self.assertEqual([f"{rate:g}" for rate in SUPPORTED_RATES], ["1", "3", "5", "12", "18", "28", "40"])
        for (group, prefix, rate), expected_rates in expected.items():
            with self.subTest(group=group, rate=rate):
                ledger = self._xml_ledger(f"{prefix} {rate}%", group, rate)
                gst = ledger.find("GSTDETAILS.LIST")
                state = gst.find("STATEWISEDETAILS.LIST")
                rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                         for row in state.findall("RATEDETAILS.LIST")}

                self.assertEqual(ledger.findtext("PARENT"), group)
                self.assertEqual(ledger.findtext("GSTAPPLICABLE"), "Applicable")
                self.assertEqual(ledger.findtext("GSTTYPEOFSUPPLY"), "Goods")
                self.assertEqual(gst.findtext("SUPPLYTYPE"), "Goods")
                self.assertEqual(gst.findtext("TAXABILITY"), "Taxable")
                self.assertEqual(gst.findtext("SRCOFGSTDETAILS"), "Specify Details Here")
                self.assertEqual(state.findtext("STATENAME") or "", "")
                self.assertEqual({key: rates[key] for key in ("CGST", "SGST/UTGST", "IGST")}, expected_rates)

    def test_purchase_12_master_carries_full_taxable_gst_rate(self):
        ledger = self._xml_ledger("GST Purchase 12%", "Purchase Accounts", "12")
        rate_rows = ledger.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                 for row in rate_rows}
        per_unit_rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATEPERUNIT")
                 for row in ledger.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}

        self.assertEqual(ledger.findtext("PARENT"), "Purchase Accounts")
        self.assertEqual(ledger.findtext("TAXTYPE"), "Others")
        self.assertEqual(ledger.findtext("GSTAPPLICABLE"), "Applicable")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/SRCOFGSTDETAILS"), "Specify Details Here")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/TAXABILITY"), "Taxable")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/APPLICABLEFROM"), "20230401")
        self.assertEqual(rates["IGST"], "12")
        self.assertEqual(rates["CGST"], "6")
        self.assertEqual(rates["SGST/UTGST"], "6")
        self.assertEqual(per_unit_rates, {"CGST": "0", "SGST/UTGST": "0", "IGST": "0", "Cess": "0", "State Cess": "0"})

    def test_company_books_date_is_used_for_gst_history(self):
        ledger = self._xml_ledger("GST Purchase 12%", "Purchase Accounts", "12", applicable_from="2025-04-30")
        master = self._master("GST Purchase 12%", "Purchase Accounts", "12", applicable_from="2025-04-30")
        master["company_books_from"] = date(2025, 4, 1)
        ledger = ET.fromstring(build_master(master)).find(".//LEDGER")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/APPLICABLEFROM"), "20250401")

    def test_purchase_18_master_carries_full_igst_rate_not_half_rate(self):
        ledger = self._xml_ledger("GST Purchase 18%", "Purchase Accounts", "18")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                 for row in ledger.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}

        self.assertEqual(rates["IGST"], "18")
        self.assertNotEqual(rates["IGST"], "9")

    def test_sales_5_master_carries_sales_parent_and_full_taxable_gst_rate(self):
        ledger = self._xml_ledger("GST Sales 5%", "Sales Accounts", "5")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                 for row in ledger.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}

        self.assertEqual(ledger.findtext("PARENT"), "Sales Accounts")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/TAXABILITY"), "Taxable")
        self.assertEqual(rates["IGST"], "5")

    def test_existing_exempt_account_ledger_with_taxable_taxability_requires_repair(self):
        master = {
            "master_type": "Purchase",
            "name": "GST Exempted",
            "group": "Purchase Accounts",
            "gst_rate": "0",
            "taxability": "Exempt",
            "supply_type": "Goods",
        }
        actual = {"name": "GST Exempted", "parent": "Purchase Accounts",
                  "gst_applicable": "Applicable", "taxability": "Taxable",
                  "supply_type": "Goods", "gst_rate": "0",
                  "gst_rate_details": "Specify Details Here",
                  "gst_rate_history_exists": True,
                  "gst_rates": {"CGST": "0", "SGST/UTGST": "0", "IGST": "0"}}

        verification = _verify_master_properties(master, actual)

        self.assertFalse(verification["valid"])
        self.assertIn("Taxability Type expected 'Exempt'", verification["reason"])

    def test_existing_zero_rate_purchase_ledger_requires_alter_of_same_ledger(self):
        master = self._master("GST Purchase 12%", "Purchase Accounts", "12")
        actual = {"name": "GST Purchase 12%", "parent": "Purchase Accounts",
                  "gst_applicable": "Applicable", "taxability": "Taxable",
                  "supply_type": "Goods", "gst_rate": "0"}

        verification = _verify_master_properties(master, actual)
        repair = ET.fromstring(build_master({**master, "action": "Alter"})).find(".//LEDGER")

        self.assertFalse(verification["valid"])
        self.assertIn("rate expected 12%", verification["reason"])
        self.assertEqual(repair.get("NAME"), "GST Purchase 12%")
        self.assertEqual(repair.get("ACTION"), "Alter")
        self.assertEqual(repair.findtext("PARENT"), "Purchase Accounts")
        self.assertEqual(repair.findtext(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST[GSTRATEDUTYHEAD='IGST']/GSTRATE"), "12")

    def test_existing_master_with_wrong_nested_components_requires_repair(self):
        master = self._master("GST Purchase 18%", "Purchase Accounts", "18")
        actual = {"name": "GST Purchase 18%", "parent": "Purchase Accounts",
                  "gst_applicable": "Applicable", "taxability": "Taxable",
                  "supply_type": "Goods", "gst_rate": "18.000",
                  "gst_rate_details": "Specify Details Here",
                  "gst_rate_history_exists": True,
                  "gst_rate_details_popup_exists": True,
                  "set_alter_gst_rate_details": "Yes",
                  "gst_rates": {"CGST": "0", "SGST/UTGST": "0.00", "IGST": "18.000"}}

        verification = _verify_master_properties(master, actual)
        repair = ET.fromstring(build_master({**master, "action": "Alter"})).find(".//LEDGER")

        self.assertFalse(verification["valid"])
        self.assertIn("nested CGST GST rate expected 9%", verification["reason"])
        self.assertIn("nested SGST/UTGST GST rate expected 9%", verification["reason"])
        self.assertEqual(repair.get("ACTION"), "Alter")
        self.assertEqual(repair.findtext(".//RATEDETAILS.LIST[GSTRATEDUTYHEAD='IGST']/GSTRATE"), "18")
        self.assertEqual(repair.findtext(".//RATEDETAILS.LIST[GSTRATEDUTYHEAD='CGST']/GSTRATE"), "9")
        self.assertEqual(repair.findtext(".//RATEDETAILS.LIST[GSTRATEDUTYHEAD='SGST/UTGST']/GSTRATE"), "9")

    def test_purchase_account_masters_write_tally_internal_nested_rate_rows(self):
        expected = {
            "5": {"IGST": "5", "CGST": "2.5", "SGST/UTGST": "2.5"},
            "12": {"IGST": "12", "CGST": "6", "SGST/UTGST": "6"},
            "18": {"IGST": "18", "CGST": "9", "SGST/UTGST": "9"},
            "28": {"IGST": "28", "CGST": "14", "SGST/UTGST": "14"},
        }
        for rate, expected_rates in expected.items():
            with self.subTest(rate=rate):
                ledger = self._xml_ledger(f"GST Purchase {rate}%", "Purchase Accounts", rate, action="Alter")
                rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                         for row in ledger.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}

                self.assertEqual(ledger.get("ACTION"), "Alter")
                self.assertEqual(rates, {**expected_rates, "Cess": "0", "State Cess": "0"})

    def test_purchase_account_gst_rate_details_use_tally_typed_master_shape(self):
        """The successful Tally export for a GST Purchase ledger carries TYPE
        metadata on the GST Rate & Related Details fields. The import payload
        should mirror that master shape so Tally persists the popup rows,
        not only the outer RATEOFTAXCALCULATION."""
        ledger = self._xml_ledger("GST Purchase 12%", "Purchase Accounts", "12", action="Alter")
        gst = ledger.find("GSTDETAILS.LIST")
        state = gst.find("STATEWISEDETAILS.LIST")
        igst = next(row for row in state.findall("RATEDETAILS.LIST")
                    if row.findtext("GSTRATEDUTYHEAD") == "IGST")
        cess = next(row for row in state.findall("RATEDETAILS.LIST")
                    if row.findtext("GSTRATEDUTYHEAD") == "Cess")

        self.assertEqual(ledger.find("PARENT").get("TYPE"), "String")
        self.assertEqual(ledger.find("TAXTYPE").get("TYPE"), "String")
        self.assertEqual(ledger.find("GSTTYPEOFSUPPLY").get("TYPE"), "String")
        self.assertEqual(ledger.find("RATEOFTAXCALCULATION").get("TYPE"), "Number")
        self.assertEqual(gst.find("APPLICABLEFROM").get("TYPE"), "Date")
        self.assertEqual(gst.find("SUPPLYTYPE").get("TYPE"), "String")
        self.assertEqual(gst.find("TAXABILITY").get("TYPE"), "String")
        self.assertEqual(gst.find("SRCOFGSTDETAILS").get("TYPE"), "String")
        self.assertEqual(state.find("STATENAME").get("TYPE"), "String")
        self.assertEqual(igst.find("GSTRATEDUTYHEAD").get("TYPE"), "String")
        self.assertEqual(igst.find("GSTRATEVALUATIONTYPE").get("TYPE"), "String")
        self.assertEqual(igst.find("GSTRATE").get("TYPE"), "Number")
        self.assertEqual(igst.find("GSTRATEPERUNIT").get("TYPE"), "Number")
        self.assertEqual(cess.findtext("GSTRATEVALUATIONTYPE"), "Not Applicable")

    def test_json_taxable_account_master_mirrors_xml_gst_rate_details(self):
        message = build_json_master(self._master("GST Sales 5%", "Sales Accounts", "5"), "Company D")["tallymessage"][0]

        self.assertEqual(message["parent"], "Sales Accounts")
        self.assertEqual(message["gstapplicable"], TALLY_APPLICABLE)
        self.assertEqual(message["gstdetails"][0]["taxability"], "Taxable")
        self.assertEqual(message["gstdetails"][0]["srcofgstdetails"], "Specify Details Here")
        rates = {row["gstratedutyhead"]: row["gstrate"]
                 for row in message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]}
        per_unit_rates = {row["gstratedutyhead"]: row["gstrateperunit"]
                          for row in message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]}
        self.assertEqual(rates["IGST"], " 5")
        self.assertEqual(per_unit_rates, {"CGST": "0", "SGST/UTGST": "0", "IGST": "0", "Cess": "0", "State Cess": "0"})

    def test_json_existing_zero_rate_taxable_master_alters_same_ledger_with_full_igst_rate(self):
        message = build_json_master(
            self._master("GST Purchase 12%", "Purchase Accounts", "12", action="Alter"), "Company D"
        )["tallymessage"][0]
        rates = {row["gstratedutyhead"]: row["gstrate"]
                 for row in message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]}

        self.assertEqual(message["metadata"]["action"], "alter")
        self.assertEqual(message["metadata"]["name"], "GST Purchase 12%")
        self.assertEqual(message["name"], "GST Purchase 12%")
        self.assertEqual(message["parent"], "Purchase Accounts")
        self.assertEqual(message["gstdetails"][0]["srcofgstdetails"], "Specify Details Here")
        self.assertEqual(message["gstdetails"][0]["taxability"], "Taxable")
        self.assertEqual(rates["IGST"], " 12")

    def test_json_purchase_account_masters_carry_exact_tally_native_enums_and_leading_space_rates(self):
        """Verified against a real Tally export: native JSON master writes use
        Tally's internal "\\x04 " fixed-list markers for gstapplicable/
        statename/gstratevaluationtype, and prefix every NUMBER-type rate
        scalar with a leading space (whole rates unadorned, a fractional half
        shown to exactly two decimals). Plain "Applicable"/"Any"/"Not
        Applicable" strings must never appear in these positions -- Tally
        silently ignores mismatched enum values, so the exact byte sequence
        matters."""
        expected = {
            "3": {"IGST": " 3", "CGST": " 1.50", "SGST/UTGST": " 1.50"},
            "5": {"IGST": " 5", "CGST": " 2.50", "SGST/UTGST": " 2.50"},
            "12": {"IGST": " 12", "CGST": " 6", "SGST/UTGST": " 6"},
            "18": {"IGST": " 18", "CGST": " 9", "SGST/UTGST": " 9"},
            "28": {"IGST": " 28", "CGST": " 14", "SGST/UTGST": " 14"},
            "40": {"IGST": " 40", "CGST": " 20", "SGST/UTGST": " 20"},
        }
        for rate, expected_rates in expected.items():
            with self.subTest(rate=rate):
                message = build_json_master(
                    self._master(f"GST Purchase {rate}%", "Purchase Accounts", rate, action="Alter"), "Company D",
                )["tallymessage"][0]
                gst_details = message["gstdetails"][0]
                statewise = gst_details["statewisedetails"][0]
                rows = {row["gstratedutyhead"]: row for row in statewise["ratedetails"]}
                rates = {head: row["gstrate"] for head, row in rows.items()}

                self.assertEqual(message["gstapplicable"], TALLY_APPLICABLE)
                self.assertEqual(message["gstapplicable"], "\x04 Applicable")
                self.assertEqual(statewise["statename"], TALLY_ANY)
                self.assertEqual(statewise["statename"], "\x04 Any")
                self.assertEqual(rows["Cess"]["gstratevaluationtype"], TALLY_NOT_APPLICABLE)
                self.assertEqual(rows["Cess"]["gstratevaluationtype"], "\x04 Not Applicable")
                self.assertEqual({head: rates[head] for head in ("IGST", "CGST", "SGST/UTGST")}, expected_rates)
                # No plain, un-prefixed enum value may appear in these fields --
                # Tally's fixed-list fields require the exact "\x04 " marker.
                self.assertNotEqual(message["gstapplicable"], "Applicable")
                self.assertNotEqual(statewise["statename"], "Any")
                self.assertNotEqual(rows["Cess"]["gstratevaluationtype"], "Not Applicable")

    def test_query_back_verifies_rate_details_mode_before_taxable_master_is_ready(self):
        master = self._master("GST Purchase 18%", "Purchase Accounts", "18")
        actual = {"name": "GST Purchase 18%", "parent": "Purchase Accounts",
                  "gst_applicable": "Applicable", "taxability": "Taxable",
                  "supply_type": "Goods", "gst_rate": "18", "gst_rate_details": ""}

        verification = _verify_master_properties(master, actual)

        self.assertFalse(verification["valid"])
        self.assertIn("GST Rate Details expected 'Specify Details Here'", verification["reason"])

    def test_taxable_account_master_uses_financial_year_start_even_when_invoice_date_is_later(self):
        # A fixed historical date (e.g. GST's 1-Jul-2017 national rollout) is
        # *rejected* by live TallyPrime as not-yet-effective when it predates the
        # company's own books-from date -- Tally accepts and stores it (ALTERED=1,
        # no error) but still shows 0% in the ledger screen. The financial-year
        # start of the voucher date is always within the company's operative
        # range, so it's the only anchor that's both gap-free and accepted.
        ledger = self._xml_ledger("GST Purchase 5%", "Purchase Accounts", "5",
                                  applicable_from="2025-04-04")
        message = build_json_master(
            self._master("GST Sales 18%", "Sales Accounts", "18",
                         applicable_from="2025-04-04"), "Company D"
        )["tallymessage"][0]

        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/APPLICABLEFROM"), "20250401")
        self.assertEqual(message["gstdetails"][0]["applicablefrom"], "20250401")

    def test_ledger_details_fetches_nested_gst_rate_history_and_popup_rates(self):
        class Client:
            payload = b""

            def post(self, payload):
                self.payload = payload
                return b'''<ENVELOPE><BODY><DATA><TALLYMESSAGE>
                    <LEDGER NAME="GST Purchase 12%">
                        <PARENT>Purchase Accounts</PARENT>
                        <GSTAPPLICABLE>Applicable</GSTAPPLICABLE>
                        <RATEOFTAXCALCULATION>12</RATEOFTAXCALCULATION>
                        <GSTDETAILS.LIST>
                            <APPLICABLEFROM>20230401</APPLICABLEFROM>
                            <TAXABILITY>Taxable</TAXABILITY>
                            <SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>
                            <STATEWISEDETAILS.LIST>
                                <STATENAME>Any</STATENAME>
                                <RATEDETAILS.LIST>
                                    <GSTRATEDUTYHEAD>Integrated Tax</GSTRATEDUTYHEAD>
                                    <GSTRATE>12</GSTRATE>
                                    <GSTRATEPERUNIT>0</GSTRATEPERUNIT>
                                </RATEDETAILS.LIST>
                            </STATEWISEDETAILS.LIST>
                        </GSTDETAILS.LIST>
                    </LEDGER>
                </TALLYMESSAGE></DATA></BODY></ENVELOPE>'''

        client = Client()
        actual = ledger_details("GST Purchase 12%", "Company D", client)
        request = ET.fromstring(client.payload)
        fetch_fields = {node.text for node in request.findall(".//FETCH")}

        self.assertIn("GSTDetails.*", fetch_fields)
        self.assertIn("GSTDetails.StateWiseDetails.*", fetch_fields)
        self.assertIn("GSTDetails.StateWiseDetails.RateDetails.*", fetch_fields)
        self.assertTrue(actual["gst_rate_history_exists"])
        self.assertTrue(actual["gst_rate_details_popup_exists"])
        self.assertEqual(actual["gst_rate_details"], "Specify Details Here")
        self.assertEqual(actual["taxability"], "Taxable")
        self.assertEqual(actual["gst_rates"]["IGST"], "12")
        self.assertEqual(actual["outer_gst_rate"], "12")

    def test_query_back_requires_history_and_nested_rate_details_before_taxable_master_is_ready(self):
        master = self._master("GST Purchase 12%", "Purchase Accounts", "12")
        actual = {"name": "GST Purchase 12%", "parent": "Purchase Accounts",
                  "gst_applicable": "Applicable", "taxability": "Taxable",
                  "supply_type": "Goods", "gst_rate": "12",
                  "gst_rate_details": "Specify Details Here",
                  "gst_rate_history_exists": False,
                  "gst_rate_details_popup_exists": False}

        verification = _verify_master_properties(master, actual)

        # History row absence is a foundational, reliably-echoed signal and
        # stays blocking. Popup-exists alone is derived/ambiguous evidence
        # (see _verify_master_properties) and is diagnostic only now --
        # surfaced as a warning, not a blocking reason.
        self.assertFalse(verification["valid"])
        self.assertIn("GST Rate & Related Details history row is missing", verification["reason"])
        self.assertIn("GST Rate Details popup rate rows are missing", verification["warnings"])

        missing_igst = {**actual, "gst_rate_history_exists": True,
                        "gst_rate_details_popup_exists": True,
                        "gst_rates": {"CGST": "6", "SGST/UTGST": "6"}}

        verification = _verify_master_properties(master, missing_igst)

        self.assertFalse(verification["valid"])
        self.assertIn("nested IGST GST rate is missing", verification["reason"])


class CanonicalTallyTaxTypeTests(SimpleTestCase):
    def _xml_duty_head(self, value):
        master = {"master_type": "Tax", "name": f"Tax {value}", "group": "Duties & Taxes",
                  "tax_type": value, "gst_rate": "9"}
        return ET.fromstring(build_master(master)).findtext(".//GSTDUTYHEAD")

    def test_xml_uses_only_tally_dropdown_values(self):
        self.assertEqual(self._xml_duty_head("Central Tax"), "CGST")
        self.assertEqual(self._xml_duty_head("State Tax"), "SGST/UTGST")
        self.assertEqual(self._xml_duty_head("Integrated Tax"), "IGST")

    def test_json_uses_only_tally_dropdown_values(self):
        for source, expected in (("CGST", "CGST"), ("SGST", "SGST/UTGST"), ("IGST", "IGST")):
            master = {"master_type": "Tax", "name": source, "group": "Duties & Taxes",
                      "tax_type": source, "gst_rate": "9"}
            message = build_json_master(master, "Company D")["tallymessage"][0]
            self.assertEqual(message["gstdutyhead"], expected)
            self.assertNotIn(message["gstdutyhead"], {"Central Tax", "State Tax", "Integrated Tax"})
