# Tally Import Architecture Design

## Purpose

Implement the final GSTR-to-Tally business rules inside the existing six-step workflow without redesigning the UI or replacing the current subsystem. Excel, CSV, and JSON inputs become one canonical JSON representation; Tally writes use a transport adapter with XML as the reliable fallback; local success is recorded only after the current Tally company confirms the written object.

## Fixed UI workflow

The UI remains exactly:

1. Upload
2. Invoice Preview
3. Party Details
4. Tally Masters
5. Voucher Preview
6. Import to Tally

No transport, ledger, tax, or verification steps are added to the UI. Those operations remain backend details within the existing stages.

## Independent return type and input format

Step 1 exposes return type and input format as independent choices. GSTR1, GSTR2A, and GSTR2B each accept JSON, CSV, and Excel (`.xlsx` plus existing safe `.xls` support), producing the full nine-combination matrix. Selecting a return type never changes or disables a format. The uploaded extension must match the selected format; mismatch returns the `Invalid File Format` warning before parsing.

Parsing is split into two stages. Format readers (`parse_json`, `parse_csv`, `parse_excel`) decode source structure into neutral source records and metadata. Return mappers (`map_gstr1`, `map_gstr2a`, `map_gstr2b`) apply shared aliases and known return-specific nested-shape extraction to produce the canonical invoice contract. Downstream party, master, voucher, correction, export, duplicate, and Tally logic consumes canonical data and never branches on file extension.

A single alias registry covers GSTIN, party name, invoice identity/date, taxable value, rate, tax components, cess, charges, totals, state/place of supply, filing data, and supported variants. JSON readers accept existing portal-style nested structures, flat arrays, and application-normalized objects for the selected return type; unsupported structures return a return-specific diagnostic rather than an empty generic import.

`source_format` is retained as audit metadata. Duplicate identity uses semantic normalized content with company GSTIN, return type, and tax period, so changing filename or serializing equivalent accounting data as another supported format does not evade duplicate detection.

## Step 2 field validation and correction

Immediately after upload normalization, the backend validates every invoice and returns structured `field_errors` keyed by canonical field name. Each error contains a stable code, direct message, expected and actual values where applicable, and an editable flag. Cross-field validation is attributed to the legitimate correction field: for example, a total mismatch marks `invoice_value`, not the whole row.

The preview grid remains read-only by default. Only blocking fields identified as editable receive the invalid border/tooltip and an appropriate editor. Clicking an invalid cell enters cell-local edit mode with explicit Save and Cancel controls. Currency editors accept signed decimal values with at most two decimal places, dates use a date editor, GSTIN text is normalized uppercase, and GST rates use the supported-rate editor. Cancel restores the current persisted value without changing validation. Save sends one field/value pair to the correction API; no keystroke writes to Tally or any downstream stage.

Saving an allowlisted correction updates the canonical invoice data, reruns complete affected-invoice validation, recalculates dependent GST/state/raw total/Round Off/rounded total fields, and returns the updated invoice and validation state. An incorrect correction remains invalid and editable with the returned precise diagnostic. A corrected cell that passes returns to ordinary read-only styling with no permanent success color. When no blocking fields remain, row status changes from `Validation Failed` to `Ready`; other invalid cells remain independently editable until corrected.

The allowlist covers invoice number/date, party source name, GSTIN, GST rate, taxable value, CGST, SGST, IGST, cess, other charges, invoice value, and state/place of supply. Arbitrary model fields cannot be modified. GST amounts are not silently altered merely to force a total match; expected dependent values and errors are recalculated. A GSTIN change recalculates state and intra/inter-state expectations and invalidates downstream prepared state.

Original parsed values remain in immutable `source_line` data. A correction audit record stores invoice, field, original value, corrected value, timestamp, and user where available. The source fingerprint remains the original upload identity; voucher idempotency is derived from final corrected canonical values.

Error tooltips expose invoice-specific calculations rather than a generic row error, including calculated total, source total, and difference for invoice-value mismatches. Unresolved blocking errors prevent Step 3 navigation and return the requested correction warning. Once all blocking errors pass, normal navigation resumes without re-uploading.

## Step 2 report exports

Invoice Preview gains `Download Excel` and `Download PDF` actions without adding a workflow step. Both are read-only backend reports generated from the current corrected canonical batch data. Export never creates masters/vouchers, changes validation/import state, or calls Tally.

Excel uses `openpyxl` with report metadata, bold headers, widths, number/date formats, and logical preview columns. PDF uses ReportLab in landscape orientation with repeated headers, readable sizing, report metadata, and page numbers. Neither includes React or internal metadata.

When blocking errors remain, exports are labeled `Draft – Validation Pending` and include validation messages. Fully valid exports omit validation warnings and special error styling.

## Canonical normalized JSON

The common format-reader/return-mapper output and `normalized_vouchers()` pipeline remain the entry points. A dedicated contract module makes the common shape explicit and validates it before builders consume it.

Every normalized voucher contains return type, voucher type, `voucher_mode="Accounting Invoice"`, invoice identity, company identity, party model, party/account groups, rate allocations, tax allocations, `other_charges`, `raw_invoice_total`, `round_off`, `rounded_invoice_total`, source-line identities, validation state, and stable idempotency material. Decimal values remain deterministic strings at serialization boundaries.

Every normalized master contains its semantic type and verified properties. Party masters contain parent, state, country, pincode, registration type, and GSTIN. Account ledgers contain parent, taxability, GST rate, and supply type. Tax ledgers contain GST duty type, Tally tax classification, percentage, and rounding method.

## Return-type resolver

One resolver is the only source of accounting-flow decisions:

| Return | Account | Taxes | Party parent | Voucher |
| --- | --- | --- | --- | --- |
| GSTR1 | GST Sales / Sales Accounts | CGST+SGST or IGST | Sundry Debtors | Sales Accounting Invoice |
| GSTR2A | GST Purchase / Purchase Accounts | InputCGST+InputSGST or InputIGST | Sundry Creditors | Purchase Accounting Invoice |
| GSTR2B | GST Purchase / Purchase Accounts | InputCGST+InputSGST or InputIGST | Sundry Creditors | Purchase Accounting Invoice |

Intra/inter-state is determined by comparing the validated company GSTIN state code with the party GSTIN state code. The complete active GST state/UT code mapping is centralized. GST rates 5, 12, 18, and 28 map to half-rate CGST/SGST for intra-state and full-rate IGST for inter-state.

## Ledger identity and reuse

Ledger matching removes case, whitespace, `@`, and formatting whitespace around `%`, then extracts a decimal percentage. A normalized name only identifies candidates. Reuse additionally requires matching parent group and semantic properties:

- Sales/Purchase: GST rate, Taxable taxability, and parent group.
- Tax: Duties & Taxes parent, GST duty type, valid Tally tax classification, and percentage.
- Party: required debtor/creditor parent, GSTIN/registration data, and state where available.

If a similar ledger is misconfigured, it is not reused and is surfaced as a conflict; the system does not silently create a confusing equivalent duplicate.

## Source fingerprints and voucher idempotency

After parsing and removal of empty rows, meaningful normalized source rows are serialized with stable key ordering and decimal/date normalization. SHA-256 is computed without filename metadata. Duplicate-file scope is company GSTIN, return type, tax period, and fingerprint.

A batch is blocked only when an earlier batch with the same scope reached a confirmed successful/verified outcome. Renaming unchanged content remains a duplicate; changed content with the same filename remains valid.

Voucher keys include company GSTIN, return type, voucher type, party GSTIN, invoice number, and invoice date. A local mapping is only a reconciliation hint. Before `Already Imported`, the current open company and the actual Tally voucher are queried. A stale local mapping becomes `Missing in Tally`, then the voucher is imported once and the mapping is refreshed.

No local database primary key is exposed or interpreted as a Tally voucher identifier. The UI identifier is blank unless Tally query verification returns a GUID, Master ID, Alter ID, voucher ID, or voucher number with its identifier type recorded. Existing ambiguous identifier values are treated as untrusted during reconciliation.

Reconciliation first verifies expected/current company name, GSTIN, and Tally period. It then queries the current company directly for Purchase/Sales vouchers across the relevant date range without filtering through local mappings. Diagnostics include voucher number, date, reference, party, GUID, Master ID, Alter ID, voucher type, and amount. Each mapping is matched using the strongest available identity in order: GUID, Master ID, Alter ID, voucher number+type+date, then reference+party+date. A search across other voucher types detects a wrong-type write but does not classify it as correctly imported.

Stale mappings are retained for audit and marked `Missing in Tally`; they are not blindly deleted. Safe re-import re-verifies masters, writes once, parses Tally counters/errors, and requires query-back before changing the mapping to `Imported`.

## Transport architecture

`TallyTransport` exposes master write, voucher write, master query, voucher query, and response parsing. `XmlTallyTransport` uses the existing HTTP/XML client and dedicated ElementTree builders. `JsonTallyTransport` is selected only for an operation with demonstrated native support. Otherwise it delegates to XML and returns metadata:

- requested transport
- actual transport
- fallback reason

`TALLY_WRITE_FORMAT` accepts XML or JSON. Unsupported values fail configuration validation. XML is the default. Selecting JSON never implies success or native support.

## Tally XML

Dedicated serialized builders cover party, sales, purchase, tax, voucher, ledger query, and voucher query payloads. Source text is always escaped by ElementTree. Sales and Purchase vouchers use ledger allocations, `PERSISTEDVIEW=Accounting Invoice View`, `OBJVIEW=Accounting Invoice View`, and `ISINVOICE=Yes`; inventory allocations are not generated.

Master creation follows account ledger, applicable tax ledgers, party ledger, then verification. Vouchers are not built for import until every required master is verified in the current Tally company.

## Final round-off calculation

Round Off is computed in Step 5 and imported in Step 6 for both Sales and Purchase Accounting Invoices. Python `Decimal` with explicit `ROUND_HALF_UP` rounding is mandatory; binary floating point and Python's built-in banker's `round()` are not used.

The calculation order is:

```text
Raw Invoice Total = Taxable Value + CGST + SGST + IGST + Cess + Other Charges
Rounded Invoice Total = Raw Invoice Total rounded to the nearest whole rupee
Round Off = Rounded Invoice Total - Raw Invoice Total
Voucher Total = Raw Invoice Total + Round Off
```

A fractional amount below 0.50 rounds down; 0.50 or above rounds up. `other_charges` remains a separate non-taxable voucher component unless a distinct source rule explicitly marks it taxable. Round Off is also a separate non-GST component and is always the final balancing adjustment; no taxable or GST component is changed.

The `Round Off` master is normalized by ignoring case, whitespace, and hyphens, so `Round Off`, `RoundOff`, `ROUND OFF`, and `Round-off` are reuse candidates. Reuse requires a valid non-GST ledger. A new Round Off ledger is created only when no valid candidate exists and carries no GST rate, duty head, or taxable classification.

Voucher builders derive the accounting sign from both voucher type and adjustment sign. All four combinations—Sales positive/negative and Purchase positive/negative—must balance. Zero adjustments omit the ledger allocation and Step 5 does not show a misleading zero line.

Validation compares raw total, rounded total, and source invoice value. A source value matching the legitimate rounded total is valid. Existing mismatch validation remains for values matching neither legitimate amount within the configured tolerance.

## Verification and persistence

Before Step 4 writes and Step 6 imports, the expected company GSTIN must match the currently open Tally company. HTTP 200 is insufficient. Each write requires:

1. parsed Tally response with positive created/altered confirmation and no errors, exceptions, ignored, cancelled, deleted, or line error condition;
2. follow-up query in the same company;
3. property/identity match;
4. persistence of confirmed identifiers.

Voucher mappings store GUID, Master ID, Alter ID, voucher number, reference, voucher type, company GSTIN, requested/actual transport, verification status/time, and diagnostic response. Existing data is preserved through additive nullable/defaulted fields.

Structured diagnostics record the local mapping ID explicitly as local-only, stored Tally identifiers, voucher identity/date, expected/current company and GSTIN, current/query period, target `SVCURRENTCOMPANY`, master verification, sanitized request/response counters, query result, and final reconciliation state. Credentials and provider secrets are never logged.

## Outcome contract

The backend returns real counters: total, eligible, imported, already_imported, waiting_for_tally_period, unknown, skipped, not_attempted, failed, and missing_in_tally. `status` is one of `success`, `already_verified`, `partial_success`, or `failed`; `title` and `message` are generated from the same outcome object.

- `success`: new verified imports and no unresolved eligible failures.
- `already_verified`: every eligible voucher was verified already present.
- `partial_success`: at least one eligible voucher completed, with unresolved vouchers.
- `failed`: no eligible voucher was imported or verified.

Both the center result and toast render this contract. They do not independently derive success.

## Error handling

Company mismatch stops all writes. Duplicate files stop before master/voucher creation. Master conflicts stop affected vouchers. Invalid source records preserve current validation behavior. Empty trailing rows are ignored. Invoice total is taxable plus CGST, SGST, IGST, cess, other charges, and the final round-off; invoice-level values are applied once. Genuine duplicate source lines are removed using stable line identity while distinct item lines remain.

## Compatibility and scope

Existing endpoints, parsers, models, React page, and six components remain. New bounded correction and export routes support Step 2 without changing existing route behavior. Changes remain focused on service boundaries, builders, reconciliation, additive persistence, field-level preview behavior, and result rendering. No unrelated refactor is included.

## Verification

Unit and integration tests use deterministic fake Tally responses and query results. Backend and frontend suites must pass. If live TallyPrime is available, one party, account ledger, tax ledger, and voucher are written and queried from the current company. Otherwise the final report states `Live Tally proof pending`.
