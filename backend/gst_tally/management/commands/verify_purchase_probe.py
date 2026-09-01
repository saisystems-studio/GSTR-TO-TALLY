from django.core.management.base import BaseCommand, CommandError

from gst_tally.models import GSTImportBatch
from gst_tally.tally.client import TallyClient
from gst_tally.tally.connection import ledger_details
from gst_tally.tally.master_builder import masters_for
from gst_tally.tally.odbc import odbc_company_status, verify_tally_company
from gst_tally.tally.service import (_required_ledgers, _response_details, _verify_master_properties,
                                     _voucher_balance, _write_master, _write_voucher, prepare)
from gst_tally.tally.voucher_builder import build_voucher
from gst_tally.tally.voucher_verification import verify_voucher


class Command(BaseCommand):
    help = "Repair/query invoice #1 Purchase masters, import only that probe, and query it back."

    def add_arguments(self, parser):
        parser.add_argument("batch_id", type=int)
        parser.add_argument("--invoice", default="1")

    def handle(self, *args, **options):
        batch = GSTImportBatch.objects.get(pk=options["batch_id"])
        target = str((batch.company_details or {}).get("selected_tally_company") or "").strip()
        client = TallyClient()
        company_status = odbc_company_status(requested_company=target, expected_gstin=batch.company_gstin, read_client=client)
        company_check = verify_tally_company(batch.company_gstin, company_status, batch.company_details)
        if not company_check["company_verified"]:
            raise CommandError(f"Tally Company Mismatch: {company_check['message']}")
        _, vouchers, _ = prepare(batch, company_status.get("company_state"))
        voucher = next((row for row in vouchers if row["invoice_number"] == options["invoice"] and row.get("voucher_type") == "Purchase"), None)
        if not voucher: raise CommandError("Purchase probe invoice not found")
        if not str(voucher.get("status", "")).startswith("Ready"): raise CommandError(f"Probe is not eligible: {voucher.get('reason')}")
        balance = _voucher_balance(voucher)
        if not balance["balanced"]: raise CommandError(f"Voucher debit/credit mismatch: {balance}")
        required = set(_required_ledgers(voucher))
        probe_masters = [master for master in masters_for([voucher]) if master["name"] in required]
        diagnostics = []
        for master in probe_masters:
            actual = ledger_details(master["name"], target, client)
            verification = _verify_master_properties(master, actual) if actual.get("exists") else {"valid": False, "reason": "missing"}
            action, response_data = "reused", None
            if not verification["valid"]:
                action = "repaired" if actual.get("exists") else "created"
                response = _write_master(client, {**master, "action": "Alter" if actual.get("exists") else "Create"}, target)
                response_data = _response_details(response, client.last_http_status)
                actual = ledger_details(master["name"], target, client)
                verification = _verify_master_properties(master, actual)
                if not response.accepted or not verification["valid"]:
                    diagnostics.append({"name": master["name"], "action": action, "verified": False,
                                        "verification": verification, "tally_response": response_data})
                    self.stdout.write(self.style.ERROR(str({"company": company_status, "masters": diagnostics})))
                    raise CommandError(f"Master verification failed: {master['name']}: {verification['reason']}")
            diagnostics.append({"name": master["name"], "action": action, "verified": True,
                                "actual": actual, "tally_response": response_data})
        request_payload = build_voucher(voucher, target)
        response = client.import_data(request_payload)
        query = verify_voucher(client, target, voucher) if response.accepted else {"found": False, "reason": "write rejected"}
        result = {"invoice_no": voucher["invoice_number"], "company": {"name": company_status["company_name"], "gstin": company_status["company_gstin"]},
                  "masters": diagnostics, "balance": balance, "request_payload": request_payload.decode("utf-8", "replace"),
                  "tally_response": _response_details(response, client.last_http_status), "query_back": query,
                  "accounting_invoice": {"voucher_type": voucher["voucher_type"], "persisted_view": "Invoice Voucher View", "is_invoice": True},
                  "success": bool(response.accepted and query.get("found"))}
        self.stdout.write(str(result))
        if not result["success"]: raise CommandError("Purchase probe was not created and query-back verified")
