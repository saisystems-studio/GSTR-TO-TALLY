from ..models import AdminAuditLog


def log_admin_action(admin_user, action, entity_type, entity_id="", customer=None,
                      old_value=None, new_value=None, reason="", ip_address=""):
    """Single write path for `AdminAuditLog` -- every mutating Super Admin
    view calls this so the audit trail (spec sections 45/46) can never be
    accidentally skipped for one action type but not another."""
    return AdminAuditLog.objects.create(
        admin_user=admin_user, action=action, entity_type=entity_type, entity_id=str(entity_id),
        customer=customer, old_value=old_value, new_value=new_value, reason=reason or "", ip_address=ip_address or "",
    )


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
