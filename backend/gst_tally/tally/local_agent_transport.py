"""TallyClient-compatible relay through a customer's outbound local agent."""
import hashlib
import json
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .client import TallyConnectionError
from .json_response_parser import parse_json_response
from .response_parser import parse_response
from gst_tally.models import LocalTallyJob
from .connector_protocol import operation


class LocalAgentTallyClient:
    """Send a single Tally HTTP request to the assigned Windows agent.

    A request is never re-enqueued after it has been claimed. If the result is
    unknown, the existing import reconciliation/query-back logic decides the
    safe next action rather than blindly creating another voucher.
    """
    def __init__(self, agent, batch):
        self.agent, self.batch = agent, batch
        self.last_http_status = None

    def post(self, payload, headers=None):
        body = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        return self._relay(body, headers or {"Content-Type": "application/xml; charset=utf-8"})

    def _relay(self, body, headers):
        key = hashlib.sha256((str(self.batch.pk) + "\n" + body).encode()).hexdigest()
        operation_name = operation(body, headers)
        job, _ = LocalTallyJob.objects.get_or_create(
            idempotency_key=key,
            defaults={"agent": self.agent, "batch": self.batch, "payload": {"operation": operation_name, "body": body, "headers": headers},
                      "expires_at": timezone.now() + timedelta(minutes=15)},
        )
        if job.agent_id != self.agent.id:
            raise TallyConnectionError("TALLY_AGENT_SCOPE_MISMATCH", "The Tally request belongs to another device.")
        if not job.payload.get("operation"):
            job.payload = {**job.payload, "operation": operation_name}
            job.save(update_fields=["payload"])
        deadline = time.monotonic() + float(getattr(settings, "TALLY_AGENT_RESULT_TIMEOUT", 90))
        while time.monotonic() < deadline:
            job.refresh_from_db()
            if job.status == LocalTallyJob.SUCCESS:
                raw = str((job.acknowledgement or {}).get("raw_response", ""))
                if raw:
                    self.last_http_status = 200
                    return raw.encode("utf-8")
                raise TallyConnectionError("TALLY_AGENT_INVALID_ACK", "Agent returned no Tally response.")
            if job.status in (LocalTallyJob.FAILED, LocalTallyJob.RETRYABLE, LocalTallyJob.EXPIRED):
                raise TallyConnectionError("TALLY_AGENT_JOB_FAILED", job.error_message or "Local Tally agent could not complete the request.")
            time.sleep(0.5)
        raise TallyConnectionError("TALLY_AGENT_RESULT_UNKNOWN", "Tally acknowledgement is pending; reconciliation is required before retrying.")

    def import_data(self, payload):
        return parse_response(self.post(payload))

    def import_data_batch(self, payload):
        return parse_response(self.post(payload))

    def import_json(self, payload, object_id="All Masters"):
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return parse_json_response(self._relay(body, {
            "Content-Type": "application/json", "version": "1", "tallyrequest": "Import",
            "type": "Data", "id": object_id, "detailed-response": "Yes",
        }))
