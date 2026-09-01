"""Read-only Tally request builders.

Every builder in this module produces an **export/query** envelope. Tally uses
``TALLYREQUEST`` to decide whether an envelope writes or reads, so read and write
envelopes are built by different modules on purpose:

* writes  -> ``master_builder``/``voucher_builder``/``company_builder`` (``Import Data``)
* reads   -> this module (``Export``/``Export Data``)

No builder here may emit ``Import Data``; :func:`assert_read_request` is the
guard that enforces it and is exercised by the regression suite.
"""
from datetime import date
from xml.etree import ElementTree as ET

# The two request verbs Tally accepts for reads. "Export Data" drives a named
# REPORTNAME (Day Book); "Export" + TYPE drives a Collection/Object query.
READ_REQUEST_REPORT = "Export Data"
READ_REQUEST_COLLECTION = "Export"
WRITE_REQUEST = "Import Data"

DAY_BOOK_REPORT = "Day Book"
VOUCHER_COLLECTION = "GSTVoucherQuery"
MASTER_COLLECTION = "GSTImportMasters"
COMPANY_COLLECTION = "GSTCompanies"

VOUCHER_FETCH = ("DATE", "VOUCHERNUMBER", "VOUCHERTYPENAME", "PARTYLEDGERNAME",
                 "REFERENCE", "MASTERID", "ALTERID", "GUID", "ISCANCELLED", "ISOPTIONAL",
                 "LEDGERENTRIES.LIST", "ALLLEDGERENTRIES.LIST",
                 "LEDGERENTRIES.LIST.*", "ALLLEDGERENTRIES.LIST.*",
                 "LEDGERENTRIES.LIST.RATEOFINVOICETAX", "LEDGERENTRIES.LIST.BASICRATEOFINVOICETAX",
                 "LEDGERENTRIES.LIST.GSTTAXRATE", "LEDGERENTRIES.LIST.GSTASSESSABLEVALUE",
                 "LEDGERENTRIES.LIST.GSTOVRDNASSESSABLEVALUE", "LEDGERENTRIES.LIST.GSTASSBLVALUE",
                 "LEDGERENTRIES.LIST.RATEDETAILS", "LEDGERENTRIES.LIST.RATEDETAILS.*",
                 "ALLLEDGERENTRIES.LIST.RATEOFINVOICETAX", "ALLLEDGERENTRIES.LIST.BASICRATEOFINVOICETAX",
                 "ALLLEDGERENTRIES.LIST.GSTTAXRATE", "ALLLEDGERENTRIES.LIST.GSTASSESSABLEVALUE",
                 "ALLLEDGERENTRIES.LIST.GSTOVRDNASSESSABLEVALUE", "ALLLEDGERENTRIES.LIST.GSTASSBLVALUE",
                 "ALLLEDGERENTRIES.LIST.RATEDETAILS", "ALLLEDGERENTRIES.LIST.RATEDETAILS.*")


def _escape(value):
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def tally_date(value):
    """Return Tally's ``YYYYMMDD`` static-variable date format."""
    if not value:
        return ""
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if text.count("-") == 2 and len(text) == 10:
        return date.fromisoformat(text).strftime("%Y%m%d")
    return text.replace("-", "").replace("/", "")


def assert_read_request(payload):
    """Raise if an envelope meant for a read carries a write request verb."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload or "")
    request = (ET.fromstring(text).findtext(".//TALLYREQUEST") or "").strip()
    if request.casefold().startswith("import"):
        raise ValueError(f"Read request builder produced a write envelope (TALLYREQUEST={request})")
    return request


def build_daybook_query_xml(company, from_date, to_date=None):
    """Standard Day Book report export. No TDL, no COLLECTION, no FETCH."""
    company = _escape(company)
    start = _escape(tally_date(from_date))
    end = _escape(tally_date(to_date or from_date))
    return (f'<ENVELOPE><HEADER><TALLYREQUEST>{READ_REQUEST_REPORT}</TALLYREQUEST></HEADER>'
            f'<BODY><EXPORTDATA><REQUESTDESC><REPORTNAME>{DAY_BOOK_REPORT}</REPORTNAME>'
            f'<STATICVARIABLES><SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>'
            f'<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>'
            f'<SVFROMDATE>{start}</SVFROMDATE><SVTODATE>{end}</SVTODATE>'
            f'</STATICVARIABLES></REQUESTDESC><REQUESTDATA></REQUESTDATA></EXPORTDATA></BODY></ENVELOPE>').encode()


def build_voucher_query_xml(company, from_date, to_date=None):
    """Voucher collection export that returns the identifiers used for matching."""
    company = _escape(company)
    start = _escape(tally_date(from_date))
    end = _escape(tally_date(to_date or from_date))
    fetch = ",".join(VOUCHER_FETCH)
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>{READ_REQUEST_COLLECTION}</TALLYREQUEST>'
            f'<TYPE>Collection</TYPE><ID>{VOUCHER_COLLECTION}</ID></HEADER>'
            f'<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>'
            f'<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>'
            f'<SVFROMDATE>{start}</SVFROMDATE><SVTODATE>{end}</SVTODATE></STATICVARIABLES>'
            f'<TDL><TDLMESSAGE><COLLECTION NAME="{VOUCHER_COLLECTION}"><TYPE>Voucher</TYPE>'
            f'<FETCH>{fetch}</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>').encode()


def build_ledger_query_xml():
    """Master (ledger/stock item/unit) collection export."""
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>{READ_REQUEST_COLLECTION}</TALLYREQUEST>'
            f'<TYPE>Collection</TYPE><ID>{MASTER_COLLECTION}</ID></HEADER>'
            f'<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>'
            f'<TDL><TDLMESSAGE><COLLECTION NAME="{MASTER_COLLECTION}"><TYPE>Ledger,Stock Item,Unit</TYPE>'
            f'<FETCH>NAME,GSTREGISTRATIONNO,PARENT,BASEUNITS</FETCH></COLLECTION></TDLMESSAGE></TDL>'
            f'</DESC></BODY></ENVELOPE>').encode()


def build_company_query_xml():
    """Open-company collection export."""
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>{READ_REQUEST_COLLECTION}</TALLYREQUEST>'
            f'<TYPE>Collection</TYPE><ID>{COMPANY_COLLECTION}</ID></HEADER>'
            f'<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>'
            f'<TDL><TDLMESSAGE><COLLECTION NAME="{COMPANY_COLLECTION}"><TYPE>Company</TYPE>'
            f'<COMPUTE>FINANCIALYEARFROM:$$String:##SVFromDate</COMPUTE>'
            f'<COMPUTE>FINANCIALYEARTO:$$String:##SVToDate</COMPUTE>'
            f'<FETCH>NAME,STATENAME,GSTREGISTRATIONNUMBER,FINANCIALYEARFROM,FINANCIALYEARTO</FETCH></COLLECTION></TDLMESSAGE></TDL>'
            f'</DESC></BODY></ENVELOPE>').encode()


def build_license_query_xml(param="SerialNumber"):
    """Tally software-license identity export.

    Tally exposes license metadata through the ``$$LicenseInfo`` platform
    function. The returned value is read from ``BODY/DATA/RESULT`` by
    ``read_tally_license``; no collection field or custom result tag is expected.
    """
    return (f'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>'
            f'<TYPE>FUNCTION</TYPE><ID>$$LicenseInfo</ID></HEADER><BODY><DESC>'
            f'<FUNCPARAMLIST><PARAM>{param}</PARAM></FUNCPARAMLIST>'
            f'</DESC></BODY></ENVELOPE>').encode()
