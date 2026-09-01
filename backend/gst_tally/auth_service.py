import hashlib
import hmac
import secrets

from django.conf import settings
from django.utils import timezone

from .models import DeviceActivation, License


def secret_hash(value):
    return hmac.new(settings.SECRET_KEY.encode(), value.strip().encode(), hashlib.sha256).hexdigest()


def validate_license(serial_number, email, raw_key, user=None, device_id=None):
    try:
        license_obj = License.objects.select_for_update().get(serial_number__iexact=serial_number.strip())
    except License.DoesNotExist as exc:
        raise ValueError("This serial number does not exist.") from exc
    if license_obj.status == License.Status.EXPIRED or (license_obj.expiry_date and license_obj.expiry_date < timezone.localdate()):
        raise ValueError("License has expired.")
    if license_obj.status == License.Status.REVOKED:
        raise ValueError("License has been revoked.")
    if license_obj.status != License.Status.ACTIVE:
        raise ValueError("License is inactive.")
    if license_obj.registered_email.casefold() != email.strip().casefold():
        raise ValueError("Invalid serial number, email, or activation key.")
    if not hmac.compare_digest(license_obj.activation_key_hash, secret_hash(raw_key)):
        raise ValueError("Activation key is invalid.")
    if license_obj.activated_user_id and (not user or license_obj.activated_user_id != user.id):
        raise ValueError("This license is already assigned.")
    active_devices = license_obj.device_activations.filter(is_active=True)
    if user and device_id:
        active_devices = active_devices.exclude(user=user, device_id=device_id)
    if active_devices.count() >= license_obj.max_devices:
        raise ValueError("Maximum device activation limit reached.")
    return license_obj


def activate_device(user, license_obj, device_id, device_name=""):
    raw_token = secrets.token_urlsafe(48)
    activation, _ = DeviceActivation.objects.update_or_create(
        user=user, device_id=device_id,
        defaults={"license": license_obj, "trusted_token_hash": secret_hash(raw_token), "device_name": device_name[:255], "is_active": True},
    )
    return activation, raw_token


def verify_device(user, device_id, trusted_token):
    activation = DeviceActivation.objects.filter(user=user, device_id=device_id, is_active=True).select_related("license").first()
    if not activation or not hmac.compare_digest(activation.trusted_token_hash, secret_hash(trusted_token or "")):
        return None
    license_obj = activation.license
    if license_obj.status != License.Status.ACTIVE or (license_obj.expiry_date and license_obj.expiry_date < timezone.localdate()):
        return None
    activation.save(update_fields=["last_used_at"])
    return activation
