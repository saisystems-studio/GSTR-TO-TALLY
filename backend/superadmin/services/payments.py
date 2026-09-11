from ..models import Payment
from .audit import log_admin_action


def payment_queryset(search="", status=None):
    qs = Payment.objects.select_related("user", "plan")
    if search:
        qs = qs.filter(invoice_number__icontains=search) | qs.filter(user__username__icontains=search) | qs.filter(transaction_id__icontains=search)
    if status:
        qs = qs.filter(payment_status=status)
    return qs


def update_payment_status(payment, new_status, performed_by, reason=""):
    old_status = payment.payment_status
    payment.payment_status = new_status
    payment.save(update_fields=["payment_status", "updated_at"])
    log_admin_action(performed_by, "PAYMENT_STATUS_CHANGED", "Payment", payment.id, customer=payment.user,
                      old_value={"payment_status": old_status}, new_value={"payment_status": new_status}, reason=reason)
    return payment
