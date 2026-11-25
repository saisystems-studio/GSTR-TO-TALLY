from django.conf import settings
from django.db import models


class NexusLink(models.Model):
    """Server-side record of a Nexus-verified identity. The Nexus session token
    is stored encrypted and never sent to the browser. A local JWT is issued only
    after Nexus verified the credentials AND reported the license authorised."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="nexus_links", on_delete=models.CASCADE)
    nexus_user_id = models.CharField(max_length=64)  # = Tally serial
    token_enc = models.TextField(blank=True)
    client_device_id = models.CharField(max_length=64, blank=True)
    license = models.JSONField(default=dict)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=64, blank=True)  # non-empty => local JWTs rejected
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["nexus_user_id", "client_device_id"], name="nexus_link_serial_device_uniq")]


class ConnectorDiscovery(models.Model):
    """First-time (pre-registration) Tally identity reported by the local connector for an
    enrollment that a browser has paired with. Values are exactly what Tally returned."""
    enrollment = models.OneToOneField("gst_tally.ConnectorEnrollment", related_name="discovery", on_delete=models.CASCADE)
    serial = models.CharField(max_length=64, blank=True)
    tally_reachable = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict)
    last_seen_at = models.DateTimeField(null=True, blank=True)
