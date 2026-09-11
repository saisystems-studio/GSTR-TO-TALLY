"""Step 6 import job/lock model (tally/import_job.py + the views that expose
it). Covers the properties the frontend polling flow depends on: a job row
IS the lock (no separate cache dependency), a stale RUNNING job is surfaced
as INTERRUPTED rather than blocking the batch forever, and the HTTP layer
never lets a job's own status ("RUNNING", "PENDING", ...) shadow the outcome
the frontend actually branches on ("already_running"/"interrupted"/"running").
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from gst_tally.models import GSTImportBatch, TallyImportJob
from gst_tally.tally.client import TallyConnectionError
from gst_tally.tally.import_job import _run_job, get_active_job_for_batch, get_job, serialize_job, start_import_job


def make_batch():
    return GSTImportBatch.objects.create(
        file_name="batch.xlsx", file_type="EXCEL", gst_return_type="GSTR1",
        company_gstin="33AFHPM6103Q1Z8", source_parties={})


class StartImportJobTests(TestCase):
    def setUp(self):
        self.batch = make_batch()

    def test_first_call_starts_a_pending_job(self):
        job, outcome = start_import_job(self.batch)
        self.assertEqual(outcome, "started")
        self.assertEqual(job.status, "PENDING")
        self.assertEqual(job.batch_id, self.batch.id)

    def test_second_call_while_healthy_attaches_instead_of_starting_another(self):
        first, _ = start_import_job(self.batch)
        first.status = "RUNNING"
        first.save(update_fields=["status"])
        second, outcome = start_import_job(self.batch)
        self.assertEqual(outcome, "already_running")
        self.assertEqual(second.job_id, first.job_id)
        self.assertEqual(TallyImportJob.objects.filter(batch=self.batch).count(), 1)

    def test_stale_heartbeat_is_surfaced_as_interrupted_not_resent(self):
        stuck, _ = start_import_job(self.batch)
        stuck.status = "RUNNING"
        stuck.heartbeat_at = timezone.now() - timedelta(minutes=10)
        stuck.save(update_fields=["status", "heartbeat_at"])

        job, outcome = start_import_job(self.batch)
        self.assertEqual(outcome, "interrupted")
        self.assertEqual(job.job_id, stuck.job_id)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "INTERRUPTED")

        # The interrupted call above must not itself start a new job -- only
        # the caller's NEXT explicit start does (see start_import_job's
        # docstring: never auto-resumes a writing job on the caller's behalf).
        self.assertEqual(TallyImportJob.objects.filter(batch=self.batch).count(), 1)
        resumed, resumed_outcome = start_import_job(self.batch)
        self.assertEqual(resumed_outcome, "started")
        self.assertNotEqual(resumed.job_id, stuck.job_id)


class GetActiveJobForBatchTests(TestCase):
    def setUp(self):
        self.batch = make_batch()

    def test_none_when_batch_has_never_had_a_job(self):
        self.assertIsNone(get_active_job_for_batch(self.batch))

    def test_stale_running_job_is_flipped_to_interrupted_on_read(self):
        # heartbeat_at is auto_now_add -- Django stamps it to now() on INSERT
        # regardless of what's passed to create(), so backdating it requires
        # a separate update afterwards.
        job = TallyImportJob.objects.create(batch=self.batch, status="RUNNING")
        job.heartbeat_at = timezone.now() - timedelta(minutes=10)
        job.save(update_fields=["heartbeat_at"])
        found = get_active_job_for_batch(self.batch)
        self.assertEqual(found.job_id, job.job_id)
        self.assertEqual(found.status, "INTERRUPTED")

    def test_healthy_running_job_is_returned_unchanged(self):
        job = TallyImportJob.objects.create(batch=self.batch, status="RUNNING", heartbeat_at=timezone.now())
        found = get_active_job_for_batch(self.batch)
        self.assertEqual(found.job_id, job.job_id)
        self.assertEqual(found.status, "RUNNING")


class RunJobConnectionLostTests(TestCase):
    """Spec section 12: Tally dropping mid-batch must be distinguishable from
    any other crash, and must never lose progress already made."""

    def setUp(self):
        self.batch = make_batch()

    def test_connection_error_marks_the_job_failed_with_its_error_code_and_keeps_prior_counts(self):
        job = TallyImportJob.objects.create(batch=self.batch, status="RUNNING", total=10,
                                            processed=4, imported=3, failed=1, heartbeat_at=timezone.now())

        with patch("gst_tally.tally.service.import_batch",
                  side_effect=TallyConnectionError("TALLY_CONNECTION_REFUSED", "Tally refused the HTTP connection")):
            _run_job(job.job_id)

        job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.error_code, "TALLY_CONNECTION_REFUSED")
        self.assertIn("refused", job.error_message.lower())
        # Progress already made before the connection dropped is untouched --
        # only status/error fields are written on this path.
        self.assertEqual(job.imported, 3)
        self.assertEqual(job.failed, 1)
        self.assertEqual(job.processed, 4)
        self.assertEqual(job.total, 10)

    def test_a_non_connection_crash_leaves_error_code_blank(self):
        job = TallyImportJob.objects.create(batch=self.batch, status="RUNNING", heartbeat_at=timezone.now())

        with patch("gst_tally.tally.service.import_batch", side_effect=RuntimeError("boom")):
            _run_job(job.job_id)

        job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.error_code, "")
        self.assertIn("boom", job.error_message)


class TallyImportViewTests(TestCase):
    """HTTP contract the frontend polling loop depends on. run_job_in_background
    is patched to a no-op everywhere here -- these tests are about the
    request/response contract (status codes, which "status" string wins,
    idempotency), not about actually driving a Tally import."""

    def setUp(self):
        self.batch = make_batch()
        self.client = APIClient()
        self.client.force_authenticate(user=self._make_user())

    def _make_user(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(username="tester", password="x")

    @patch("gst_tally.views.pre_import_security_check", return_value={"ready": True, "verification_result": "LICENSE_VERIFIED"})
    @patch("gst_tally.views.run_job_in_background")
    def test_post_starts_a_job_and_returns_running_not_the_jobs_own_pending_status(self, mock_run, mock_license):
        response = self.client.post(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/")
        self.assertEqual(response.status_code, 202)
        # Regression: serialize_job(job)'s own "status" field (job.status,
        # e.g. "PENDING") must never silently overwrite the outcome the
        # frontend branches on -- a dict-spread ordering bug once let that happen.
        self.assertEqual(response.data["status"], "running")
        self.assertIn("job_id", response.data)
        mock_run.assert_called_once()

    @patch("gst_tally.views.pre_import_security_check", return_value={"ready": True, "verification_result": "LICENSE_VERIFIED"})
    @patch("gst_tally.views.run_job_in_background")
    def test_second_post_while_active_returns_already_running_not_the_jobs_own_status(self, mock_run, mock_license):
        first = self.client.post(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/")
        job = TallyImportJob.objects.get(job_id=first.data["job_id"])
        job.status = "RUNNING"
        job.save(update_fields=["status"])

        second = self.client.post(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["status"], "already_running")
        self.assertEqual(second.data["job_id"], str(job.job_id))
        mock_run.assert_called_once()  # never started a second background run

    def test_job_status_endpoint_returns_404_for_unknown_job(self):
        response = self.client.get("/api/gst-tally/tally-import/jobs/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(response.status_code, 404)

    @patch("gst_tally.views.pre_import_security_check", return_value={"ready": True, "verification_result": "LICENSE_VERIFIED"})
    @patch("gst_tally.views.run_job_in_background")
    def test_job_status_endpoint_reflects_progress(self, mock_run, mock_license):
        started = self.client.post(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/")
        job_id = started.data["job_id"]
        TallyImportJob.objects.filter(job_id=job_id).update(status="RUNNING", total=10, processed=4)

        status_response = self.client.get(f"/api/gst-tally/tally-import/jobs/{job_id}/")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.data["status"], "RUNNING")
        self.assertEqual(status_response.data["processed"], 4)
        self.assertEqual(status_response.data["total"], 10)

    def test_active_job_endpoint_returns_none_before_any_import(self):
        response = self.client.get(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/active-job/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "none")

    def test_active_job_endpoint_restores_running_job_for_page_reload(self):
        job = TallyImportJob.objects.create(batch=self.batch, status="RUNNING", total=5, processed=2,
                                            heartbeat_at=timezone.now())
        response = self.client.get(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/active-job/")
        self.assertEqual(response.data["status"], "RUNNING")
        self.assertEqual(response.data["job_id"], str(job.job_id))
        self.assertEqual(response.data["processed"], 2)
