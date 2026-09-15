# Step 3 verification results

## Implemented

- Compare uploaded batch GSTIN with detected Tally GSTIN; company name is informational.
- Return detected and registered serials, independent product/device capacity and authorization, all errors, and an aggregate `ready` flag.
- Block device-limit bypasses and reuse existing device registrations without duplicates.
- Add configurable `Subscription.allowed_products`, editable in Django subscription admin. New subscriptions default to one product; the migration preserves capacity for existing registered products.
- Preserve the existing first-product recovery rule within subscription capacity. Verification never replaces an existing serial; additional products still require existing administrative registration.
- Show a blue verification popup with a comparison table, separate correct/action-required sections, every error, and confirmation gated on all six checks plus saved company details.
- Run party enrichment without delaying company/license verification. Display independent device processing status.

## Acceptance tests

These tests use the real Django models/services with simulated Tally responses and an isolated SQLite test database. Frontend readiness tests verify that each failed check disables the confirmation condition. A live Tally/browser walkthrough was not performed.

| Case | Scenario | Observed result | Result |
| --- | --- | --- | --- |
| A | Correct GSTIN, serial and authorized device | Ready; no errors | PASS |
| B | Wrong GSTIN, correct serial | COMPANY_GSTIN_MISMATCH; blocked | PASS |
| C | Correct GSTIN, wrong serial | TALLY_SERIAL_MISMATCH; both serials returned; blocked | PASS |
| D | Wrong GSTIN and serial | Both errors returned; blocked | PASS |
| E | Correct GSTIN/serial, new device at capacity | DEVICE_LIMIT_REACHED; no new device; blocked | PASS |
| F | New serial at product capacity | PRODUCT_LIMIT_REACHED; registered serial preserved; blocked | PASS |
| G | Repeat verification with same serial/device | No increase in product or device counts | PASS |
| H | Matching GSTIN with different company name | Ready | PASS |
| I | Same serial, another company with wrong GSTIN | Serial passes; COMPANY_GSTIN_MISMATCH; blocked | PASS |

Additional coverage: free device capacity, revoked devices, missing source GSTIN, GSTIN normalization, missing registered serial, and frontend independent-check gating.

## Validation

- Backend: **57 passed** across `test_step3_verification`, `test_product_license_security`, `test_verify_tally_company_name`, and `test_tally_license_reader`.
- Frontend: **36 passed** across `step3Verification.test.js`, `workflowState.test.js`, and `SuperAdminApp.static.test.mjs`.
- `npm.cmd run build`: passed.
- Django migration consistency check: no missing migrations.
- `subscriptions.0003_subscription_allowed_products`: applied successfully to the configured local SQL Server database.

## Files changed for this task

### Backend

- `backend/gst_tally/services/company_verification.py`
- `backend/gst_tally/services/product_license.py`
- `backend/gst_tally/tests/test_product_license_security.py`
- `backend/gst_tally/tests/test_step3_verification.py` (new)
- `backend/subscriptions/models.py`
- `backend/subscriptions/admin.py`
- `backend/subscriptions/migrations/0003_subscription_allowed_products.py` (new)

### Frontend

- `frontend/src/pages/GstTallyImport.jsx`
- `frontend/src/components/gst-tally/Step3Verification.jsx` (new)
- `frontend/src/utils/step3Verification.js` (new)
- `frontend/src/utils/step3Verification.test.js` (new)
- `frontend/src/utils/workflowState.js`
- `frontend/src/utils/workflowState.test.js`
- `frontend/src/styles/gst-tally.css`
- `frontend/src/superadmin/SuperAdminApp.static.test.mjs` (Step 3 assertions)

Other modifications already present in the workspace were preserved.
