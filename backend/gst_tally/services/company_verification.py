"""Verify the open Tally company by GSTIN and persist its identity.

Company names are display information; license/device checks remain independent.
"""
import logging

from django.utils import timezone

from ..models import CompanyDetails
from ..tally.license_reader import read_tally_license
from ..tally.odbc import normalize_company_name, odbc_company_status
from .party_lookup import normalize_gstin

logger = logging.getLogger(__name__)


def _save_company_details(details):
    canonical_name = normalize_company_name(details["company_name"])
    record, _ = CompanyDetails.objects.update_or_create(
        company_name=canonical_name,
        defaults={**details, "company_name": canonical_name},
    )
    return record


def verify_and_store_company(entered_company_name, expected_gstin="", client=None, batch=None):
    entered_company_name = str(entered_company_name or "").strip()
    source_file_gstin = normalize_gstin(expected_gstin)
    connection = odbc_company_status(requested_company=entered_company_name, expected_gstin=source_file_gstin)
    tally_connected = bool(connection.get("read_connected"))

    if not tally_connected:
        return {"company_verified": False, "tally_connected": False, "company_details_saved": False,
                "ready_for_master_preparation": False,
                "source_file_gstin": source_file_gstin,
                "message": connection.get("message") or "Tally Connection Failed."}

    detected_company_name = str(connection.get("company_name") or connection.get("company") or "").strip()
    current_tally_company_gstin = normalize_gstin(connection.get("company_gstin") or connection.get("gstin"))

    company_source = ""
    if batch is not None:
        source_file_gstin = normalize_gstin(batch.company_gstin)
        company_source = (batch.company_details or {}).get("company_source", "")

    # GSTIN is the primary company identity (the uploaded GSTR return's own
    # taxpayer GSTIN vs the GSTIN of the company currently open in Tally) --
    # the company name is informational only and must never block
    # verification by itself (punctuation/formatting differences are
    # cosmetic and irrelevant once the GSTIN matches).
    company_verified = bool(
        source_file_gstin and current_tally_company_gstin
        and source_file_gstin == current_tally_company_gstin
    )
    company_fields = {
        "source_file_gstin": source_file_gstin,
        "current_tally_company_name": detected_company_name,
        "current_tally_company_gstin": current_tally_company_gstin,
        "company_source": company_source,
    }
    if not company_verified:
        if not source_file_gstin:
            # No comparison was actually possible -- a source-file extraction
            # gap, never a real GSTIN mismatch. Reporting a mismatch here
            # would misleadingly imply two GSTINs were read and disagreed.
            code, message = "SOURCE_COMPANY_GSTIN_MISSING", "Unable to identify the company GSTIN from the uploaded return."
        else:
            code = "COMPANY_GSTIN_MISMATCH"
            message = (f"Company Verification Failed. The GSTIN of the company currently open in Tally "
                       f"({current_tally_company_gstin or '-'}) does not match the uploaded return's "
                       f"company GSTIN ({source_file_gstin}).")
        return {"company_verified": False, "tally_connected": True, "company_details_saved": False,
                "ready_for_master_preparation": False,
                "verification_result": code, "company_error": code,
                **company_fields,
                "message": message}

    license_reading = read_tally_license(client)
    details = {
        "company_name": detected_company_name,
        "gstin": current_tally_company_gstin,
        "state": connection.get("company_state") or connection.get("state") or "",
        "financial_year": connection.get("financial_year", ""),
        "financial_year_from": connection.get("financial_year_from", ""),
        "financial_year_to": connection.get("financial_year_to", ""),
        "tally_serial_number": license_reading.get("serial_number", ""),
        "tally_edition": license_reading.get("edition", ""),
        "tss_status": license_reading.get("tally_software_services", ""),
        "license_administrator": license_reading.get("license_administrator", ""),
        "tally_connected": True,
        "company_verified": True,
        "verified_at": timezone.now(),
    }

    try:
        _save_company_details(details)
    except Exception:
        logger.exception("Failed to save verified company details to companydetails_tbl")
        return {"company_verified": True, "tally_connected": True, "company_details_saved": False,
                "ready_for_master_preparation": False,
                "verification_result": "COMPANY_VERIFIED",
                **company_fields,
                "message": "Unable to save verified company information."}

    return {"company_verified": True, "tally_connected": True, "company_details_saved": True,
            "ready_for_master_preparation": True,
            "verification_result": "COMPANY_VERIFIED",
            **company_fields,
            "message": "Company Verified. Tally Connected."}
