import test from 'node:test'
import assert from 'node:assert/strict'
import { step3Ready, step3Rows } from './step3Verification.js'

const good = () => ({ ready: true, tally_connected: true, company: { gstin_match: true }, tally_license: { match: true, registered_serial: 'ABC', detected_serial: 'ABC' }, product: { allowed: true, limit: 1, used: 1 }, device: { authorized: true, limit_allowed: true, limit: 2, used: 1 }, errors: [] })
test('confirmation requires all six independent checks', () => {
  assert.equal(step3Ready(good()), true)
  for (const [section, field] of [['company', 'gstin_match'], ['tally_license', 'match'], ['product', 'allowed'], ['device', 'authorized'], ['device', 'limit_allowed']]) {
    const result = good()
    result[section][field] = false
    assert.equal(step3Ready(result), false)
  }
  assert.equal(step3Ready({ ...good(), tally_connected: false }), false)
  assert.equal(step3Ready({ ...good(), errors: [{ code: 'BLOCKED' }] }), false)
  assert.equal(step3Ready({ license_verified: true }), false)
})
test('table exposes both serials and separate real usage counts', () => {
  const rows = step3Rows(good())
  assert.deepEqual(rows[2].slice(1, 3), ['ABC', 'ABC'])
  assert.equal(rows[3][2], '1/1 Used')
  assert.equal(rows[5][2], '1/2 Used')
})
