import { authenticatedFetch } from './superadminAuthApi.js'

const BASE = '/api/superadmin'

async function request(path, options = {}) {
  const response = await authenticatedFetch(`${BASE}${path}`, options)
  const contentType = response.headers.get('content-type') || ''
  if (!response.ok) {
    let body = {}
    try { body = await response.json() } catch {}
    throw Object.assign(new Error(body.detail || 'Request failed.'), body)
  }
  if (contentType.includes('text/csv')) return response.blob()
  try { return await response.json() } catch { return null }
}

const json = (method, path, data) => request(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
const qs = params => {
  const entries = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '')
  return entries.length ? `?${new URLSearchParams(entries)}` : ''
}

export const getDashboard = () => request('/dashboard/')

export const listCustomers = params => request(`/customers/${qs(params)}`)
export const getCustomer = userId => request(`/customers/${userId}/`)
export const suspendCustomer = (userId, reason) => json('POST', `/customers/${userId}/suspend/`, { reason })
export const reactivateCustomer = (userId, reason) => json('POST', `/customers/${userId}/reactivate/`, { reason })
export const changeCompanyLimit = (userId, newLimit, reason) => json('POST', `/customers/${userId}/company-limit/`, { new_limit: newLimit, reason })
export const listCustomerNotes = userId => request(`/customers/${userId}/notes/`)
export const addCustomerNote = (userId, note) => json('POST', `/customers/${userId}/notes/`, { note })
export const registerCustomerCompany = (userId, gstin, companyName) => json('POST', `/customers/${userId}/companies/`, { gstin, company_name: companyName })

export const listCompanies = params => request(`/companies/${qs(params)}`)
export const getCompany = companyId => request(`/companies/${companyId}/`)
export const updateCompany = (companyId, data) => request(`/companies/${companyId}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
export const resetDevice = (companyId, deviceId, reason) => json('POST', `/companies/${companyId}/device/${deviceId}/reset/`, { reason })

export const listPlans = () => request('/plans/')
export const createPlan = data => json('POST', '/plans/', data)
export const updatePlan = (planId, data) => request(`/plans/${planId}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
export const deactivatePlan = planId => request(`/plans/${planId}/`, { method: 'DELETE' })

export const listSubscriptions = params => request(`/subscriptions/${qs(params)}`)
export const renewSubscription = (userId, data) => json('POST', `/subscriptions/${userId}/renew/`, data)
export const changePlan = (userId, planCode, reason) => json('POST', `/subscriptions/${userId}/change-plan/`, { plan_code: planCode, reason })

export const listPayments = params => request(`/payments/${qs(params)}`)
export const getPayment = paymentId => request(`/payments/${paymentId}/`)
export const createPayment = data => json('POST', '/payments/', data)
export const updatePaymentStatus = (paymentId, paymentStatus, reason) => request(`/payments/${paymentId}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payment_status: paymentStatus, reason }) })

export const getUsage = () => request('/usage/')

export const listTickets = params => request(`/support/${qs(params)}`)
export const createTicket = data => json('POST', '/support/', data)
export const updateTicket = (ticketId, data) => request(`/support/${ticketId}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })

export const listAuditLogs = params => request(`/audit-logs/${qs(params)}`)

export const listProductLicenses = params => request(`/product-licenses/${qs(params)}`)
export const getProductLicense = licenseId => request(`/product-licenses/${licenseId}/`)
export const createProductLicense = data => json('POST', '/product-licenses/', data)
export const extendProductLicense = (licenseId, data) => json('POST', `/product-licenses/${licenseId}/extend/`, data)
export const suspendProductLicense = licenseId => json('POST', `/product-licenses/${licenseId}/suspend/`, {})
export const reactivateProductLicense = licenseId => json('POST', `/product-licenses/${licenseId}/reactivate/`, {})
export const revokeProductLicense = licenseId => json('POST', `/product-licenses/${licenseId}/revoke/`, {})
export const changeProductLicenseTallySerial = (licenseId, newSerial, reason) => json('POST', `/product-licenses/${licenseId}/change-tally-serial/`, { new_serial: newSerial, reason })
export const revokeLicensedDevice = (licenseId, deviceId, reason) => json('POST', `/product-licenses/${licenseId}/devices/${deviceId}/revoke/`, { reason })
export const listDeviceRequests = params => request(`/device-requests/${qs(params)}`)
export const approveDeviceRequest = (requestId, reason) => json('POST', `/device-requests/${requestId}/approve/`, { reason })
export const replaceDeviceRequest = (requestId, reason) => json('POST', `/device-requests/${requestId}/replace/`, { reason })
export const rejectDeviceRequest = (requestId, reason) => json('POST', `/device-requests/${requestId}/reject/`, { reason })

export const getSettings = () => request('/settings/')
export const updateSettings = data => request('/settings/', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
export const getSandboxConfiguration = () => request('/sandbox-configuration/')
export const testSandboxConfiguration = data => json('POST', '/sandbox-configuration/', data)
export const activateSandboxConfiguration = data => request('/sandbox-configuration/', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })

export async function exportReport(kind) {
  const blob = await request(`/reports/${kind}/export/`)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${kind}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
