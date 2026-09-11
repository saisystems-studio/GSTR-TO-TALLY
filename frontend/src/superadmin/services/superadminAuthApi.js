// Deliberately separate from ../../services/authApi.js -- own localStorage
// keys so a Super Admin session and a customer session can coexist in the
// same browser without clobbering each other (spec: Super Admin must be a
// fully separate area). No device-trust/license concept applies here (that
// is a customer-app-only flow), so this stays much thinner than authApi.js.

const AUTH_BASE = '/api/superadmin/auth'
const ACCESS_KEY = 'sa-access-token'
const REFRESH_KEY = 'sa-refresh-token'
export const SA_SESSION_EXPIRED_EVENT = 'sa-session-expired'
let refreshPromise = null

async function jsonRequest(path, options = {}) {
  const response = await fetch(`${AUTH_BASE}${path}`, options)
  let body = {}
  try { body = await response.json() } catch {}
  if (!response.ok) { const error = new Error(body.detail || 'Request failed.'); Object.assign(error, body, { status: response.status }); throw error }
  return body
}

function saveSession(data) {
  if (data.access) localStorage.setItem(ACCESS_KEY, data.access)
  if (data.refresh) localStorage.setItem(REFRESH_KEY, data.refresh)
  return data
}

export async function loginSuperAdmin(username, password) {
  return saveSession(await jsonRequest('/login/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) }))
}

export async function refreshAccess() {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) throw new Error('Your session has expired. Please sign in again.')
  return saveSession(await jsonRequest('/refresh/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh }) }))
}

async function refreshAccessOnce() {
  if (!refreshPromise) refreshPromise = refreshAccess().finally(() => { refreshPromise = null })
  return refreshPromise
}

function expireSession() {
  clearSession()
  window.dispatchEvent(new CustomEvent(SA_SESSION_EXPIRED_EVENT))
}

export async function authenticatedFetch(url, options = {}) {
  const send = token => {
    const headers = new Headers(options.headers || {})
    headers.set('Authorization', `Bearer ${token}`)
    return fetch(url, { ...options, headers })
  }
  let response = await send(localStorage.getItem(ACCESS_KEY) || '')
  if (response.status === 401) {
    try {
      const refreshed = await refreshAccessOnce()
      response = await send(refreshed.access)
    } catch (error) {
      expireSession()
      throw error
    }
    if (response.status === 401) expireSession()
  }
  return response
}

export async function fetchMe() {
  const response = await authenticatedFetch(`${AUTH_BASE}/me/`)
  if (!response.ok) throw new Error('Your session has expired. Please sign in again.')
  return response.json()
}

export async function updateProfile(data) {
  const response = await authenticatedFetch(`${AUTH_BASE}/me/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  })
  const body = await response.json()
  if (!response.ok) throw Object.assign(new Error(body.detail || 'Unable to update profile.'), body)
  return body
}

export async function changePassword(currentPassword, newPassword, confirmPassword) {
  const response = await authenticatedFetch(`${AUTH_BASE}/change-password/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword, confirm_password: confirmPassword }),
  })
  const body = await response.json()
  if (!response.ok) throw Object.assign(new Error(body.detail || 'Unable to change password.'), body)
  return body
}

export async function forgotPassword(username) {
  return jsonRequest('/forgot-password/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username }) })
}

export async function resetPassword(token, newPassword, confirmPassword) {
  return jsonRequest('/reset-password/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword, confirm_password: confirmPassword }),
  })
}

export async function signOut() {
  const refresh = localStorage.getItem(REFRESH_KEY)
  try { if (refresh) await authenticatedFetch(`${AUTH_BASE}/logout/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh }) }) }
  finally { clearSession() }
}

export function clearSession() { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY) }
export function hasSession() { return Boolean(localStorage.getItem(ACCESS_KEY)) }
