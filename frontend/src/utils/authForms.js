const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function normalizeContactNumber(value = '') {
  return String(value).replace(/\D/g, '').slice(0, 10)
}

// Login phone numbers may arrive with a +91/91 country code or spacing
// (e.g. "+91 9876543210", "98765 43210"); normalize to the bare 10-digit
// number so validation and the API always see the same shape.
export function normalizePhoneNumber(value = '') {
  let digits = String(value).replace(/\D/g, '')
  if (digits.length > 10 && digits.startsWith('91')) digits = digits.slice(-10)
  else if (digits.length === 11 && digits.startsWith('0')) digits = digits.slice(1)
  return digits.slice(0, 10)
}

export function validateRegistration(values) {
  const errors = {}
  if (!String(values.full_name || '').trim()) errors.full_name = 'Full name is required.'
  if (!emailPattern.test(String(values.email || '').trim())) errors.email = 'Enter a valid email address.'
  if (!/^\d{10}$/.test(String(values.contact_number || ''))) errors.contact_number = 'Contact number must contain exactly 10 digits.'
  if (!String(values.password || '')) errors.password = 'Password is required.'
  else if (String(values.password).length < 8) errors.password = 'Password must be at least 8 characters.'
  if (String(values.password || '') !== String(values.confirm_password || '')) errors.confirm_password = 'Passwords do not match.'
  return errors
}

export function validatePasswordReset(values) {
  const errors = {}
  if (!String(values.new_password || '')) errors.new_password = 'Password is required.'
  else if (String(values.new_password).length < 8) errors.new_password = 'Password must be at least 8 characters.'
  if (String(values.new_password || '') !== String(values.confirm_password || '')) errors.confirm_password = 'Passwords do not match.'
  return errors
}

// The login identifier accepts either an email address or a phone number.
// "@" present -> treat as email; otherwise treat as a phone number and
// normalize away +91/spacing/leading-0 before checking it is 10 digits.
export function normalizeLoginIdentifier(value = '') {
  const raw = String(value).trim()
  return raw.includes('@') ? raw.toLowerCase() : normalizePhoneNumber(raw)
}

export function validateLogin(values) {
  const errors = {}
  const raw = String(values.identifier || '').trim()
  if (!raw) errors.identifier = 'Enter your email or phone number.'
  else if (raw.includes('@')) {
    if (!emailPattern.test(raw)) errors.identifier = 'Enter a valid email address.'
  } else if (!/^\d{10}$/.test(normalizePhoneNumber(raw))) {
    errors.identifier = 'Enter a valid email address or 10-digit phone number.'
  }
  if (!String(values.password || '')) errors.password = 'Password is required.'
  return errors
}
