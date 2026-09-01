import assert from 'node:assert/strict'
import test from 'node:test'

import {
  authModalShouldOpen,
  fetchPartyDetailsAvailability,
  lookupAvailability,
  normalizeSandboxError,
} from './partyAuthUi.js'

test('GST authentication modal stays closed until a user explicitly requests it', () => {
  assert.equal(authModalShouldOpen(false, { configured: true, taxpayer_session_active: false }), false)
  assert.equal(authModalShouldOpen(true, { configured: true, taxpayer_session_active: false }), true)
})

test('Sandbox error message is displayed once without adding a duplicate prefix', () => {
  assert.equal(normalizeSandboxError({ message: 'OTP request failed: Sandbox rejected the GST portal username.' }),
    'Sandbox rejected the GST portal username.')
  assert.equal(normalizeSandboxError({ message: 'Sandbox rejected the GST portal username or taxpayer session.' }),
    'Sandbox rejected the GST portal username or taxpayer session.')
})

test('sandbox provider not yet configured is not reported ready', () => {
  assert.deepEqual(lookupAvailability(
    { enabled: true, configured: true, provider: 'sandbox', lookup_ready: false }
  ), { ready: false, message: 'GST Sandbox is not configured for GSTIN lookup.' })
})

test('sandbox provider is ready from application configuration alone, without any taxpayer session', () => {
  assert.deepEqual(lookupAvailability(
    { enabled: true, configured: true, provider: 'sandbox', session_active: false, session_required: false, otp_required: false, lookup_ready: true }
  ), { ready: true, message: 'GST Lookup ready.' })
})

test('configured direct provider is lookup ready without any sandbox status fields', () => {
  assert.deepEqual(lookupAvailability(
    { enabled: true, configured: true, provider: 'jamku' }
  ), { ready: true, message: 'GST Lookup ready.' })
})

test('Fetch Party Details remains enabled for unresolved valid GSTINs when authentication is required', () => {
  const rows = [
    { gstin: '27ABCDE1234F1Z5', tally_ready: false, status: 'Sandbox Session Required' },
    { gstin: '29ABCDE1234F1Z1', tally_ready: true, status: 'Existing' },
    { gstin: 'invalid', tally_ready: false, status: 'Invalid' },
  ]

  assert.deepEqual(fetchPartyDetailsAvailability(rows, false), { disabled: false, unresolvedCount: 1 })
  assert.deepEqual(fetchPartyDetailsAvailability(rows, true), { disabled: true, unresolvedCount: 1 })
})

test('Fetch Party Details is disabled when no valid unresolved GSTIN remains', () => {
  const rows = [
    { gstin: '27ABCDE1234F1Z5', tally_ready: true, status: 'Existing' },
    { gstin: 'invalid', tally_ready: false, status: 'Invalid' },
  ]

  assert.deepEqual(fetchPartyDetailsAvailability(rows, false), { disabled: true, unresolvedCount: 0 })
})
