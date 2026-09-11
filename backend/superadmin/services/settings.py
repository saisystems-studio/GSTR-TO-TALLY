from ..models import SuperAdminSettings


def get_settings():
    settings_row, _ = SuperAdminSettings.objects.get_or_create(pk=1)
    return settings_row


def update_settings(**fields):
    settings_row = get_settings()
    for field, value in fields.items():
        setattr(settings_row, field, value)
    settings_row.save()
    return settings_row
