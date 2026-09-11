// Backend Step 6 result carries import_status ('Import Verified' | 'Import
// Successful' | 'Partial Import' | 'Import Failed') and a message that names
// each distinct outcome (Imported / Already Imported / Validation Failed /
// Tally Failed / Skipped / Not Attempted) instead of a single generic failure
// count. Prefer that when present; the status/imported/failed fallback below
// exists for older callers.
export function finalImportToast({ status, imported = 0, failed = 0, import_status, message }) {
  // Every eligible voucher was already verified present in Tally -- nothing new
  // was imported, so this is distinct from a fresh "Import Successful".
  if (import_status === 'Import Verified') return { kind: 'success', title: '✅ Already Imported', message: message || `${imported} vouchers are already present in Tally. No duplicate vouchers were created.` }
  if (import_status === 'Import Successful') return { kind: 'success', title: '✅ Import Successful', message: message || `${imported} vouchers imported successfully into Tally.` }
  if (import_status === 'Partial Import') return { kind: 'warning', title: '⚠️ Partial Import', message: message || `${imported} vouchers imported successfully. ${failed} vouchers require attention.` }
  // Tally already accepted these voucher writes (CREATED/ALTERED>0) -- the
  // query-back just hasn't confirmed them yet. This is never a rejection and
  // must never read as "Import Failed" (see service.py's
  // WRITE_ACCEPTED_PENDING_STATUS / task spec Case B) -- doing so would
  // invite exactly the accidental-resend/duplicate-voucher risk this status
  // exists to prevent.
  if (import_status === 'Write Accepted - Verification Pending') return { kind: 'warning', title: '⚠️ Verification Pending', message: message || 'Tally accepted the voucher write, but verification is pending. Do not resend -- recheck Tally before retrying.' }
  if (import_status === 'Import Failed') return { kind: 'error', title: '❌ Import Failed', message: message || 'No vouchers were imported into Tally. Please check the validation errors.' }
  if (status === 'success') return { kind: 'success', title: '✅ Import Successful', message: `${imported} vouchers imported successfully into Tally.` }
  if (status === 'partial_success') return { kind: 'warning', title: '⚠️ Import Completed', message: `${imported} vouchers imported successfully. ${failed} vouchers require attention.` }
  return { kind: 'error', title: '❌ Import Failed', message: 'No vouchers were imported into Tally. Please check the validation errors.' }
}
