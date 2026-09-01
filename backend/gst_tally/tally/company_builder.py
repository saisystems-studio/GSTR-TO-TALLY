from datetime import date
from xml.etree import ElementTree as ET

def _tally_date(value): return date.fromisoformat(value).strftime("%Y%m%d") if value else ""

def build_company(company):
    root = ET.Element("ENVELOPE"); header = ET.SubElement(root, "HEADER"); ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
    data = ET.SubElement(ET.SubElement(root, "BODY"), "IMPORTDATA"); desc = ET.SubElement(data, "REQUESTDESC"); ET.SubElement(desc, "REPORTNAME").text = "All Masters"
    message = ET.SubElement(ET.SubElement(data, "REQUESTDATA"), "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    node = ET.SubElement(message, "COMPANY", {"NAME": company["company_name"], "ACTION": "Create"})
    for tag, value in (("NAME", company["company_name"]), ("MAILINGNAME", company["company_name"]),
                       ("STATENAME", company.get("state")), ("COUNTRYNAME", company.get("country")),
                       ("PINCODE", company.get("pincode")), ("PHONENUMBER", company.get("mobile")),
                       ("EMAIL", company.get("email")), ("GSTREGISTRATIONNUMBER", company.get("gstin")),
                       ("STARTINGFROM", _tally_date(company.get("financial_year_beginning"))),
                       ("BOOKSFROM", _tally_date(company.get("books_beginning")))):
        if value: ET.SubElement(node, tag).text = str(value)
    if company.get("address"):
        values = ET.SubElement(node, "ADDRESS.LIST", {"TYPE": "String"}); ET.SubElement(values, "ADDRESS").text = company["address"]
    return ET.tostring(root, encoding="utf-8")
