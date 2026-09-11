from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from gst_tally.auth_service import secret_hash
from gst_tally.models import LicensedDevice, ProductLicense, UserProfile
from subscriptions.models import Subscription
from superadmin.models import CustomerProfile


User = get_user_model()


CUSTOMERS = [
    {
        "username": "arun",
        "email": "arun@example.com",
        "name": "Arun Kumar",
        "phone": "9876543210",
        "company": "SRI MAHALAKSHMI TRADERS",
        "gstin": "33AFHPM6103Q1Z8",
        "plan": "Professional",
        "purchase": "2026-09-07",
        "expiry": "2027-09-06",
        "key": "G2T-PRO-2026-0001",
        "serial": "735149529",
        "allowed_devices": 1,
        "status": ProductLicense.ACTIVE,
        "device": {"name": "OFFICE-PC-01", "fingerprint": "DEV-81F4A2C9", "app_version": "1.0.0"},
    },
    {
        "username": "priya",
        "email": "priya@example.com",
        "name": "Priya",
        "phone": "9876543211",
        "company": "ABC ENTERPRISES",
        "gstin": "33ABCDE1234F1Z5",
        "plan": "Standard",
        "purchase": "2025-09-10",
        "expiry": "2026-09-10",
        "key": "G2T-STD-2025-0002",
        "serial": "845621773",
        "allowed_devices": 1,
        "status": ProductLicense.EXPIRING,
    },
    {
        "username": "rajesh",
        "email": "rajesh@example.com",
        "name": "Rajesh",
        "phone": "9876543212",
        "company": "XYZ AGENCIES",
        "gstin": "33XYZDE5678G1Z2",
        "plan": "Professional",
        "purchase": "2025-09-01",
        "expiry": "2026-09-01",
        "key": "G2T-PRO-2025-0003",
        "serial": "992381425",
        "allowed_devices": 2,
        "status": ProductLicense.EXPIRED,
    },
]


class Command(BaseCommand):
    help = "Seed local ProductLicense rows for GSTR 2 Tally development."

    def handle(self, *args, **options):
        for row in CUSTOMERS:
            user, _ = User.objects.get_or_create(username=row["username"], defaults={"email": row["email"], "first_name": row["name"]})
            if user.email != row["email"]:
                user.email = row["email"]
                user.save(update_fields=["email"])
            if user.first_name != row["name"]:
                user.first_name = row["name"]
                user.save(update_fields=["first_name"])

            purchase_date = timezone.datetime.fromisoformat(row["purchase"]).date()
            expiry_date = timezone.datetime.fromisoformat(row["expiry"]).date()
            Subscription.objects.update_or_create(
                user=user,
                defaults={
                    "plan": row["plan"],
                    "purchase_date": purchase_date,
                    "is_activated": True,
                    "activation_date": purchase_date,
                    "expiry_date": expiry_date,
                    "subscription_status": Subscription.EXPIRED if row["status"] == ProductLicense.EXPIRED else Subscription.ACTIVE,
                },
            )
            CustomerProfile.objects.update_or_create(
                user=user,
                defaults={"business_name": row["company"], "contact_person": row["name"]},
            )
            UserProfile.objects.update_or_create(user=user, defaults={"phone_number": row["phone"]})
            license_obj, _ = ProductLicense.objects.update_or_create(
                activation_key_hash=secret_hash(row["key"]),
                defaults={
                    "customer": user,
                    "display_activation_key": row["key"],
                    "display_activation_key_suffix": row["key"][-4:],
                    "licensed_gstin": row["gstin"],
                    "licensed_tally_serial": row["serial"],
                    "plan": row["plan"],
                    "allowed_devices": row["allowed_devices"],
                    "status": row["status"],
                    "purchase_date": purchase_date,
                    "expiry_date": expiry_date,
                },
            )
            device = row.get("device")
            if device:
                LicensedDevice.objects.update_or_create(
                    license=license_obj,
                    device_fingerprint=device["fingerprint"],
                    defaults={
                        "device_name": device["name"],
                        "app_version": device["app_version"],
                        "status": LicensedDevice.ACTIVE,
                    },
                )
        self.stdout.write(self.style.SUCCESS("Seeded GSTR 2 Tally product licenses."))
