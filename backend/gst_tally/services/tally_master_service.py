import logging
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from django.conf import settings

logger = logging.getLogger(__name__)


def _endpoint(): return settings.TALLY_BASE_URL.rstrip("/")

def _post(payload):
    request = Request(_endpoint(), data=payload, headers={"Content-Type": "application/xml"}, method="POST")
    with urlopen(request, timeout=8) as response: return ElementTree.fromstring(response.read())

def existing_ledgers():
    if settings.TALLY_MOCK: return False, {}, set()
    xml = b'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>GSTLedgers</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE><COLLECTION NAME="GSTLedgers"><TYPE>Ledger</TYPE><FETCH>NAME,GSTREGISTRATIONNO</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>'
    try: root = _post(xml)
    except (URLError, TimeoutError, ElementTree.ParseError, OSError) as exc:
        logger.warning("Tally ledger check unavailable: %s", exc); return False, {}, set()
    by_gstin, names = {}, set()
    for ledger in root.findall(".//LEDGER"):
        name = (ledger.get("NAME") or ledger.findtext("NAME") or "").strip()
        gstin = (ledger.findtext(".//GSTREGISTRATIONNO") or "").strip().upper()
        if name: names.add(name.casefold())
        if name and gstin: by_gstin[gstin] = name
    return True, by_gstin, names

def create_ledger(master):
    envelope = ElementTree.Element("ENVELOPE")
    header = ElementTree.SubElement(envelope, "HEADER"); ElementTree.SubElement(header, "TALLYREQUEST").text = "Import Data"
    body = ElementTree.SubElement(envelope, "BODY"); data = ElementTree.SubElement(body, "IMPORTDATA")
    desc = ElementTree.SubElement(data, "REQUESTDESC"); ElementTree.SubElement(desc, "REPORTNAME").text = "All Masters"
    request_data = ElementTree.SubElement(data, "REQUESTDATA"); message = ElementTree.SubElement(request_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    ledger = ElementTree.SubElement(message, "LEDGER", {"NAME": master["ledger_name"], "ACTION": "Create"})
    ElementTree.SubElement(ledger, "NAME").text = master["ledger_name"]
    ElementTree.SubElement(ledger, "PARENT").text = master["group"]
    if master.get("gstin"):
        ElementTree.SubElement(ledger, "GSTREGISTRATIONNO").text = master["gstin"]
        if master.get("address"):
            address_list = ElementTree.SubElement(ledger, "ADDRESS.LIST", {"TYPE": "String"})
            ElementTree.SubElement(address_list, "ADDRESS").text = master["address"]
    if master["master_type"] == "Tax":
        ElementTree.SubElement(ledger, "TAXTYPE").text = "GST"
        duty_heads = {"CGST": "Central Tax", "SGST": "State Tax", "IGST": "Integrated Tax"}
        ElementTree.SubElement(ledger, "GSTDUTYHEAD").text = duty_heads[master["ledger_name"]]
    try: response = _post(ElementTree.tostring(envelope, encoding="utf-8"))
    except (URLError, TimeoutError, ElementTree.ParseError, OSError) as exc:
        return "Failed", str(exc)
    created = int(response.findtext(".//CREATED") or 0)
    errors = int(response.findtext(".//ERRORS") or 0)
    return ("Created", "Ledger created") if created and not errors else ("Failed", response.findtext(".//LINEERROR") or "Tally did not create the ledger")

def prepare_masters(masters):
    available, by_gstin, names = existing_ledgers()
    if not available: return False, [{**master, "status": "Pending", "message": "Tally connection unavailable"} for master in masters]
    results = []
    for master in masters:
        exists = (master.get("gstin") and master["gstin"] in by_gstin) or master["ledger_name"].casefold() in names
        if exists: results.append({**master, "status": "Existing", "message": "Reusing existing Tally ledger"}); continue
        status, message = create_ledger(master)
        results.append({**master, "status": status, "message": message})
        if status == "Created": names.add(master["ledger_name"].casefold())
    return True, results
