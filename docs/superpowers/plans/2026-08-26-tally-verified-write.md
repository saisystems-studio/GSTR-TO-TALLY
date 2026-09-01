# Tally Verified Write Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a Purchase voucher is classified as imported only after a valid Tally query confirms it exists.

**Architecture:** Validate that query-back returns the requested Day Book report before treating absence as authoritative. Reconcile every local imported mapping against Tally, send voucher writes through XML, and persist `Imported` only after a successful write response and positive query-back.

**Tech Stack:** Django, Python `xml.etree.ElementTree`, Django TestCase

**Spec:** `C:/Users/Sai_Dev_1/.codex/attachments/69f884d2-b1b0-4e50-880d-08782fe979a3/pasted-text.txt`

## Global Constraints

- Trace only one invoice before any batch retry.
- Stop writes when the Tally query interface is invalid.
- Use XML for the active voucher write.
- Do not alter frontend labels.
- Never treat HTTP 200 alone as import success.
- Persist `Imported` only after query-back finds the voucher.

---

### Task 1: Authoritative voucher query

**Files:**
- Modify: `backend/gst_tally/tally/voucher_verification.py`
- Test: `backend/gst_tally/tests/test_tally_integration.py`

**Interfaces:**
- Consumes: `verify_voucher(client, company, voucher)`
- Produces: query result with `query_valid: bool`; unrelated Tally envelopes report `query_valid=False`.

- [ ] Write a failing test where a Day Book request receives an `Import Data / All Masters` envelope and assert `query_valid` is false.
- [ ] Run the focused test and confirm it fails because the response is currently treated as a valid not-found result.
- [ ] Add minimal response-envelope validation to `verify_voucher`.
- [ ] Run the focused test and confirm it passes.

### Task 2: Verified persistence and stale mapping reconciliation

**Files:**
- Modify: `backend/gst_tally/tally/service.py`
- Test: `backend/gst_tally/tests/test_tally_integration.py`

**Interfaces:**
- Consumes: `verify_voucher(...)->{query_valid, found, ...}`
- Produces: `Already Imported` only for positive query-back; `Write not verified` for accepted writes not found; no stale-row deletion on invalid queries.

- [ ] Write failing tests proving an `Imported` local mapping is queried and an accepted write without positive query-back is not persisted as `Imported`.
- [ ] Run focused tests and verify the expected failures.
- [ ] Reorder reconciliation and persistence so query validity and `found` gate every imported status.
- [ ] Add the required trace markers and write metadata (`dry_run`, `write_enabled`, requested/actual transport, `actual_write_attempted`) to the result diagnostics.
- [ ] Run focused tests and confirm they pass.

### Task 3: Force XML active writes and verify regression suite

**Files:**
- Modify: `backend/gst_tally/tally/service.py`
- Test: `backend/gst_tally/tests/test_tally_integration.py`

**Interfaces:**
- Consumes: internal normalized voucher dictionaries.
- Produces: exact Tally XML sent via `client.import_data`, with JSON retained only as internal/preview representation.

- [ ] Write a failing test asserting voucher and master writes use XML even when `TALLY_WRITE_FORMAT=JSON`.
- [ ] Run it and confirm the current JSON transport causes failure.
- [ ] Make XML the active write transport and report the JSON-to-XML fallback reason.
- [ ] Run backend Tally tests and purchase regression tests.
- [ ] Re-run the live query preflight; do not send a voucher unless it returns a valid Day Book report.
