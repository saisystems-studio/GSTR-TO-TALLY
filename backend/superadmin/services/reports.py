import csv

from django.http import HttpResponse

from subscriptions.models import RenewalHistory, Subscription

from ..models import Payment, RegisteredCompany


def _csv_response(filename, fieldnames, rows):
    """CSV export via the stdlib `csv` module only (spec section 50) --
    Excel/PDF binaries would need `openpyxl`/`reportlab`, which aren't
    installed in this project; not added silently."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return response


def export_report(kind):
    if kind == "customers":
        fields = ["username", "email", "plan", "subscription_status", "expiry_date", "days_remaining"]
        rows = ({"username": s.user.username, "email": s.user.email, "plan": s.plan,
                 "subscription_status": s.subscription_status, "expiry_date": s.expiry_date,
                 "days_remaining": s.days_remaining()} for s in Subscription.objects.select_related("user").iterator())
        return _csv_response("customers.csv", fields, rows)

    if kind in ("active_subscriptions", "expired_subscriptions"):
        status = Subscription.ACTIVE if kind == "active_subscriptions" else Subscription.EXPIRED
        fields = ["username", "plan", "activation_date", "expiry_date"]
        rows = ({"username": s.user.username, "plan": s.plan, "activation_date": s.activation_date,
                 "expiry_date": s.expiry_date} for s in Subscription.objects.select_related("user").filter(subscription_status=status).iterator())
        return _csv_response(f"{kind}.csv", fields, rows)

    if kind == "renewals":
        fields = ["customer", "renewal_date", "previous_expiry_date", "new_expiry_date", "amount", "payment_status"]
        rows = ({"customer": r.subscription.user.username, "renewal_date": r.renewal_date,
                 "previous_expiry_date": r.previous_expiry_date, "new_expiry_date": r.new_expiry_date,
                 "amount": r.amount, "payment_status": r.payment_status}
                for r in RenewalHistory.objects.select_related("subscription__user").iterator())
        return _csv_response("renewals.csv", fields, rows)

    if kind == "payments":
        fields = ["customer", "invoice_number", "final_amount", "payment_date", "payment_status"]
        rows = ({"customer": p.user.username, "invoice_number": p.invoice_number, "final_amount": p.final_amount,
                 "payment_date": p.payment_date, "payment_status": p.payment_status}
                for p in Payment.objects.select_related("user").iterator())
        return _csv_response("payments.csv", fields, rows)

    if kind == "company_registration":
        fields = ["customer", "gstin", "company_name", "status", "first_activated"]
        rows = ({"customer": c.user.username, "gstin": c.gstin, "company_name": c.company_name,
                 "status": c.status, "first_activated": c.first_activated}
                for c in RegisteredCompany.objects.select_related("user").iterator())
        return _csv_response("company_registration.csv", fields, rows)

    raise ValueError(f"Unknown report kind: {kind}")
