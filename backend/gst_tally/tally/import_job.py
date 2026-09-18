"""Step 6 Tally import execution as a job/state model.

Replaces holding one long synchronous HTTP request open for the whole batch
import: POST starts (or attaches to) a job and returns immediately; the
actual import runs on a background thread inside this same process, updating
a TallyImportJob row's heartbeat/progress as it goes; the frontend polls the
job's status instead of waiting on one request.

The job row IS the lock: a job is a valid, active lock only while its status
is PENDING/RUNNING/VERIFYING AND its heartbeat is recent (see
ACTIVE_STATUSES/_is_stale below). This never blocks a batch forever on an
abandoned/crashed request the way a plain boolean flag could.

Safety is inherited from import_batch() itself (see service.py's
WRITE_ACCEPTED_PENDING_STATUS / pre-write reconciliation safety gate, added
for the query-back fix) -- this module only decides *whether and when* to
call import_batch() again, never how it decides to write or skip a voucher.
"""
import logging
import threading
import time
import uuid as uuid_module

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from gst_tally.models import TallyImportJob

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("PENDING", "RUNNING", "VERIFYING")
TERMINAL_STATUSES = ("COMPLETED", "PARTIAL", "FAILED", "INTERRUPTED")
# PAUSED is deliberately its own bucket -- not ACTIVE (no worker thread owns
# it, so the heartbeat staleness check must never apply to it -- a job can
# sit paused indefinitely) and not TERMINAL (it is resumable, and its `total`
# is the real batch total, not a finished count). See request_pause/resume_job.
PAUSED_STATUS = "PAUSED"
# start_import_job's lock also needs to treat a paused job as "this batch
# still has an owner" so a stray second POST to the plain start endpoint
# doesn't spin up a duplicate concurrent job while one is only paused.
LOCKED_STATUSES = ACTIVE_STATUSES + (PAUSED_STATUS,)

# A RUNNING/PENDING/VERIFYING job whose heartbeat hasn't moved in this long is
# treated as abandoned (its owning process/thread died or was killed) rather
# than genuinely still in progress. Comfortably longer than the per-voucher
# progress callback interval (see service.py's import_batch loop) so a
# merely-slow Tally response never gets mistaken for a dead job.
STALE_HEARTBEAT_SECONDS = getattr(settings, "TALLY_IMPORT_JOB_STALE_SECONDS", 45)


def _is_stale(job):
    return (timezone.now() - job.heartbeat_at).total_seconds() > STALE_HEARTBEAT_SECONDS


def serialize_job(job):
    """Compact status the frontend polls -- never includes the full result
    payload until the job actually reaches a terminal status, so a mid-run
    poll stays small and fast."""
    data = {
        "job_id": str(job.job_id), "batch_id": job.batch_id, "status": job.status,
        "pause_requested": job.pause_requested,
        "total": job.total, "processed": job.processed, "imported": job.imported,
        "failed": job.failed, "verification_pending": job.verification_pending,
        "skipped": job.skipped, "current_invoice": job.current_invoice,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
        "error_code": job.error_code,
    }
    if job.status in TERMINAL_STATUSES and job.result is not None:
        data["result"] = job.result
    return data


def get_job(job_id):
    try:
        return TallyImportJob.objects.get(job_id=job_id)
    except (TallyImportJob.DoesNotExist, ValueError, TypeError):
        return None


def get_active_job_for_batch(batch):
    """For page-reload recovery (task spec acceptance test 23): the job the
    frontend should re-attach to, if one is genuinely still active. A stale
    one is surfaced too (as INTERRUPTED) so a reloading user sees why the
    page isn't showing "Ready to Import" rather than silently reverting to
    it. Returns None only when there is truly nothing to restore."""
    job = TallyImportJob.objects.filter(batch=batch).order_by("-started_at").first()
    if not job:
        return None
    if job.status in ACTIVE_STATUSES and _is_stale(job):
        job.status = "INTERRUPTED"
        job.save(update_fields=["status"])
    return job


def _counts_from_results(results):
    """Same status strings service.py's own final `counts` dict counts by --
    kept in one place so the live progress a job reports mid-run can never
    disagree with the final summary the same run produces at the end."""
    from .service import WRITE_ACCEPTED_PENDING_STATUS
    imported = sum(1 for row in results if row.get("status") in ("Imported", "Already Imported"))
    pending = sum(1 for row in results if row.get("status") == WRITE_ACCEPTED_PENDING_STATUS)
    skipped = sum(1 for row in results if row.get("status") in ("Skipped", "Needs Attention"))
    failed = sum(1 for row in results if row.get("status") not in
                ("Imported", "Already Imported", WRITE_ACCEPTED_PENDING_STATUS, "Skipped",
                 "Needs Attention", "Not Attempted", "Review Required", "Validation Failed"))
    return imported, failed, pending, skipped


def start_import_job(batch, request_id="", local_agent=None):
    """Attach to an existing healthy job, surface a stale one as INTERRUPTED
    without silently resuming it, or start a genuinely new one.

    Returns (job, outcome) where outcome is one of:
      "already_running" -- a healthy job is already active; job is that job.
      "paused"           -- the most recent job is paused; job is that job.
                             The caller must use resume_job(), not this
                             function, to continue it -- pausing/resuming is
                             always an explicit, separate action from start.
      "interrupted"      -- the most recent job just got marked INTERRUPTED;
                             job is that (now-inactive) job. The caller must
                             ask again (a fresh POST) to actually start a new
                             one -- this function never auto-resumes a
                             writing job on the caller's behalf.
      "started"          -- job is a brand new PENDING job the caller should
                             now run (e.g. on a background thread).
    """
    with transaction.atomic():
        existing = (TallyImportJob.objects.select_for_update()
                    .filter(batch=batch, status__in=LOCKED_STATUSES)
                    .order_by("-started_at").first())
        if existing:
            if existing.status == PAUSED_STATUS:
                return existing, "paused"
            if not _is_stale(existing):
                return existing, "already_running"
            # Abandoned by whatever process/request last owned it -- release
            # the lock, but do NOT immediately write anything. Reconciliation
            # happens the same way every import_batch() run already reconciles
            # (preflight query against Tally before any write -- see
            # service.py), which only occurs once the caller explicitly starts
            # the next job.
            existing.status = "INTERRUPTED"
            existing.save(update_fields=["status"])
            return existing, "interrupted"
        job = TallyImportJob.objects.create(batch=batch, local_agent=local_agent, status="PENDING", request_id=request_id,
                                            total=0, heartbeat_at=timezone.now())
        return job, "started"


def _make_progress_callback(job_id):
    """Runs on the worker thread, once per voucher -- see service.py's
    import_batch loop. Cheap DB update only (no Tally I/O of its own);
    exceptions here must never abort the real import, so callers already wrap
    this in try/except (see import_batch)."""
    last_update = [0.0]

    def _callback(processed, total, results_so_far, current_invoice):
        now = time.monotonic()
        interval = max(0.0, float(getattr(settings, "TALLY_IMPORT_PROGRESS_INTERVAL", 0.25)))
        if processed != total and now - last_update[0] < interval:
            return
        last_update[0] = now
        imported, failed, pending, skipped = _counts_from_results(results_so_far)
        processed = min(total, imported + failed + pending + skipped)
        TallyImportJob.objects.filter(job_id=job_id).update(
            status="VERIFYING" if current_invoice == "" and processed == total and total else "RUNNING",
            total=total, processed=processed, imported=imported, failed=failed,
            verification_pending=pending, skipped=skipped, current_invoice=current_invoice,
            heartbeat_at=timezone.now())
    return _callback


def _make_should_pause_callback(job_id):
    """Runs on the worker thread, once per voucher, immediately before any
    Tally I/O for it (see service.py's import_batch loop) -- a fresh DB read
    each time so a pause request is picked up as soon as the in-flight
    voucher finishes, not only at the next heartbeat interval."""
    last_check = [0.0]
    last_value = [False]

    def _check():
        now = time.monotonic()
        if now - last_check[0] >= 0.25:
            last_check[0] = now
            last_value[0] = bool(TallyImportJob.objects.filter(job_id=job_id).values_list("pause_requested", flat=True).first())
        return last_value[0]
    return _check


def request_pause(job_id):
    """Marks a running job to stop before its next voucher -- never touches
    a write already in flight (see should_pause_callback above), so this can
    never leave a voucher half-written. Returns False if there is no
    active job to pause (already paused/finished/gone)."""
    return TallyImportJob.objects.filter(job_id=job_id, status__in=ACTIVE_STATUSES).update(pause_requested=True) > 0


def resume_job(job_id):
    """Clears the pause flag and re-runs the same job row on a fresh
    background thread. Safe to restart from the top of the voucher list --
    import_batch()'s own pre-write reconciliation against Tally (see its
    module-level safety notes) reports every already-imported voucher as
    "Already Imported" and never re-sends it, so this always continues from
    the next unprocessed voucher in effect, without needing a separate
    "resume index" to track. Returns False if the job isn't actually
    PAUSED (already resumed elsewhere, or gone)."""
    updated = TallyImportJob.objects.filter(job_id=job_id, status=PAUSED_STATUS).update(
        pause_requested=False, status="PENDING", heartbeat_at=timezone.now())
    if updated:
        run_job_in_background(job_id)
    return updated > 0


def _run_job(job_id):
    from .service import import_batch, WRITE_ACCEPTED_PENDING_STATUS
    from .client import TallyClient, TallyConnectionError
    from .local_agent_transport import LocalAgentTallyClient
    from gst_tally.models import GSTImportBatch

    job = get_job(job_id)
    if job is None:
        logger.error("Tally import job %s vanished before it could start", job_id)
        return
    job.status = "RUNNING"
    job.heartbeat_at = timezone.now()
    job.save(update_fields=["status", "heartbeat_at"])
    try:
        batch = GSTImportBatch.objects.get(pk=job.batch_id)
        client = LocalAgentTallyClient(job.local_agent, batch) if job.local_agent_id else TallyClient()
        result = import_batch(batch, client=client, progress_callback=_make_progress_callback(job_id),
                              should_pause_callback=_make_should_pause_callback(job_id))
        if result.get("paused"):
            # Never reached a terminal outcome -- counts here describe an
            # in-progress run, not a finished one, so this is handled
            # entirely separately from the COMPLETED/PARTIAL/FAILED branch
            # below (no `result`/`completed_at` -- the job is still resumable).
            summary = result.get("summary") or {}
            job.refresh_from_db()
            job.status = PAUSED_STATUS
            job.pause_requested = False
            job.total = summary.get("total", summary.get("total_eligible", summary.get("eligible", job.total)))
            job.processed = result.get("processed_before_pause", job.processed)
            job.imported = (summary.get("imported", 0) or 0) + (summary.get("already_imported", 0) or 0)
            job.failed = summary.get("failed", 0) or 0
            job.verification_pending = summary.get("verification_pending", 0) or 0
            job.skipped = summary.get("skipped", 0) or 0
            job.heartbeat_at = timezone.now()
            job.save()
            return
        import_status = result.get("import_status", "")
        if import_status in ("Import Successful", "Import Verified"):
            final_status = "COMPLETED"
        elif import_status == "Partial Import":
            final_status = "PARTIAL"
        elif import_status == WRITE_ACCEPTED_PENDING_STATUS:
            final_status = "PARTIAL"
        else:
            final_status = "FAILED"
        summary = result.get("summary") or {}
        job.refresh_from_db()
        job.status = final_status
        job.result = result
        # The job progress is about every source voucher reaching a terminal
        # state, not only the subset eligible for a Tally write. Otherwise
        # legitimately skipped/invalid rows appear as permanent Remaining.
        job.total = summary.get("total", summary.get("total_eligible", summary.get("eligible", job.total)))
        job.imported = (summary.get("imported", 0) or 0) + (summary.get("already_imported", 0) or 0)
        # summary["failed"] only ever counted master/preflight/tally/verification
        # failures. "unknown" (a query-back that couldn't be confirmed, incl. a
        # transient Tally connection failure), "not_attempted" (blocked by an
        # earlier whole-batch precheck), and "waiting_for_tally_period" are just
        # as terminal -- each is a real row in summary["results"] -- but were
        # never added to any job bucket, so those vouchers stayed in Remaining
        # forever even after the run had genuinely finished with them. Folding
        # them into "failed" (the UI's Not Imported bucket) is what makes
        # imported + failed + skipped + pending == total once the run ends.
        job.failed = (
            (summary.get("failed", 0) or 0)
            + (summary.get("unknown", 0) or 0)
            + (summary.get("not_attempted", 0) or 0)
            + (summary.get("waiting_for_tally_period", 0) or 0)
        )
        job.verification_pending = summary.get("verification_pending", 0) or 0
        # Validation-rejected and structurally-invalid rows are terminally
        # classified and were never sent to Tally. Expose them with the job's
        # skipped bucket so they cannot leave an otherwise completed import
        # showing as Remaining.
        job.skipped = (
            (summary.get("skipped", 0) or 0)
            + (summary.get("validation_failed", 0) or 0)
            + (summary.get("invalid", 0) or 0)
        )
        # Use the final result counters, not the last throttled progress
        # callback.  The latter can legitimately contain only early skipped
        # records while the worker has already classified the whole batch.
        job.processed = min(job.total, job.imported + job.failed + job.skipped + job.verification_pending)
        job.current_invoice = ""
        job.completed_at = timezone.now()
        job.heartbeat_at = timezone.now()
        job.save()
    except TallyConnectionError as exc:
        # Tally genuinely dropped mid-batch (refused/timed out/unreachable) --
        # counters already written by the last progress_callback tick are
        # untouched by this update (only status/error fields are set here),
        # so nothing already imported is lost. error_code lets the frontend
        # show "Tally Connection Lost" / Retry Connection instead of a
        # generic failure (see views.py's serialize_job consumer).
        logger.warning("Tally import job %s lost its Tally connection: %s", job_id, exc)
        TallyImportJob.objects.filter(job_id=job_id).update(
            status="FAILED", error_code=exc.code, error_message=str(exc),
            completed_at=timezone.now(), heartbeat_at=timezone.now())
    except Exception as exc:
        logger.exception("Tally import job %s crashed", job_id)
        TallyImportJob.objects.filter(job_id=job_id).update(
            status="FAILED", error_message=str(exc) or exc.__class__.__name__,
            completed_at=timezone.now(), heartbeat_at=timezone.now())


def run_job_in_background(job_id):
    thread = threading.Thread(target=_run_job, name=f"tally-import-job-{job_id}", args=(job_id,), daemon=True)
    thread.start()
    return thread
