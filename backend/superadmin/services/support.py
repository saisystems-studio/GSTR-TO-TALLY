from ..models import SupportTicket
from .audit import log_admin_action


def ticket_queryset(search="", status=None, priority=None):
    qs = SupportTicket.objects.select_related("user", "assigned_to")
    if search:
        qs = qs.filter(subject__icontains=search) | qs.filter(user__username__icontains=search)
    if status:
        qs = qs.filter(status=status)
    if priority:
        qs = qs.filter(priority=priority)
    return qs


def update_ticket(ticket, performed_by, **fields):
    old_value = {"status": ticket.status, "priority": ticket.priority, "assigned_to_id": ticket.assigned_to_id}
    for field, value in fields.items():
        setattr(ticket, field, value)
    ticket.save(update_fields=[*fields.keys(), "updated_at"])
    log_admin_action(performed_by, "SUPPORT_TICKET_UPDATED", "SupportTicket", ticket.id, customer=ticket.user,
                      old_value=old_value, new_value=fields)
    return ticket
