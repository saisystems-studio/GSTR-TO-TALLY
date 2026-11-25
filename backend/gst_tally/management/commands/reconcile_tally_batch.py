"""Read-only-by-default reconciliation of a batch's vouchers against what Tally actually holds.

For each voucher recorded as Pending Verification / Unknown (or any mapping you pass with --all),
the company the write was addressed to (stored on the mapping) is read back. A voucher is confirmed
only when exactly ONE Tally Purchase/Sales voucher carries the same reference (invoice number) and
voucher date. Nothing is ever sent to Tally; unmatched vouchers keep their status. --apply updates
database status only, preserving the original error text in the audit note.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from gst_tally.models import GSTInvoice, TallyVoucherMapping
from gst_tally.tally.client import TallyClient
from gst_tally.tally.voucher_verification import query_vouchers


class Command(BaseCommand):
    help = "Reconcile a batch's Pending Verification vouchers against Tally (read-only unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, required=True)
        parser.add_argument("--apply", action="store_true", help="Write confirmed statuses to the database.")
        parser.add_argument("--all", action="store_true", help="Also re-check rows already marked Imported.")

    def handle(self, *args, batch, apply, all, **options):
        statuses = ["Pending Verification", "Unknown"] + (["Imported"] if all else [])
        rows = list(TallyVoucherMapping.objects.filter(batch_id=batch, import_status__in=statuses))
        by_company = defaultdict(list)
        for row in rows:
            by_company[row.tally_company].append(row)
        confirmed = unmatched = ambiguous = 0
        for company, mappings in by_company.items():
            invoices = {i.id: i for i in GSTInvoice.objects.filter(pk__in=[m.invoice_id for m in mappings])}
            dates = [invoices[m.invoice_id].voucher_date or invoices[m.invoice_id].invoice_date
                     for m in mappings if m.invoice_id in invoices]
            dates = [d for d in dates if d]
            if not dates:
                continue
            result = query_vouchers(TallyClient(), company, min(dates), max(dates), operation="reconcile_batch")
            if not result.get("query_valid"):
                self.stderr.write(f"{company}: read-back invalid ({result.get('reason')}); nothing changed.")
                continue
            index = defaultdict(list)
            for v in result["vouchers"]:
                index[(v["reference"].strip().casefold(), v["date"])].append(v)
            for m in mappings:
                invoice = invoices.get(m.invoice_id)
                if not invoice:
                    unmatched += 1
                    continue
                vdate = (invoice.voucher_date or invoice.invoice_date).strftime("%Y%m%d")
                hits = [v for v in index.get((m.source_invoice_number.strip().casefold(), vdate), [])
                        if v["voucher_type"] in ("Purchase", "Sales", "Debit Note", "Credit Note")]
                if len(hits) == 1:
                    confirmed += 1
                    if apply:
                        note = f"Reconciled {timezone.now():%Y-%m-%d %H:%M} against Tally company '{company}'. Previous: {m.import_status} {m.error_message}"[:1000]
                        TallyVoucherMapping.objects.filter(pk=m.pk).update(
                            import_status="Imported", tally_voucher_identifier=hits[0].get("master_id") or m.tally_voucher_identifier,
                            error_message=note, imported_at=m.imported_at or timezone.now())
                        from gst_tally.models import GSTTallyVoucherRegistry
                        GSTTallyVoucherRegistry.objects.filter(latest_invoice_id=m.invoice_id, batch_id=batch).update(
                            import_status="IMPORTED", tally_created=True, last_error_code="", last_error_message="",
                            tally_voucher_identifier=hits[0].get("master_id") or "", imported_at=timezone.now())
                elif len(hits) > 1:
                    ambiguous += 1
                else:
                    unmatched += 1
        self.stdout.write(f"batch {batch}: checked={len(rows)} confirmed_in_tally={confirmed} ambiguous={ambiguous} "
                          f"not_found={unmatched} applied={'yes' if apply else 'no (dry run)'}")
