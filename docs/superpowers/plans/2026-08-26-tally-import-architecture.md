# Tally Import Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement verified, idempotent GSTR-1/GSTR-2A/GSTR-2B master and Accounting Invoice imports behind the unchanged six-step UI.

**Architecture:** Preserve current parsers/endpoints and introduce focused normalized-contract, rule-resolver, fingerprint, master-catalog, transport, and outcome services. XML remains the guaranteed Tally write path; JSON stays the canonical internal representation and is used natively only when an operation explicitly supports it.

**Tech Stack:** Python 3/Django/DRF, SQL Server, ElementTree XML, existing Tally HTTP/ODBC clients, React/Vite, Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-26-tally-import-architecture-design.md`

## Global Constraints

- Keep the six UI steps, names, order, and layout unchanged.
- Preserve existing parsers, endpoints, models, and validation wherever possible.
- GSTR1 and GSTR2A/GSTR2B accounting flows must never mix.
- XML is the reliable fallback; native JSON support must never be faked.
- Tally queries, not HTTP status or local SQL alone, confirm masters and vouchers.
- Use test-first red-green-refactor for every production behavior change.

---

## 1. Files/modules to change

- `backend/gst_tally/services/import_service.py`: normalized source fingerprint generation and duplicate-batch gate.
- `backend/gst_tally/services/gstr1_parser.py`, `gstr2a_parser.py`, and `gstr2b_parser.py`: retain compatibility wrappers while delegating to independent format readers and return mappers.
- `backend/gst_tally/services/source_preview.py`: normalized preview fields and field-level validation state.
- `backend/gst_tally/models.py`: minimal batch fingerprint/outcome fields and verified voucher metadata.
- `backend/gst_tally/serializers.py`: expose duplicate/outcome fields without changing routes.
- `backend/gst_tally/views.py` and `urls.py`: bounded correction and read-only PDF/Excel export actions.
- `backend/gst_tally/tally/mappings.py`: consume the resolver and emit the canonical voucher contract.
- `backend/gst_tally/tally/master_builder.py`: delegate to semantic XML builders and enforce master order.
- `backend/gst_tally/tally/voucher_builder.py`: emit real Accounting Invoice XML.
- `backend/gst_tally/tally/voucher_verification.py`: match current-company voucher identifiers and identity fields.
- `backend/gst_tally/tally/response_parser.py` and `json_response_parser.py`: require authoritative positive counters and reject ignored/cancelled/deleted/line-error responses.
- `backend/gst_tally/tally/service.py`: orchestrate company check, masters, reconciliation, writes, verification, persistence, and one outcome.
- `backend/gst_tally/tally/json_master_builder.py` and `json_voucher_builder.py`: build from the canonical contract; do not imply native support.
- `backend/gst_tally/tally/connection.py`/`odbc.py`: return the master properties required for safe reuse where the current query path supports them.
- `backend/config/settings.py` and `backend/.env.example`: XML default and validated XML/JSON configuration.
- `frontend/src/utils/importOutcome.js`: render backend outcome without recomputing it.
- `frontend/src/services/gstTallyApi.js`: correction and report-download calls.
- `frontend/src/components/gst-tally/FileUpload.jsx` and `ImportTabs.jsx`: independent enabled format selection and extension validation for every return type.
- `frontend/src/components/gst-tally/ExcelPreviewGrid.jsx`: invalid-cell-only editing, revalidation, and export controls.
- `frontend/src/components/gst-tally/TallyWorkflowStages.jsx`: display the same backend status/title/message, expose non-zero Round Off in Step 5, and preserve counters and workflow.
- `frontend/src/pages/GstTallyImport.jsx`: pass the backend result to the shared renderer only; no workflow changes.

## 2. New services/adapters to add

### Task 1: Canonical contract and GST resolver

**Files:**
- Create: `backend/gst_tally/tally/contracts.py`
- Create: `backend/gst_tally/tally/gst_rules.py`
- Test: `backend/gst_tally/tests/test_gst_rules.py`
- Test: `backend/gst_tally/tests/test_normalized_contract.py`

**Produces:** `resolve_flow(return_type)`, `gst_state(gstin)`, `tax_ledgers(return_type, company_gstin, party_gstin, gst_rate)`, `extract_ledger_rate(name)`, `normalize_ledger_name(name)`, and canonical master/voucher constructors including raw/rounded/round-off totals.

- [ ] Write failing tests for all return routes, complete state mapping, name/rate variants, four intra/inter mappings, party grouping/registration/name priority/pincode, voucher mode, and canonical raw/rounded/round-off fields.
- [ ] Run focused tests and confirm failures are caused by missing resolver/contract APIs.
- [ ] Implement immutable rule tables and deterministic JSON-safe constructors.
- [ ] Run focused tests, then existing mapping/parser tests.

### Task 1A: Nine-combination parser architecture

**Files:**
- Create: `backend/gst_tally/services/source_aliases.py`
- Create: `backend/gst_tally/services/source_readers.py`
- Create: `backend/gst_tally/services/return_mappers.py`
- Modify: `backend/gst_tally/services/gstr1_parser.py`
- Modify: `backend/gst_tally/services/gstr2a_parser.py`
- Modify: `backend/gst_tally/services/gstr2b_parser.py`
- Modify: `backend/gst_tally/services/import_service.py`
- Modify: `backend/gst_tally/services/source_preview.py`
- Test: `backend/gst_tally/tests/test_source_format_matrix.py`
- Test: `backend/gst_tally/tests/test_cross_format_equivalence.py`

**Produces:** `parser_for(input_format)`, `mapper_for(return_type)`, shared alias lookup, supported JSON-shape extraction, and one canonical output for all nine combinations.

- [ ] Write nine failing matrix tests proving every return type parses JSON, CSV, and Excel and records the selected source format.
- [ ] For each combination, assert canonical preview, correct party group, account ledger family, tax ledger family, voucher type, Accounting Invoice mode, and duplicate fingerprint behavior.
- [ ] Write three failing cross-format equivalence tests proving equivalent JSON/CSV/Excel accounting fields normalize identically for GSTR1, GSTR2A, and GSTR2B.
- [ ] Write failing alias tests for the required GSTIN, invoice number, taxable value, and related shared header variants.
- [ ] Write failing JSON-shape tests for flat arrays, normalized objects, existing portal nested shapes, and return-specific unsupported-structure messages.
- [ ] Write failing tests for fully empty CSV/Excel rows and selected-format/extension mismatches, including the exact warning semantics.
- [ ] Verify failures demonstrate the current extension-to-return parser coupling.
- [ ] Implement neutral readers, shared aliases, return mappers, and thin compatibility wrappers; remove the extension map that selects a return-specific parser.
- [ ] Run matrix, equivalence, existing parser, preview, and import tests.

### Task 2: Fingerprint and source-line identity

**Files:**
- Create: `backend/gst_tally/services/import_identity.py`
- Modify: `backend/gst_tally/services/import_service.py`
- Test: `backend/gst_tally/tests/test_import_identity.py`

**Produces:** `normalized_source_fingerprint(rows)`, `source_line_identity(row)`, and `duplicate_batch(company_gstin, return_type, tax_period, fingerprint)`.

- [ ] Write failing tests for identical/renamed Excel, CSV, JSON; changed same-name content; empty rows; and legitimate versus duplicate item lines.
- [ ] Verify failures before implementation.
- [ ] Implement stable date/decimal/key serialization and SHA-256 independent of filename/source row ordering metadata.
- [ ] Gate duplicate batches before downstream party/master/voucher work, but only against confirmed successful batches.
- [ ] Run focused parser/import tests.

### Task 2A: Step 2 field validation and canonical correction

**Files:**
- Create: `backend/gst_tally/services/invoice_validation.py`
- Create: `backend/gst_tally/services/invoice_correction.py`
- Modify: `backend/gst_tally/services/source_preview.py`
- Modify: `backend/gst_tally/views.py`
- Modify: `backend/gst_tally/urls.py`
- Test: `backend/gst_tally/tests/test_invoice_field_validation.py`
- Test: `backend/gst_tally/tests/test_invoice_corrections.py`

**Produces:** `validate_invoice_fields(invoice, company_context)`, `correct_invoice_field(batch, invoice_identity, field, value, user)`, structured `field_errors`, and a bounded invoice-correction endpoint.

- [ ] Write failing tests mapping every requested validation to the exact canonical cell, error code, direct message, expected/actual values, and editable flag.
- [ ] Write failing tests that valid fields are absent from `field_errors`, one/two failures unlock only those cells, and duplicate lines do not mark unrelated cells.
- [ ] Write failing correction tests for the strict field allowlist, signed two-decimal currency rules, typed date/Decimal/GSTIN parsing, audit metadata, wrong-correction persistence of the error, and unchanged immutable `source_line`.
- [ ] Write failing dependency tests for taxable-value recalculation and GSTIN-driven state/intra/inter revalidation.
- [ ] Write failing propagation tests proving corrected values reach party resolution, masters, voucher preview, idempotency, and import.
- [ ] Verify failures, implement correction/revalidation, and return the complete updated normalized invoice plus blocking count.
- [ ] Invalidate downstream prepared results after correction without changing the original fingerprint.
- [ ] Run focused and existing parser/voucher validation suites.

### Task 2B: Step 2 PDF and Excel exports

**Files:**
- Create: `backend/gst_tally/services/invoice_reports.py`
- Modify: `backend/gst_tally/views.py`
- Modify: `backend/gst_tally/urls.py`
- Modify: `backend/requirements.txt`
- Test: `backend/gst_tally/tests/test_invoice_reports.py`

**Produces:** current-canonical-data XLSX and landscape PDF responses.

- [ ] Write failing workbook tests for corrected values, title/company/return/period metadata, bold headers, widths, date/currency formats, full rows, and draft/final labels.
- [ ] Write failing PDF tests for metadata, corrected values, landscape pages, repeated headers, readable table, page numbers, and draft/final labels.
- [ ] Write failing side-effect tests proving exports do not change invoices, validation, batch outcome, mappings, or call Tally.
- [ ] Add ReportLab as the sole new report dependency; retain existing `openpyxl` for Excel.
- [ ] Generate both reports from the same canonical serializer used by the grid.
- [ ] Run focused report tests.

### Task 3: Semantic master catalog and XML/JSON builders

**Files:**
- Create: `backend/gst_tally/tally/master_catalog.py`
- Create: `backend/gst_tally/tally/xml_builders.py`
- Modify: `backend/gst_tally/tally/master_builder.py`
- Modify: `backend/gst_tally/tally/json_master_builder.py`
- Test: `backend/gst_tally/tests/test_master_models.py`
- Test: `backend/gst_tally/tests/test_tally_xml_builders.py`

**Produces:** account/tax/party master JSON models, property-aware candidate comparison, ordered master lists, and safe XML/JSON payload builders.

- [ ] Write failing tests for Sales/Purchase parents, all tax names/types/rates, debtor/creditor parties, registration/state/pincode, equivalent names, normalized Round Off aliases, non-GST Round Off properties, and rejection of misconfigured candidates.
- [ ] Verify the tests fail for the intended missing properties.
- [ ] Implement semantic matching and ElementTree builders with GST rate, taxability, supply type, duty head, percentage, and rounding fields.
- [ ] Run focused and existing Tally integration tests.

### Task 4: Transport abstraction and fallback

**Files:**
- Create: `backend/gst_tally/tally/transports.py`
- Modify: `backend/gst_tally/tally/client.py`
- Modify: `backend/config/settings.py`
- Modify: `backend/.env.example`
- Test: `backend/gst_tally/tests/test_tally_transports.py`

**Produces:** `TallyTransport`, `XmlTallyTransport`, `JsonTallyTransport`, and `transport_for(operation, configured_format)` returning requested/actual/fallback metadata.

- [ ] Write failing tests for XML selection, genuinely supported JSON operations, JSON-to-XML fallback, invalid configuration, parsed failures, and transport audit metadata.
- [ ] Verify failures.
- [ ] Implement adapters around the current client and response parsers; default configuration to XML.
- [ ] Run transport and existing client tests.

### Task 5: Voucher Accounting Invoice builders and verification

**Files:**
- Modify: `backend/gst_tally/tally/voucher_builder.py`
- Modify: `backend/gst_tally/tally/json_voucher_builder.py`
- Modify: `backend/gst_tally/tally/voucher_verification.py`
- Test: `backend/gst_tally/tests/test_accounting_invoice.py`
- Test: `backend/gst_tally/tests/test_voucher_reconciliation.py`

**Produces:** Sales/Purchase Accounting Invoice payloads and strong voucher identity verification.

- [ ] Write failing assertions for `ISINVOICE=Yes`, Accounting Invoice views, ledger-only allocations, debit/credit direction, Input versus Output taxes, and full-rate IGST.
- [ ] Write failing reconciliation tests for current-company GUID/MasterID/AlterID/number/reference/type/date/party matching in strongest-key order, wrong-type discovery, wrong-period rejection, stale mappings, and safe retry.
- [ ] Implement the minimal payload and verification changes.
- [ ] Run focused balance, period, XML/JSON, and reconciliation suites.

### Task 5A: Decimal nearest-rupee Round Off

**Files:**
- Modify: `backend/gst_tally/tally/validators.py`
- Modify: `backend/gst_tally/tally/mappings.py`
- Modify: `backend/gst_tally/tally/master_builder.py`
- Modify: `backend/gst_tally/tally/voucher_builder.py`
- Modify: `backend/gst_tally/tally/json_voucher_builder.py`
- Modify: `backend/gst_tally/tally/service.py`
- Test: `backend/gst_tally/tests/test_round_off.py`

**Produces:** `nearest_rupee_totals(taxable, cgst, sgst, igst, cess, other_charges)` returning Decimal-safe `raw_invoice_total`, `round_off`, and `rounded_invoice_total`; balanced Sales/Purchase allocations; reusable non-GST Round Off master.

- [ ] Write failing boundary tests for 100.00, 100.01, 100.40, 100.49, 100.50, 100.51, 100.60, and 100.99 using exact expected adjustments.
- [ ] Write failing tests proving `other_charges` is included before rounding but remains separate and non-taxable.
- [ ] Write failing tests for positive and negative adjustments in GSTR1 Sales, GSTR2A Purchase, and GSTR2B Purchase.
- [ ] Write failing XML and JSON builder tests for all Sales/Purchase sign combinations, omitted zero allocation, balanced party amount, and preserved Accounting Invoice mode.
- [ ] Write failing tests that reuse `Round Off`, `RoundOff`, `ROUND OFF`, and `Round-off` only when the ledger is non-GST, and never apply GST metadata to it.
- [ ] Write failing validation tests where source value matches the rounded total and where it matches neither the raw nor rounded legitimate total.
- [ ] Verify every test fails because the existing validator derives adjustments from source differences instead of explicit nearest-rupee rounding.
- [ ] Implement `Decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP)` after taxable, GST, cess, and other charges are summed.
- [ ] Feed the three canonical totals through preview, balance checks, XML/JSON builders, diagnostics, and query verification without modifying component totals.
- [ ] Run the focused suite and all existing purchase/rounding/Tally integration tests.

## 3. Database changes

### Task 5B: Current-company voucher register diagnostics and reconciliation

**Files:**
- Create: `backend/gst_tally/tally/voucher_register.py`
- Create: `backend/gst_tally/tally/reconciliation.py`
- Modify: `backend/gst_tally/tally/voucher_verification.py`
- Modify: `backend/gst_tally/tally/service.py`
- Modify: `backend/gst_tally/tally/response_parser.py`
- Modify: `backend/gst_tally/tally/json_response_parser.py`
- Test: `backend/gst_tally/tests/test_stale_mapping_reconciliation.py`
- Test: `backend/gst_tally/tests/test_tally_voucher_register.py`

**Produces:** direct date-range/type register query, all-type identity search, strongest-key reconciliation, sanitized per-voucher diagnostics, and retained `Missing in Tally` mappings.

- [ ] Write failing tests proving an `Imported` local mapping is always queried and never immediately preserved as `Already Imported`.
- [ ] Write failing tests that local mapping primary keys and ambiguous stored IDs are never displayed or accepted as Tally proof.
- [ ] Write failing direct-register tests for zero Purchase vouchers, returned identity fields/amounts, exact date range, explicit current company, and no local-mapping prefilter.
- [ ] Write failing tests for company name/GSTIN mismatch, wrong query period, wrong voucher type discovery, and strongest-key matching order.
- [ ] Write failing response tests for HTTP 200 with CREATED=0, LINEERROR, ERRORS, IGNORED, CANCELLED, and DELETED; none may persist success.
- [ ] Write failing safe-retry tests that retain/mark stale mappings, reverify required masters, write once, require query-back, and do not save success if query-back fails.
- [ ] Write failing structured-log tests for local/stored/current/query/write fields with secret redaction.
- [ ] Add a diagnostic reconciliation entry point capable of tracing two selected invoices and reconciling all mappings in a batch without deleting audit history.
- [ ] Run focused reconciliation, response-parser, voucher, period, and integration suites.

### Task 6: Additive persistence migration

**Files:**
- Modify: `backend/gst_tally/models.py`
- Create: `backend/gst_tally/migrations/0019_verified_import_identity.py`
- Test: `backend/gst_tally/tests/test_import_persistence.py`

**Batch fields:** `source_fingerprint`, `tax_period`, `import_outcome_status`, `import_confirmed_at`; composite conditional lookup index rather than a filename constraint.

**Voucher mapping fields:** `company_gstin`, `return_type`, `voucher_type`, `voucher_number`, `reference`, `voucher_guid`, `tally_master_id`, `tally_alter_id`, `identifier_type`, `verification_status`, `verified_at`, `requested_transport`, `actual_transport`, `transport_fallback_reason`, and structured reconciliation diagnostics.

**Correction audit model:** `GSTInvoiceCorrection(invoice, field_name, original_value, corrected_value, corrected_at, corrected_by)`. Parsed originals remain in `GSTInvoice.source_line`; the uploaded source is never overwritten.

- [ ] Write failing model/persistence tests for scoped fingerprints and verified identifiers.
- [ ] Write failing correction-audit tests for original/corrected values, timestamp/user, and multiple corrections without arbitrary-field access.
- [ ] Add nullable/defaulted fields so existing rows migrate safely.
- [ ] Replace the global key assumption only if required by current-company scope; preserve existing mappings for reconciliation.
- [ ] Run Django migration checks and model tests.

## 4. Backend API response changes

### Task 7: Verified orchestration and one outcome

**Files:**
- Modify: `backend/gst_tally/tally/service.py`
- Modify: `backend/gst_tally/views.py` only if duplicate upload needs a structured 409 response.
- Modify: `backend/gst_tally/serializers.py`
- Test: `backend/gst_tally/tests/test_verified_import_flow.py`
- Test: `backend/gst_tally/tests/test_import_outcome.py`

**Response:** top-level and `summary` counters remain available for compatibility; add authoritative `status`, `title`, `message`, `missing_in_tally`, and transport metadata.

- [ ] Write failing tests for company mismatch before writes, mandatory master order/verification, stale local mapping retry, verified already-present voucher, post-write query requirement, blank UI identifier without verified Tally identity, and no success on HTTP 200 alone.
- [ ] Write outcome tests for new success, all already verified, partial, and failed cases with exact dynamic messages.
- [ ] Implement per-voucher orchestration and atomic confirmed mapping updates.
- [ ] Ensure duplicate-file responses contain the required warning and perform zero Tally writes.
- [ ] Run backend API and complete Django tests.

## 5. Frontend status handling changes

### Task 8: Render authoritative outcome

**Files:**
- Modify: `frontend/src/utils/importOutcome.js`
- Modify: `frontend/src/utils/importOutcome.test.js`
- Modify: `frontend/src/components/gst-tally/TallyWorkflowStages.jsx`
- Modify: `frontend/src/pages/GstTallyImport.jsx`
- Test: existing Node tests plus focused component-pure helper tests where feasible.

- [ ] Write failing tests for success, already verified, partial, and failed backend outcomes and exact messages.
- [ ] Change the helper to consume backend `status/title/message` rather than independently deriving status.
- [ ] Make the center stage use the same helper/result as the toast while retaining every existing counter and the six-step layout; show Step 5 subtotal, non-zero Round Off, and rounded invoice total from backend fields.
- [ ] Run Node tests and Vite production build.

### Task 8A: Invalid-cell-only Step 2 UI

**Files:**
- Modify: `frontend/src/components/gst-tally/ExcelPreviewGrid.jsx`
- Modify: `frontend/src/pages/GstTallyImport.jsx`
- Modify: `frontend/src/services/gstTallyApi.js`
- Modify: `frontend/src/styles/gst-tally.css`
- Create: `frontend/src/components/gst-tally/invoicePreviewState.js`
- Create: `frontend/src/components/gst-tally/invoicePreviewState.test.js`

- [ ] Write failing pure-state tests proving valid cells stay read-only and only errored editable fields enter edit mode.
- [ ] Write failing tests for type-specific editors, signed two-decimal amount constraints, GSTIN uppercase normalization, per-invoice calculated/source/difference tooltips, save/revalidation replacement, and blocking counts.
- [ ] Write failing tests that Cancel restores the current persisted value without an API mutation and that an incorrect Save keeps only the remaining invalid cell red/editable.
- [ ] Write failing tests proving successful correction removes all invalid/editor styling, relocks the cell, and leaves no success color.
- [ ] Write failing tests for independent multiple-error correction and the exact `Validation Failed` to `Ready` transition only after the last blocking field passes.
- [ ] Write failing navigation tests for unresolved-error warning/blocking and normal Step 3 continuation after all errors clear.
- [ ] Write failing download-helper tests for corrected-data PDF/Excel downloads and no workflow/import side effects.
- [ ] Implement cell-local styling/editors, correction save, full-row response replacement, export buttons, and Next gating without changing the workflow.
- [ ] Run Node tests and Vite build.

### Task 8B: Independent Step 1 format selection

**Files:**
- Modify: `frontend/src/components/gst-tally/FileUpload.jsx`
- Modify: `frontend/src/components/gst-tally/ImportTabs.jsx`
- Modify: `frontend/src/pages/GstTallyImport.jsx`
- Modify: `frontend/src/services/gstTallyApi.js`
- Create: `frontend/src/components/gst-tally/sourceSelection.js`
- Create: `frontend/src/components/gst-tally/sourceSelection.test.js`

- [ ] Write failing tests for all three enabled format choices under each of the three return types and for return changes that do not force a format.
- [ ] Write failing tests for JSON/CSV/Excel extension matching, safe `.xls` compatibility, and exact mismatch messaging.
- [ ] Write failing request tests proving both `return_type` and `input_format` are submitted independently to preview and import endpoints.
- [ ] Implement the separate format selection inside Step 1 without adding, removing, renaming, or reordering workflow steps.
- [ ] Run Node tests and Vite build.

## 6. Test cases to add

The tasks above cover the requested 48 cases, the Round Off matrix, all Step 2 correction/export cases, the nine-combination input matrix, and the 13 stale-mapping diagnostics. Coverage includes direct current-company register queries, local-versus-Tally identifier separation, strongest-key reconciliation, wrong type/date/company detection, query-back-required retry, sanitized tracing, Save/Cancel, wrong corrections, row readiness transitions, invoice-specific tooltips, cross-format equivalence, field-specific errors/editability, canonical propagation, reports, and fingerprint/idempotency interaction.

Existing tests in `test_tally_integration.py`, `test_purchase_import_regressions.py`, parser/provider suites, and frontend status tests remain regression coverage and are not replaced.

## 7. Migration/order of implementation

1. Baseline backend/frontend tests and capture existing failures.
2. Contract and GST resolver.
3. Independent format readers, return mappers, and nine-combination normalization.
4. Fingerprint/source identities.
5. Step 2 validation/correction and report exports.
6. Master models/catalog/builders.
7. Transport adapters/fallback.
8. Accounting Invoice builders and reconciliation.
9. Current-company voucher-register diagnostics and stale-mapping reconciliation.
10. Decimal nearest-rupee Round Off and balanced allocations.
11. Additive migration/persistence, including correction audit and verified identifier typing.
12. Orchestration/outcome API.
13. Frontend independent format selection, invalid-cell behavior, exports, shared outcome, and Step 5 Round Off rendering.
14. Full automated verification and optional live Tally proof.

Every numbered implementation task follows RED (new failing test), GREEN (minimal implementation), REFACTOR, then the relevant regression suite. No production behavior is changed before its test fails for the expected reason.

## 8. Verification strategy

- Run targeted Django tests after every backend task using `backend/.venv/Scripts/python.exe manage.py test <test labels> --settings=config.test_settings`.
- Run `manage.py makemigrations --check --dry-run` and migration/model tests.
- Run the complete Django test suite with test settings.
- Run `npm test` and `npm run build` in `frontend`.
- Inspect generated XLSX structure/styles and PDF metadata/text; prove reports use corrected canonical data and perform no Tally calls or persistence mutations.
- Run all nine parser combinations and three cross-format equivalence fixtures through preview, rule resolution, fingerprinting, and voucher generation.
- Inspect representative XML and JSON snapshots for Sales, Purchase, CGST/SGST, InputCGST/InputSGST, IGST/InputIGST, party, Round Off, and Accounting Invoice vouchers.
- Verify exact Decimal boundary results, separate other-charge and Round Off allocations, all four Sales/Purchase sign combinations, zero-allocation omission, and final party amount equality with the rounded whole rupee.
- Use fake transports to prove zero writes for duplicates/company mismatch, XML fallback for unsupported JSON, and mandatory post-write query confirmation.
- Reconcile the target batch when its database and Tally connection are available: trace invoices 1 and one additional invoice, query the full April 2025 Purchase register, classify all 28 mappings, and record actual counts/company/period/response counters without deleting stale rows.
- If TallyPrime is reachable, verify current-company GSTIN, import one controlled voucher, query it back, and record returned identifiers. Otherwise report exactly `Live Tally proof pending`.
- Final report states what displayed ID 79 represented; direct Purchase count; company and period matches; CREATED/ALTERED/ERRORS values; confirmed root cause; changed files/tests; and live visibility proof or `Live Tally proof pending`.
- Before completion, run the verification-before-completion checklist and report commands/results rather than inferred success.

## Plan self-review

- Spec coverage: all confirmed nine-format combinations, source, Step 2 correction/export, rules, masters, vouchers, Round Off, stale-mapping diagnostics, duplicate, verification, persistence, outcome, UI, and live-proof requirements map to Tasks 1-8 plus Tasks 1A, 2A, 2B, 5A, 5B, 8A, and 8B.
- Placeholder scan: no deferred implementation placeholders remain.
- Interface consistency: canonical models feed both builders; adapters return common write metadata; reconciliation feeds persistence; one outcome feeds both frontend surfaces.
