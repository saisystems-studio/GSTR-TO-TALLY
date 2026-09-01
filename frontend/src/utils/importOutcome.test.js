import test from 'node:test'
import assert from 'node:assert/strict'

import { finalImportToast } from './importOutcome.js'

test('success toast uses imported count', () => {
  assert.deepEqual(finalImportToast({ status: 'success', imported: 4, failed: 0 }), {
    kind: 'success', title: '✅ Import Successful',
    message: '4 vouchers imported successfully into Tally.',
  })
})

test('partial success is a warning rather than a generic error', () => {
  assert.deepEqual(finalImportToast({ status: 'partial_success', imported: 3, failed: 2 }), {
    kind: 'warning', title: '⚠️ Import Completed',
    message: '3 vouchers imported successfully. 2 vouchers require attention.',
  })
})

test('failed toast directs the user to validation errors', () => {
  assert.deepEqual(finalImportToast({ status: 'failed', imported: 0, failed: 2 }), {
    kind: 'error', title: '❌ Import Failed',
    message: 'No vouchers were imported into Tally. Please check the validation errors.',
  })
})

test('backend import_status and message take precedence when present', () => {
  const message = '25 vouchers imported successfully. 1 was already present in Tally. 2 require data correction and 1 was rejected by Tally.'
  assert.deepEqual(finalImportToast({ status: 'partial_success', imported: 26, failed: 3, import_status: 'Partial Import', message }), {
    kind: 'warning', title: '⚠️ Partial Import', message,
  })
})

test('Import Successful is never shown when the backend reports a Tally failure', () => {
  const result = finalImportToast({ status: 'partial_success', imported: 25, failed: 1, import_status: 'Partial Import', message: 'x' })
  assert.notEqual(result.title, '✅ Import Successful')
})

test('all-already-verified outcome is distinct from a fresh import success', () => {
  const message = '28 vouchers are already present in Tally. No duplicate vouchers were created.'
  assert.deepEqual(finalImportToast({ status: 'success', imported: 0, failed: 0, import_status: 'Import Verified', message }), {
    kind: 'success', title: '✅ Already Imported', message,
  })
})
