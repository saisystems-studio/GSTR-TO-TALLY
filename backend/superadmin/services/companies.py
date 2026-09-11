from gst_tally.models import CompanyDetails, DeviceActivation, TallyCompanyMapping

from ..models import RegisteredCompany
from .audit import log_admin_action


def company_queryset(search=""):
    qs = RegisteredCompany.objects.select_related("user")
    if search:
        qs = qs.filter(gstin__icontains=search) | qs.filter(company_name__icontains=search) | qs.filter(user__username__icontains=search)
    return qs


def enrich_with_tally_details(registered_company):
    """Read-only join by GSTIN into the existing `gst_tally` tables -- no FK,
    no write, no risk to those models (spec section 53)."""
    details = CompanyDetails.objects.filter(gstin=registered_company.gstin).first()
    mapping = TallyCompanyMapping.objects.filter(gstin=registered_company.gstin).first()
    return {
        "registered_company": registered_company,
        "company_details": details,
        "tally_mapping": mapping,
    }


def register_company(user, gstin, company_name="", performed_by=None):
    company, created = RegisteredCompany.objects.get_or_create(
        user=user, gstin=gstin, defaults={"company_name": company_name},
    )
    if created:
        log_admin_action(performed_by, "COMPANY_REGISTERED", "RegisteredCompany", company.id, customer=user,
                          new_value={"gstin": gstin, "company_name": company_name})
    return company, created


def set_company_status(registered_company, new_status, performed_by, reason=""):
    old_status = registered_company.status
    registered_company.status = new_status
    registered_company.save(update_fields=["status", "updated_at"])
    log_admin_action(performed_by, "COMPANY_STATUS_CHANGED", "RegisteredCompany", registered_company.id,
                      customer=registered_company.user, old_value={"status": old_status},
                      new_value={"status": new_status}, reason=reason)
    return registered_company


def device_queryset(user):
    return DeviceActivation.objects.filter(user=user).select_related("license")


def reset_device(device, performed_by, reason=""):
    """Deactivates one device activation, freeing its slot (spec section 27)
    -- flips the existing `is_active` flag `DeviceActivation` already has for
    exactly this purpose; no new device-tracking model needed."""
    device.is_active = False
    device.save(update_fields=["is_active", "last_used_at"])
    log_admin_action(performed_by, "DEVICE_RESET", "DeviceActivation", device.id, customer=device.user, reason=reason)
    return device
