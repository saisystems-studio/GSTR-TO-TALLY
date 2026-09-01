# GSTR to Tally Final Architecture Design

## Purpose

Implement the final GSTR-to-Tally business rules inside the existing six-step UI workflow. Excel, CSV, and JSON uploads become one normalized JSON representation; Tally writes use a transport adapter with XML as the reliable default; and no master or voucher is reported successful until it is queried back from the currently open, GSTIN-matched Tally company.

## Fixed UI Workflow

The application retains exactly these steps and their current order, names, and layout:

1. Upload
2. Invoice Preview
3. Party Details
4. Tally Masters
5. Voucher Preview
6. Import to Tally

JSON normalization, XML conversion, ledger resolution, duplicate detection, and post-write verification remain backend operations within those steps. No new workflow step is introduced.

## Architectural Approach

The implementation extends the existing parsers, Django models and endpoints, Tally client, and React result components. It introduces focused normalization, business-rule, fingerprint, payload-builder, transport, and verification boundaries without rewriting unrelated functionality.

The canonical flow is:

```text
Excel / CSV / JSON
  -> source parser
  -> canonical normalized JSON
  -> validation and source-line deduplication
  -> file and voucher duplicate checks
  -> return-type, state, ledger, and tax resolution
  -> normalized master JSON models
  -> normalized Accounting Invoice voucher JSON
  -> configured Tally transport adapter
  -> XML, or native JSON only when genuinely supported
  -> parsed Tally response
  -> query current Tally company
  -> verified local persistence
  -> one backend import outcome
```

## Canonical JSON Models

Normalized invoice and voucher JSON is the source of truth for preview, validation, persistence, logging, tests, and transport generation. Its stable fields include return type, voucher type and mode, company identity, invoice identity, party details, rate allocations, account ledgers, tax allocations, totals, source-line identities, and idempotency metadata.

Normalized master JSON has explicit variants:

- `sales_ledger`: Sales Accounts, taxable, source GST rate, Goods.
- `purchase_ledger`: Purchase Accounts, taxable, source GST rate, Goods.
- `tax_ledger`: Duties & Taxes, GST duty type, valid Tally tax classification, exact percentage, no rounding.
- `party`: return-type-specific parent, resolved registration, state, country, pincode, and GSTIN.

Source party names follow this priority: fetched trade/party name, source party name, then a valid GSTIN. Pincode follows GST lookup, source data, then a verified existing Tally party ledger. No pincode is invented.

## Return-Type Routing

One resolver owns the accounting separation:

- GSTR-1: Sales voucher, Sales Accounts, output CGST/SGST/IGST, Sundry Debtors.
- GSTR-2A and GSTR-2B: Purchase voucher, Purchase Accounts, input CGST/SGST/IGST, Sundry Creditors.

All vouchers use `voucher_mode = Accounting Invoice`. Ledger entries are used; inventory allocations are not inferred.

Supported GST rates are 5, 12, 18, and 28 percent. Intra-state rates split equally into CGST and SGST: 2.5, 6, 9, and 14 percent. Inter-state IGST uses the full rate: 5, 12, 18, or 28 percent. Purchase flows prefix tax ledger names with `Input`.

## State and Registration Resolution

A complete valid GST State/UT code map resolves GSTIN prefixes to Tally-compatible state names. Intra/inter-state is determined by comparing the resolved company state code with the resolved party state code. Tamil Nadu is not hardcoded as the universal company or party state.

A valid GSTIN produces `Regular` registration and the actual GSTIN. An absent GSTIN produces `Unregistered/Consumer` with a blank GSTIN. A state selection is required only when neither a valid GSTIN nor a reliable source state exists.

## Ledger Matching and Master Creation

Ledger-name matching ignores case, whitespace, `@`, and optional whitespace before `%`, and extracts decimal rates. A normalized name is only a candidate: reuse also requires verified Tally properties.

- Sales/Purchase candidates must match parent group, GST rate, taxable status, and supply type where available.
- Tax candidates must match Duties & Taxes, GST duty type, tax type, and percentage.
- Party candidates must match the return-type parent and registration identity.

Wrongly configured similarly named ledgers are never reused. Missing masters are created in account-ledger, tax-ledger, then party-ledger order and queried back before the workflow can advance.

## Tally Builders and Transport

Dedicated serializers build party, sales, purchase, tax, Sales voucher, Purchase voucher, ledger query, and voucher query payloads. XML uses an XML serializer rather than raw string interpolation for source values.

`TallyTransport` exposes write and query operations. `XmlTallyTransport` is the reliable implementation. `JsonTallyTransport` is selected only for operations with confirmed native support. With `TALLY_WRITE_FORMAT=JSON`, unsupported operations fall back to XML and record:

```text
Requested transport: JSON
Actual Tally transport: XML
Reason: Native JSON transport not supported for this operation
```

Selecting JSON never fakes a native JSON write and never requires manual conversion.

## Accounting Invoice Payload

Sales and Purchase vouchers use their correct voucher type with `OBJVIEW` and `PERSISTEDVIEW` set to the Tally Accounting Invoice view and the invoice flag set to the valid value required for Accounting Invoice behavior. Party, account, and tax ledger allocations carry accounting signs appropriate to Sales or Purchase. No Item Invoice allocations are emitted.

## Duplicate Protection

At upload, meaningful normalized source content is serialized deterministically and hashed with SHA-256. Duplicate scope is company GSTIN, return type, tax period, and normalized fingerprint. Filename is excluded. A completed matching import blocks further processing before masters or vouchers are created; renamed identical files remain duplicates, while changed contents remain eligible.

Each voucher also has a deterministic key based on company GSTIN, return type, voucher type, party GSTIN, invoice number, invoice date, and stable disambiguating values where required. This protects retries and partial batches.

## Verification and Persistence

The currently open Tally company GSTIN must equal the expected company GSTIN before master reconciliation or voucher writes. A mismatch stops the operation.

HTTP 200 is transport success only. A write is successful only when the Tally response contains no errors and the created object is queried back from the current company. Confirmed voucher persistence records available GUID, Master ID, Alter ID, voucher number, reference, voucher type, company GSTIN, actual transport, and raw diagnostic responses.

Local mappings are hints, not proof. Every apparent prior import is checked against current Tally using the strongest available identifiers plus voucher number/reference, type, date, and party. A verified match becomes `Already Imported`. A stale mapping becomes `Missing in Tally` and is eligible for one safe re-import. The refreshed mapping is saved only after query-back verification.

## Validation and Calculations

Completely blank source rows are discarded before invoice validation. Source-line identities include enough original fields and row identity to remove genuinely repeated persisted lines while preserving legitimate equal-valued item lines.

Invoice total is taxable value plus CGST, SGST, IGST, cess, and invoice-level round-off. Round-off and invoice-level charges are applied once. Existing validation is preserved, with an absolute source-total tolerance of Rs. 1.00.

## Import Outcome Contract

The backend returns real counters for total, eligible, imported, already imported, waiting for period, unknown, skipped, not attempted, failed, and missing in Tally where applicable. Definitions are mutually exclusive.

One `status` field drives both the center result and toast and uses only the required values `success`, `partial_success`, and `failed`:

- `success`: all eligible vouchers completed without genuine failures, whether newly imported or already verified.
- `partial_success`: some eligible vouchers completed or were verified and some require attention.
- `failed`: no eligible voucher was imported or verified.

Within `success`, the counters determine the presentation. If every eligible voucher is already verified and `imported` is zero, the center and toast both show `Import Verified` / `Already Imported`; otherwise they both show `Import Successful`.

Frontend copy is derived from this same result. The existing visual workflow is unchanged.

## Failure Handling

Company mismatch, invalid normalized data, master verification failure, Tally response errors, query-back failure, and transport failure remain explicit row/batch failures. A voucher is not attempted until all masters it requires are verified. A duplicate source file creates no masters or vouchers. Partial success preserves successful verified results while surfacing remaining attention items.

## Testing Strategy

Implementation follows test-driven development. Unit tests cover all parsers, empty rows, normalized JSON, name/rate normalization, complete state mapping, return routing, tax splits, parties, totals, source deduplication, fingerprints, voucher keys, JSON builders, XML builders/parsers, Accounting Invoice flags, and transport fallback.

Integration tests use controlled Tally responses for company mismatch, existing/missing masters, successful/failed writes, existing/missing vouchers, stale mappings, retry idempotency, and outcome status consistency. Frontend tests assert matching center and toast semantics for success, already verified, partial, and failed outcomes.

When live Tally is available, an end-to-end check creates or reuses one party, one GST account ledger, one tax ledger, and one voucher, then queries them from the current company. When it is unavailable, automated implementation continues and the final report states `Live Tally proof pending`.

## Scope Boundaries

The work does not redesign the six-step UI, create inventory masters, alter unrelated authentication or GST-provider code, create or alter a Tally company, hardcode dynamic counters, trust HTTP status alone, or report frontend-only success.
