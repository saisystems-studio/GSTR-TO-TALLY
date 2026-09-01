import csv, io
from gst_tally.utils.file_utils import first_value, parse_date, parse_decimal, json_safe
from gst_tally.utils.uploaded_files import decode_csv_upload
from .source_party import add_source_party, log_source_parties

ALIASES = {
 "gstin": ["GSTIN of supplier", "GSTIN", "ctin"], "party_name": ["Trade/Legal name", "Supplier Name", "Party Name"],
 "invoice_number": ["Invoice number", "Invoice No", "inum"], "invoice_date": ["Invoice Date", "idt"],
 "taxable_value": ["Taxable Value", "txval"], "cgst": ["Central Tax", "CGST", "camt"],
 "sgst": ["State/UT Tax", "SGST", "samt"], "igst": ["Integrated Tax", "IGST", "iamt"],
 "cess": ["Cess", "csamt"], "total_value": ["Invoice Value", "Total Value", "val"],
 "tax_percent": ["Rate", "Tax %", "rt"], "state_code": ["Place Of Supply", "State Code", "pos"],
 "reverse_charge": ["Reverse Charge", "rchrg"], "invoice_type": ["Invoice Type", "inv_typ"],
 "filing_period": ["Filing Period", "Return Period"], "filing_date": ["Filing Date"],
}
COMPANY_ALIASES = ["GSTIN of recipient", "Recipient GSTIN", "Company GSTIN", "My GSTIN", "Taxpayer GSTIN"]
def parse(file_obj):
    text = decode_csv_upload(file_obj)
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV does not contain a header row")
        rows, parties, company_candidates, company_source = [], {}, [], {}
        for source_row_number, source in enumerate(reader, 2):
            if not any(str(v).strip() for v in source.values() if v is not None): continue
            row = {k: first_value(source, v) for k, v in ALIASES.items()}
            company_value = first_value(source, COMPANY_ALIASES)
            if company_value: company_candidates.append(company_value); company_source = source
            add_source_party(parties, row["gstin"], source)
            rows.append({"customer_gstin": str(row["gstin"] or "").strip().upper(),
                "invoice_no": str(row["invoice_number"] or "").strip(), "invoice_date": parse_date(row["invoice_date"]),
                "taxable_value": parse_decimal(row["taxable_value"]), "tax_percent": parse_decimal(row["tax_percent"]),
                "cgst": parse_decimal(row["cgst"]), "sgst": parse_decimal(row["sgst"]), "igst": parse_decimal(row["igst"]),
                "invoice_value": parse_decimal(row["total_value"]), "state_code": str(row["state_code"] or "").strip(),
                "reverse_charge": str(row["reverse_charge"] or "").strip(), "invoice_type": str(row["invoice_type"] or "").strip(),
                "filing_period": str(row["filing_period"] or "").strip(), "filing_type": "GSTR2A",
                "filing_date": parse_date(row["filing_date"]), "source_type": "B2B",
                "source_line": json_safe({"source_row_number": source_row_number, **source})})
        log_source_parties(parties)
        from .company import source_company_metadata
        return rows, {"parties": parties, **source_company_metadata(company_candidates, company_source)}
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV file: {exc}") from exc
