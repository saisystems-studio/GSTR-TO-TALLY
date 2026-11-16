import { deviceId, saveSession } from './authApi.js'

const BASE = '/api/nexus'

// Browser cannot read a hardware id; the install-scoped id plus UA is the best stable value here.
const device = () => ({
  client_device_id: deviceId().slice(0, 64),
  fingerprint: `${deviceId()}|${navigator.userAgent}`.slice(0, 256),
  device_name: (navigator.platform || 'Browser').slice(0, 100),
  app_version: '1.0.0',
})

async function request(path, body, method = 'POST') {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Device-Fingerprint': deviceId() },
    body: method === 'GET' ? undefined : JSON.stringify(body || {}),
  })
  let data = {}
  try { data = await response.json() } catch { /* empty body */ }
  if (!response.ok) {
    // Show the real reason: server message, else the first field error, else a status-specific sentence.
    const FIELDS = { name: 'full_name' }
    const fieldErrors = Object.fromEntries(Object.entries(data.errors || {}).map(([key, value]) => [FIELDS[key] || key, Array.isArray(value) ? value[0] : String(value)]))
    const firstField = Object.values(fieldErrors)[0]
    const fallback = response.status === 429 ? 'Too many attempts. Please wait a minute and try again.'
      : response.status >= 500 ? 'The licensing server had a problem. Please try again shortly.' : 'The request could not be completed.'
    const error = new Error(data.detail || data.message || firstField || fallback)
    Object.assign(error, data, { status: response.status, errors: fieldErrors })
    throw error
  }
  return data
}

export const getNexusConfig = () => request('/config/', null, 'GET').catch(() => ({ enabled: false }))
export const detectTallySerial = () => request('/tally/detect/', null, 'GET')
export const pairConnector = (enrollment_id, proof) => request('/connector/pair/', { enrollment_id, proof })
export const nexusRegister = values => request('/register/', values)
export const nexusLogin = async (userId, password) => saveSession(await request('/login/', { user_id: userId, password, device: device() }))
export const nexusChangePassword = (ticket, current_password, new_password) => request('/password/change/', { ticket, current_password, new_password })
export const nexusForgot = identifier => request('/password/forgot/', { identifier })
export const nexusReset = (token, new_password, identifier = '') => request('/password/reset/', { token, new_password, identifier })
export const nexusResendCredentials = user_id => request('/credentials/resend/', { user_id })
export const activateRequestCode = userId => request('/activate/request-code/', { user_id: userId }) // emails a fresh Activation Key (no OTP)
export const activateVerify = (userId, activationKey) => request('/activate/verify/', { user_id: userId, activation_key: activationKey })
export const activateConfirm = async (ticket, confirmTransfer) => saveSession(await request('/activate/confirm/', { ticket, device: device(), confirm_transfer: confirmTransfer }))

// After sign-in: record one app-open (server de-dupes) and poll the authoritative session.
// A 401 from the session poll means Nexus revoked this device; the caller signs the user out.
export function startNexusSession(authedFetch, onEnded, intervalMs = 5 * 60 * 1000) {
  let stopped = false
  const call = (path, method = 'GET') => authedFetch(`${BASE}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: method === 'POST' ? '{}' : undefined })
  const poll = async () => {
    try { const r = await call('/session/'); if (r.status === 401 || r.status === 403) { if (!stopped) onEnded(); return } } catch { /* offline: server-side lease governs */ }
  }
  // One recorded open per browser session (login also records one server-side); refreshes/polls never add more.
  let alreadyOpened = false
  try { alreadyOpened = sessionStorage.getItem('gst-usage-opened') === '1'; sessionStorage.setItem('gst-usage-opened', '1') } catch { /* storage blocked */ }
  if (!alreadyOpened) call('/usage/open/', 'POST').catch(() => {})
  const timer = setInterval(poll, intervalMs)
  return () => { stopped = true; clearInterval(timer) }
}
