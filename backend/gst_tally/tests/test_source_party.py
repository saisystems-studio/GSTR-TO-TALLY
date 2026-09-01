from django.test import SimpleTestCase

from gst_tally.services.source_party import extract_source_party


class SourcePartyExtractionTests(SimpleTestCase):
    def test_nested_einvoice_aliases_are_combined(self):
        result = extract_source_party({"SellerDtls": {
            "TrdNm": "ABC TRADERS", "Addr1": "12 Main Road", "Addr2": "Anna Nagar",
            "Loc": "Chennai", "Stcd": "Tamil Nadu", "Pin": 600040,
        }})
        self.assertEqual(result["trade_name"], "ABC TRADERS")
        self.assertEqual(result["principal_place_of_business"],
                         "12 Main Road, Anna Nagar, Chennai, Tamil Nadu - 600040")
        self.assertEqual(result["trade_name_source"], "SellerDtls.TrdNm")

    def test_csv_supplier_aliases_are_combined(self):
        result = extract_source_party({
            "Supplier_Trade_Name": "XYZ STORES", "Supplier_Address1": "45 Market Street",
            "Supplier_Address2": "North Block", "city": "Coimbatore", "pincode": "641001",
        })
        self.assertEqual(result["trade_name"], "XYZ STORES")
        self.assertEqual(result["principal_place_of_business"],
                         "45 Market Street, North Block, Coimbatore - 641001")

