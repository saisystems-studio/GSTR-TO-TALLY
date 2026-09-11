import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import DeviceActivation, License, PasswordResetCode


def secret_hash(value):
    return hmac.new(settings.SECRET_KEY.encode(), value.strip().encode(), hashlib.sha256).hexdigest()


PASSWORD_RESET_CODE_TTL_MINUTES = 10
PASSWORD_RESET_MAX_ATTEMPTS = 5


def create_password_reset_code(user):
    """Issues a fresh 6-digit code, invalidating any earlier unused ones for
    this user so only the most recently requested code can ever succeed."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    PasswordResetCode.objects.create(
        user=user,
        code_hash=secret_hash(code),
        expires_at=timezone.now() + timedelta(minutes=PASSWORD_RESET_CODE_TTL_MINUTES),
    )
    return code


def send_password_reset_email(user, code):
    send_mail(
        subject="Your GSTR 2 Tally password reset code",
        message=(
            f"Your verification code is {code}.\n\n"
            f"This code expires in {PASSWORD_RESET_CODE_TTL_MINUTES} minutes. "
            "If you did not request a password reset, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def verify_and_consume_reset_code(user, raw_code):
    """Checks the most recent unused code for this user, capping repeated
    wrong guesses (PASSWORD_RESET_MAX_ATTEMPTS) so it can't be brute-forced,
    and marks it used on success so it can never be replayed."""
    raw_code = str(raw_code or "").strip()
    if not raw_code:
        return False
    reset_code = PasswordResetCode.objects.filter(user=user, used_at__isnull=True).order_by("-created_at").first()
    if not reset_code or reset_code.expires_at < timezone.now() or reset_code.attempts >= PASSWORD_RESET_MAX_ATTEMPTS:
        return False
    if not hmac.compare_digest(reset_code.code_hash, secret_hash(raw_code)):
        reset_code.attempts += 1
        reset_code.save(update_fields=["attempts"])
        return False
    reset_code.used_at = timezone.now()
    reset_code.save(update_fields=["used_at"])
    return True


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
