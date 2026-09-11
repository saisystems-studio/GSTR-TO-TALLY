import assert from 'node:assert/strict'
import test from 'node:test'

import { licenseFailureUi } from './licenseSecurityUi.js'

test('serial mismatch has no bypass action', () => {
  const ui = licenseFailureUi({
    verification_result: 'TALLY_SERIAL_MISMATCH',
    registered_tally_serial: '735149529',
    detected_tally_serial: '845621773',
  })

  assert.equal(ui.title, 'Tally License Mismatch')
  assert.deepEqual(ui.actions, ['Contact Administrator'])
  assert.match(ui.detail, /735149529/)
  assert.match(ui.detail, /845621773/)
})

test('a license with no registered Tally Serial requires setup, never a retry -- even wrapped in a pre-import failure', () => {
  const ui = licenseFailureUi({
    code: 'PRE_IMPORT_LICENSE_FAILED',
    verification_result: 'PRODUCT_LICENSE_NOT_CONFIGURED',
  })

  assert.equal(ui.title, 'License Setup Required')
  assert.deepEqual(ui.actions, ['Contact Administrator'])
})

test('pre-import failure says no vouchers were sent and offers recheck', () => {
  const ui = licenseFailureUi({
    code: 'PRE_IMPORT_LICENSE_FAILED',
    verification_result: 'TALLY_SERIAL_MISMATCH',
    registered_tally_serial: '735149529',
    detected_tally_serial: '845621773',
  })

  assert.equal(ui.title, 'Import Blocked')
  assert.deepEqual(ui.actions, ['Recheck Tally', 'Contact Administrator'])
  assert.match(ui.detail, /No vouchers were sent to Tally/)
})
