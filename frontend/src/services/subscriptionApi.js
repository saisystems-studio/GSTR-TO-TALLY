import { authenticatedFetch } from './authApi'

const BASE = '/api/subscriptions'

async function request(path, options = {}) {
  const response = await authenticatedFetch(`${BASE}${path}`, options)
  let body = {}
  try { body = await response.json() } catch {}
  if (!response.ok) {
    const error = new Error(body.detail || body.message || 'Request failed')
    Object.assign(error, body)
    throw error
  }
  return body
}

// GET-only for a normal customer -- activation_date/expiry_date/status can
// never be changed through this or any other customer-facing call (spec
// section 33). Every field here is a real backend value (spec section 19).
export const getMySubscription = () => request('/me/')
