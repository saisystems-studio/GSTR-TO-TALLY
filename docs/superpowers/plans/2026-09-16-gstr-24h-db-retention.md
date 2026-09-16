# GSTR 24-Hour Database Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain import row data for at most 24 hours while retaining only a file-level audit summary and allowing correction re-uploads during the active session.

**Architecture:** `GSTImportBatch` is the active session and owns temporary rows. One permanent `GSTCompanyImportSummary` belongs to the session and has aggregate data only. Expired batches are finalized then deleted transactionally without Tally/local-agent interaction.

**Tech Stack:** Django 5 ORM and migrations; SQL Server; Django tests.

**Spec:** User-approved 24-hour retention/re-upload design in this conversation.

## Global Constraints

- Work only on `gstr-24h-db-retention`; never merge or modify `main`.
- Isolate sessions by company GSTIN, return type, and active `expires_at`.
- Do not permanently retain SQL invoice identities, rows, source JSON, mapping, job, error, or correction data.
- Cleanup is database-only and makes zero Tally/local-agent calls.
- Preserve existing Tally query-back before actual writes.

---

### Task 1: Establish session/audit data model

**Files:**
- Modify: `backend/gst_tally/models.py`
- Create: `backend/gst_tally/migrations/0039_24h_import_session_retention.py`
- Test: `backend/gst_tally/tests/test_import_session_retention.py`

**Interfaces:** `GSTCompanyImportSummary.session_key` identifies exactly one permanent session summary; temporary batch relationships cascade safely.

- [ ] **Step 1: Write a failing model test**

```python
def test_new_batch_has_one_session_summary_and_24_hour_expiry(self):
    batch = import_file(csv_upload(...), "GSTR2A", "")
    self.assertEqual(batch.company_import_summary.session_key, str(batch.pk))
    self.assertEqual(batch.expires_at, batch.uploaded_at + timedelta(hours=24))
```

- [ ] **Step 2: Run it and verify the missing-session failure**

Run: `python manage.py test gst_tally.tests.test_import_session_retention -v 2`

- [ ] **Step 3: Implement model and migration**

```python
session_key = models.CharField(max_length=64, unique=True)
```

Set temporary-to-temporary foreign keys to safe cascading deletes; leave permanent-summary links nullable/`SET_NULL`.

- [ ] **Step 4: Re-run the focused test and commit**

### Task 2: Reuse active session and expose only actionable rows

**Files:**
- Modify: `backend/gst_tally/services/import_service.py`
- Modify: `backend/gst_tally/services/import_summary.py`
- Modify: `backend/gst_tally/serializers.py`
- Test: `backend/gst_tally/tests/test_import_session_retention.py`

**Interfaces:** `import_file(...) -> GSTImportBatch` returns the active session; `processing_state` marks active correction rows.

- [ ] **Step 1: Write failing 45-success/5-correction and repeat-upload tests**

```python
def test_corrected_upload_reuses_session_and_only_five_rows_are_actionable(self):
    session = import_file(csv_upload(rows=first_fifty()), "GSTR2A", "")
    mark_imported(session, count=45); mark_failed(session, count=5)
    retry = import_file(csv_upload(rows=corrected_fifty()), "GSTR2A", "")
    self.assertEqual(retry.pk, session.pk)
    self.assertEqual(actionable_invoices(retry).count(), 5)
```

- [ ] **Step 2: Run the test and verify it fails because uploads create separate batches**

- [ ] **Step 3: Implement indexed, locked active-session lookup and bulk row classification**

```python
active = GSTImportBatch.objects.select_for_update().filter(
    company_gstin=company_gstin, gst_return_type=return_type, expires_at__gt=timezone.now()
).order_by("-uploaded_at").first()
```

Completed rows are skipped; retryable rows are refreshed; preview/import query only actionable states.

- [ ] **Step 4: Run focused session and summary tests and commit**

### Task 3: Database-only expiry cleanup

**Files:**
- Modify: `backend/gst_tally/services/import_service.py`
- Modify: `backend/gst_tally/management/commands/cleanup_expired_imports.py`
- Modify: `backend/gst_tally/management/commands/cleanup_gst_temporary_data.py`
- Test: `backend/gst_tally/tests/test_import_session_retention.py`

**Interfaces:** `cleanup_expired_processing_rows(now=None) -> dict` finalizes summaries then deletes temporary batches.

- [ ] **Step 1: Write failing cleanup/no-Tally-call test**

```python
@patch("gst_tally.tally.service.import_batch")
def test_cleanup_finalizes_summary_and_never_calls_tally(self, tally_import):
    cleanup_expired_processing_rows(now=expired_now)
    self.assertFalse(GSTImportBatch.objects.filter(pk=batch.pk).exists())
    self.assertTrue(GSTCompanyImportSummary.objects.filter(pk=summary.pk).exists())
    tally_import.assert_not_called()
```

- [ ] **Step 2: Run and verify the existing protected-mapping cleanup failure**

- [ ] **Step 3: Finalize aggregate summary and delete in one transaction**

```python
with transaction.atomic():
    for summary in summaries: update_company_import_summary(summary)
    GSTImportBatch.objects.filter(pk__in=batch_ids).delete()
```

No `tally.*` or local-agent import belongs in this service.

- [ ] **Step 4: Run focused tests and commit**

### Task 4: Permit identical uploads after expiry

**Files:**
- Modify: `backend/gst_tally/services/import_service.py`
- Modify: `backend/gst_tally/tally/service.py` only if SQL-registry preview filtering needs active-session scoping
- Test: `backend/gst_tally/tests/test_import_session_retention.py`

**Interfaces:** an identical post-cleanup upload returns a different batch/session and is not blocked by historical summaries.

- [ ] **Step 1: Write failing post-expiry acceptance test**

```python
def test_same_file_after_expiry_creates_a_new_actionable_session(self):
    first = import_file(csv_upload(...), "GSTR2A", "")
    expire_and_cleanup(first)
    second = import_file(csv_upload(...), "GSTR2A", "")
    self.assertNotEqual(first.pk, second.pk)
    self.assertEqual(second.invoices.count(), 50)
```

- [ ] **Step 2: Run and verify permanent SQL duplicate state fails it**
- [ ] **Step 3: Limit SQL fingerprint/registry decisions to current active session; retain Tally query-back code**
- [ ] **Step 4: Run retention, summary, identity suites and commit**

### Task 5: Verify migration and scope

**Files:** only changes required by Tasks 1–4.

- [ ] **Step 1: Run `python manage.py makemigrations --check --dry-run`**
- [ ] **Step 2: Run `python manage.py test gst_tally.tests.test_import_session_retention gst_tally.tests.test_import_summary_registry gst_tally.tests.test_import_identity -v 1`**
- [ ] **Step 3: Run `git diff --check`, `git diff --stat`, and `git status --short`**
