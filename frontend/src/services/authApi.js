const AUTH_BASE = import.meta.env.VITE_AUTH_API_BASE_URL || '/api/auth'
const ACCESS_KEY = 'gst-access-token'
const REFRESH_KEY = 'gst-refresh-token'
const DEVICE_KEY = 'gst-device-id'
const TRUSTED_KEY = 'gst-trusted-device-token'
const SESSION_EXPIRED_EVENT = 'gst-session-expired'
let refreshPromise = null
let sessionValidationPromise = null

export function deviceId() {
  let value = localStorage.getItem(DEVICE_KEY)
  if (!value) { value = crypto.randomUUID(); localStorage.setItem(DEVICE_KEY, value) }
  return value
}

const credentials = () => ({
  device_id: deviceId(),
  trusted_device_token: localStorage.getItem(TRUSTED_KEY) || '',
  device_name: navigator.userAgent.slice(0, 255),
})

async function jsonRequest(path, options = {}) {
  const response = await fetch(`${AUTH_BASE}${path}`, options)
  let body = {}
  try { body = await response.json() } catch {}
  if (!response.ok) { const error = new Error(body.detail || 'Authentication request failed.'); Object.assign(error, body, { status: response.status }); throw error }
  return body
}

export function saveSession(data) {
  if (data.access) localStorage.setItem(ACCESS_KEY, data.access)
  if (data.refresh) localStorage.setItem(REFRESH_KEY, data.refresh)
  if (data.trusted_device_token) localStorage.setItem(TRUSTED_KEY, data.trusted_device_token)
  return data
}

export async function registerAccount(values) {
  return saveSession(await jsonRequest('/register/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, ...credentials() }) }))
}

export async function loginAccount(identifier, password = '') {
  return saveSession(await jsonRequest('/login/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ identifier, password, ...credentials() }) }))
}

export async function requestPasswordResetCode(email) {
  return jsonRequest('/password/forgot/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) })
}

export async function resetPasswordWithCode(email, code, newPassword) {
  return jsonRequest('/password/reset/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, code, new_password: newPassword }) })
}

export async function activateProductLicenseSession(activationKey, snapshot = {}) {
  const payload = {
    activation_key: activationKey,
    device_fingerprint: snapshot.device_fingerprint || deviceId(),
    device_name: snapshot.device_name || navigator.userAgent.slice(0, 255),
    windows_version: snapshot.windows_version || '',
    app_version: snapshot.app_version || '',
    detected_tally_serial: snapshot.detected_tally_serial || '',
    tally_edition: snapshot.tally_edition || '',
    tss_status: snapshot.tss_status || '',
    license_administrator: snapshot.license_administrator || '',
    current_company_name: snapshot.current_company_name || '',
    current_company_gstin: snapshot.current_company_gstin || '',
    state: snapshot.state || '',
    financial_year: snapshot.financial_year || '',
  }
  const response = await fetch('/api/gst-tally/license/activate/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  let body = {}
  try { body = await response.json() } catch {}
  if (!response.ok) { const error = new Error(body.message || body.detail || 'License activation failed.'); Object.assign(error, body, { status: response.status }); throw error }
  if (body.verification_result !== 'LICENSE_VERIFIED' || !body.ready) throw new Error('License activation was not verified by the backend.')
  return saveSession(body)
}

export async function refreshAccess() {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) throw new Error('Your session has expired. Please login again.')
  return saveSession(await jsonRequest('/refresh/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh, ...credentials() }) }))
}

async function refreshAccessOnce() {
  if (!refreshPromise) refreshPromise = refreshAccess().finally(() => { refreshPromise = null })
  return refreshPromise
}

// Distinct from a normal expired-access-token 401: SimpleJWT's own
// JWTAuthentication.get_user() raises this when the token itself decodes
// fine but the user row behind it no longer exists. Refreshing can never fix
// this (the new access token still maps to the same missing user), so it
// must never be treated as a retryable 401 -- one refresh-and-retry loop
// would just repeat forever.
const USER_NOT_FOUND_MESSAGE = 'Your session is no longer valid. Please sign in again.'

function expireSession(message = '') {
  clearSession()
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT, { detail: { message } }))
}

async function errorCodeOf(response) {
  try { return (await response.clone().json()).code || '' } catch { return '' }
}

export async function authenticatedFetch(url, options = {}) {
  const send = token => {
    const headers = new Headers(options.headers || {})
    headers.set('Authorization', `Bearer ${token}`)
    headers.set('X-Device-ID', deviceId())
    headers.set('X-Trusted-Device-Token', localStorage.getItem(TRUSTED_KEY) || '')
    return fetch(url, { ...options, headers })
  }
  let response = await send(localStorage.getItem(ACCESS_KEY) || '')
  if (response.status === 401) {
    if (await errorCodeOf(response) === 'user_not_found') {
      expireSession(USER_NOT_FOUND_MESSAGE)
      throw new Error(USER_NOT_FOUND_MESSAGE)
    }
    try {
      const refreshed = await refreshAccessOnce()
      response = await send(refreshed.access)
    } catch (error) {
      expireSession()
      throw error
    }
    if (response.status === 401) {
      expireSession(await errorCodeOf(response) === 'user_not_found' ? USER_NOT_FOUND_MESSAGE : '')
    }
  }
  return response
}

function tokenExpiresSoon(token, leewaySeconds = 30) {
  if (!token) return true
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return !payload.exp || payload.exp <= Math.floor(Date.now() / 1000) + leewaySeconds
  } catch { return true }
}

async function performSessionValidation() {
  let access = localStorage.getItem(ACCESS_KEY)
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!access && !refresh) return null
  // Avoid an expected 401 in the browser console: refresh an expired/malformed
  // access JWT before requesting the protected /me/ endpoint.
  if (refresh && tokenExpiresSoon(access)) access = (await refreshAccessOnce()).access
  const response = await authenticatedFetch(`${AUTH_BASE}/me/`)
  if (!response.ok) throw new Error('Your session has expired. Please login again.')
  return response.json()
}

export function validateSession() {
  if (!sessionValidationPromise) {
    sessionValidationPromise = performSessionValidation().finally(() => { sessionValidationPromise = null })
  }
  return sessionValidationPromise
}

export async function signOut() {
  const refresh = localStorage.getItem(REFRESH_KEY)
  try { if (refresh) await authenticatedFetch(`${AUTH_BASE}/logout/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh }) }) }
  finally { clearSession() }
}

export function clearSession() { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY) }
