import test from 'node:test'
import assert from 'node:assert/strict'

import {
  normalizeContactNumber,
  normalizeLoginIdentifier,
  normalizePhoneNumber,
  validateLogin,
  validateRegistration,
} from './authForms.js'

test('normalizeContactNumber keeps exactly the first 10 digits', () => {
  assert.equal(normalizeContactNumber('+91 98765-43210 99'), '9198765432')
})

test('normalizePhoneNumber strips a +91 country code down to 10 digits', () => {
  assert.equal(normalizePhoneNumber('+91 9876543210'), '9876543210')
  assert.equal(normalizePhoneNumber('91 9876543210'), '9876543210')
  assert.equal(normalizePhoneNumber('98765 43210'), '9876543210')
  assert.equal(normalizePhoneNumber('9876543210'), '9876543210')
  assert.equal(normalizePhoneNumber('09876543210'), '9876543210')
})

test('normalizePhoneNumber rejects alphabets and leaves incomplete numbers incomplete', () => {
  assert.equal(normalizePhoneNumber('98765abcde'), '98765')
  assert.equal(normalizePhoneNumber('98765'), '98765')
})

test('validateRegistration requires the requested registration fields', () => {
  const errors = validateRegistration({
    full_name: '',
    email: 'bad',
    contact_number: '123',
    password: 'secret',
    confirm_password: 'different',
  })

  assert.equal(errors.full_name, 'Full name is required.')
  assert.equal(errors.email, 'Enter a valid email address.')
  assert.equal(errors.contact_number, 'Contact number must contain exactly 10 digits.')
  assert.equal(errors.username, undefined)
  assert.equal(errors.confirm_password, 'Passwords do not match.')
})

test('validateRegistration accepts a complete matching password registration with no username field', () => {
  assert.deepEqual(validateRegistration({
    full_name: 'Sai Dev',
    email: 'sai@example.com',
    contact_number: '9876543210',
    password: 'secret123',
    confirm_password: 'secret123',
  }), {})
})

test('validateLogin requires an email-or-phone identifier and a password', () => {
  assert.deepEqual(validateLogin({ identifier: '', password: '' }), {
    identifier: 'Enter your email or phone number.',
    password: 'Password is required.',
  })
  assert.deepEqual(validateLogin({ identifier: '98765', password: 'secret' }), {
    identifier: 'Enter a valid email address or 10-digit phone number.',
  })
  assert.deepEqual(validateLogin({ identifier: 'not-an-email@', password: 'secret' }), {
    identifier: 'Enter a valid email address.',
  })
  assert.deepEqual(validateLogin({ identifier: '9876543210', password: 'secret', remember: true }), {})
  assert.deepEqual(validateLogin({ identifier: 'sai@example.com', password: 'secret' }), {})
})

test('normalizeLoginIdentifier normalizes phone numbers but lowercases emails as-is', () => {
  assert.equal(normalizeLoginIdentifier('+91 98765 43210'), '9876543210')
  assert.equal(normalizeLoginIdentifier('Sai@Example.com'), 'sai@example.com')
})
