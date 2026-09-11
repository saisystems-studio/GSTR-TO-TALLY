import { formatDate } from './date.js'

const formats = {
  xlsx: 'Excel',
  xls: 'Excel',
  csv: 'CSV',
  json: 'JSON',
}

export function detectFileFormat(fileName = '') {
  const extension = String(fileName).split('.').pop().toLowerCase()
  return formats[extension] || ''
}

export function canStartImport({ returnType, file, fileFormat }) {
  return Boolean(returnType && file && fileFormat && detectFileFormat(file.name) === fileFormat)
}

export function licenseStatusFromPayload(payload) {
  const source = payload?.license || payload?.tally_license || payload
  const serial = source?.serial || source?.serial_number || source?.license_serial || ''
  const edition = source?.edition || source?.license_edition || ''
  const tssStatus = source?.tssStatus || source?.tss_status || source?.tss || ''
  const administrator = source?.administrator || source?.license_administrator || source?.admin || ''
  const active = String(tssStatus || '').trim().toLowerCase() === 'active'
  const verified = Boolean(source?.verified || source?.license_verified) && Boolean(serial) && active
  return {
    verified,
    blocked: !verified,
    serial,
    edition,
    tssStatus,
    administrator,
    message: verified ? 'Tally license verified.' : serial ? 'Tally license is not active or not verified by backend.' : 'Tally license details are unavailable from backend.',
  }
}

export function companyVerificationStatus(result) {
  if (!result) return { label: 'Pending', tone: 'pending' }
  return result.company_verified
    ? { label: 'Matched', tone: 'success' }
    : { label: 'Mismatch', tone: 'error' }
}

export function normalizeCompanyName(value = '') {
  return String(value || '').trim().replace(/\s+/g, ' ').replace(/[,.]+$/g, '').trim().toLowerCase()
}

export function normalizeCompanyVerificationResult(result, enteredCompany = '') {
  const company = result?.company || {}
  const detectedCompany = String(
    result?.detected_company ||
    result?.tally_company_name ||
    company.company_name ||
    company.company ||
    '',
  ).trim()
  const entered = String(
    result?.entered_company ||
    result?.uploaded_company_name ||
    enteredCompany ||
    '',
  ).trim()
  const companyGstin = String(
    result?.company_gstin ||
    result?.tally_gstin ||
    company.gstin ||
    '',
  ).trim()
  const companyState = String(
    result?.company_state ||
    result?.state ||
    company.state ||
    result?.company_read?.state ||
    '',
  ).trim()
  const financialYearFrom = String(
    result?.financial_year_from ||
    company.financial_year_from ||
    result?.company_read?.financial_year_from ||
    '',
  ).trim()
  const financialYearTo = String(
    result?.financial_year_to ||
    company.financial_year_to ||
    result?.company_read?.financial_year_to ||
    '',
  ).trim()
  const financialYear = String(
    result?.financial_year ||
    company.financial_year ||
    result?.company_read?.financial_year ||
    '',
  ).trim()
  const financialYearError = String(
    result?.financial_year_error ||
    company.financial_year_error ||
    result?.company_read?.financial_year_error ||
    '',
  ).trim()
  // GSTIN is the authoritative company identity: trust the backend's own
  // company_verified (GSTIN-primary) rather than re-gating it on a frontend
  // name comparison, which would block a valid exact-GSTIN match whenever the
  // company-name strings merely differ in formatting or wording.
  const verified = Boolean(result?.company_verified)

  return {
    ...result,
    company_verified: verified,
    entered_company: entered,
    detected_company: detectedCompany,
    company_gstin: companyGstin,
    company_state: companyState,
    error: result?.error ?? (verified ? null : result?.verification_code || result?.code || 'COMPANY_VERIFICATION_FAILED'),
    company: {
      ...company,
      company_name: detectedCompany,
      company: detectedCompany,
      gstin: companyGstin,
      state: companyState,
      financial_year_from: financialYearFrom,
      financial_year_to: financialYearTo,
      financial_year: financialYear,
      financial_year_error: financialYearError,
    },
  }
}

// Every branch here mirrors one exact verification_result the backend
// (services/product_license.py::verify_license_snapshot, used by both the
// Step 3 pre-import check and Step 6's import gate) can actually return --
// this is the ONLY place that turns those codes into copy a customer reads,
// so a code with no branch here is a code the customer sees as generic
// "License Verification Required" with no way to know what to do about it.
export function licenseVerificationStatus(status) {
  if (!status) return { label: 'Pending', tone: 'pending' }
  if (status.license_verified) return { label: 'Verified', tone: 'success' }
  const code = status.license_error || ''
  if (code === 'TALLY_LICENSE_DATA_UNAVAILABLE') {
    return {
      label: 'Unavailable',
      tone: 'error',
      title: 'Unable to Read Tally License',
      detail: status.license_error_detail || code,
    }
  }
  if (code === 'TALLY_NOT_CONNECTED') {
    return {
      label: 'Unavailable',
      tone: 'error',
      title: 'Unable to Connect to Tally',
      detail: 'Check your Tally connection and try again.',
    }
  }
  if (code === 'PRODUCT_LICENSE_NOT_CONFIGURED') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'License Setup Required',
      detail: 'Registered Tally Serial has not been configured.',
    }
  }
  if (code === 'TALLY_SERIAL_MISMATCH') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'Tally License Mismatch',
      detail: `Registered Serial: ${status.registered_tally_serial || '-'} | Detected Serial: ${status.detected_tally_serial || status.serial_number || '-'}`,
    }
  }
  if (code === 'COMPANY_GSTIN_MISMATCH') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'Company GSTIN Mismatch',
      detail: `Licensed GSTIN: ${status.licensed_gstin || '-'} | Current GSTIN: ${status.current_company_gstin || '-'}`,
    }
  }
  if (code === 'SOURCE_COMPANY_GSTIN_MISSING') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'Company GSTIN Not Found in Return',
      detail: 'Unable to identify the company GSTIN from the uploaded return.',
    }
  }
  if (code === 'LICENSE_EXPIRED') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'License Expired',
      detail: `Expired on: ${status.expiry_date ? formatDate(status.expiry_date) : '-'}`,
    }
  }
  if (code === 'LICENSE_SUSPENDED') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'License Suspended',
      detail: 'Contact your administrator to reactivate this license.',
    }
  }
  if (code === 'LICENSE_REVOKED') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'License Revoked',
      detail: 'Contact your administrator.',
    }
  }
  if (code === 'DEVICE_LIMIT_REACHED' || code === 'DEVICE_CHANGE_DETECTED') {
    return {
      label: 'Blocked',
      tone: 'error',
      title: 'Device Approval Required',
      detail: `Registered Device: ${status.registered_device || '-'}`,
    }
  }
  if (status.license_available === false) return { label: 'Unavailable', tone: 'error' }
  return { label: 'Blocked', tone: 'error', title: 'License Verification Failed', detail: 'Contact your administrator.' }
}

// Company Verification -> DB Storage step: readiness for the "Confirm &
// Prepare Masters" button. Company-name verification only -- no license
// gating here (see the separate, unrelated confirmMastersReadiness below,
// used by the later voucher-import gate).
export function companyDetailsReadiness(companyResult) {
  const companyVerified = Boolean(companyResult?.company_verified)
  const tallyConnected = Boolean(companyResult?.tally_connected)
  const companyDetailsSaved = Boolean(companyResult?.company_details_saved)
  return {
    companyVerified,
    tallyConnected,
    companyDetailsSaved,
    ready: companyVerified && tallyConnected && companyDetailsSaved,
  }
}

export function confirmMastersReadiness(companyResult, licenseResult) {
  const companyVerified = Boolean(companyResult?.company_verified)
  const licenseVerified = Boolean(licenseResult?.license_verified)
  const companyData = companyResult?.company || {}
  const financialYearVerified = Boolean(companyData.financial_year || (companyData.financial_year_from && companyData.financial_year_to))
  const tallyConnected = companyVerified && licenseVerified
  return {
    companyVerified,
    licenseVerified,
    tallyConnected,
    financialYearVerified,
    ready: companyVerified && licenseVerified && tallyConnected && financialYearVerified,
  }
}

// created/existing/reused/already-exists/updated/verified all count as a
// usable, ready master -- a master that already exists in Tally is READY,
// not merely "not newly created".
const usableStatusLabels = [['existing', 'Existing'], ['already exists', 'Already Exists'], ['reused', 'Reused'],
  ['created', 'Created'], ['updated', 'Updated'], ['verified', 'Verified']]
const usableMasterStatuses = new Set(usableStatusLabels.map(([status]) => status))
const masterSummaryCategories = [
  { key: 'parties', label: 'PARTIES', types: new Set(['party']) },
  { key: 'accounts', label: 'ACCOUNTS', types: new Set(['sales', 'purchase']) },
  { key: 'taxLedgers', label: 'TAX LEDGERS', types: new Set(['tax']) },
  { key: 'otherLedgers', label: 'OTHER LEDGERS', types: null },
]

const normalizedMasterType = row => String(row?.master_type || row?.type || '').trim().toLowerCase()
const normalizedMasterStatus = row => String(row?.status || '').trim().toLowerCase()
const isFailedMaster = row => normalizedMasterStatus(row).includes('failed')
const isCompletedMaster = row => usableMasterStatuses.has(normalizedMasterStatus(row))

function categoryForMaster(row) {
  const type = normalizedMasterType(row)
  return masterSummaryCategories.find(category => category.types?.has(type)) || masterSummaryCategories[3]
}

export function mastersSummaryFromRows(rows = []) {
  const summaries = masterSummaryCategories.map(category => ({ ...category, completed: 0, total: 0, percent: 0, byStatus: {} }))
  let failed = 0
  ;(rows || []).forEach(row => {
    if (isFailedMaster(row)) failed += 1
    const summary = summaries.find(item => item.key === categoryForMaster(row).key)
    summary.total += 1
    if (isCompletedMaster(row)) {
      summary.completed += 1
      const status = normalizedMasterStatus(row)
      summary.byStatus[status] = (summary.byStatus[status] || 0) + 1
    }
  })
  return [
    ...summaries.map(({ types, ...summary }) => ({
      ...summary,
      percent: summary.total > 0 ? Math.round((summary.completed / summary.total) * 100) : 0,
    })),
    { key: 'failed', label: 'FAILED', completed: failed, total: failed, percent: 0 },
  ]
}

// "13 Existing" / "5 Existing, 2 Created" -- the ready_count breakdown by
// what made each master usable (already existed vs. newly created/reused).
export function readyBreakdownText(byStatus = {}) {
  return usableStatusLabels
    .filter(([status]) => byStatus[status] > 0)
    .map(([status, label]) => `${byStatus[status]} ${label}`)
    .join(', ')
}

export function filterMastersRows(rows = [], filter = 'all') {
  if (filter === 'failed') return (rows || []).filter(isFailedMaster)
  if (filter === 'all' || !filter) return rows
  // Category tabs (parties/accounts/taxLedgers/otherLedgers) reuse the exact
  // same categorization the summary cards are built from, so a tab's rows can
  // never disagree with that card's own count.
  return (rows || []).filter(row => categoryForMaster(row).key === filter)
}

// Local, instant text search over already-loaded master rows -- no backend
// call, matches the same fields the table itself displays.
export function searchMastersRows(rows = [], query = '') {
  const needle = String(query || '').trim().toLowerCase()
  if (!needle) return rows
  return (rows || []).filter(row => [row?.master_type, row?.type, row?.name, row?.group, row?.gst_rate, row?.tax_type, row?.status, row?.message]
    .some(value => String(value || '').toLowerCase().includes(needle)))
}

export function hasReadyParty(parties) {
  return (parties?.parties || []).some(row => row?.tally_ready)
}

export function nextWorkflowStep(state) {
  if (!state?.preview || !state?.batch?.id) return 'upload'
  if (!state.parties) return 'preview'
  if (!hasReadyParty(state.parties)) return 'parties'
  if (!state.companyVerified) return 'company'
  if (!state.licenseVerified) return 'license'
  if (!state.confirmed) return 'confirm'
  if (!state.mastersReady) return 'masters'
  if (!state.vouchersValidated) return 'vouchers'
  return 'import'
}

export function buildPrepState(screen, { batch, parties, connectionResult, verifyStage, companyResult, licenseResult, confirmed }) {
  const inParties = screen === 'parties'
  const partiesDone = Boolean(batch?.id)
  const fetchDone = Boolean(parties?.parties?.length)
  const connectionDone = isTallyConnectionReady(connectionResult)
  const connectionFailed = Boolean(connectionResult && !connectionDone)
  const companyDone = Boolean(companyResult?.company_verified)
  const companyFailed = Boolean(companyResult && !companyDone)
  const licenseDone = Boolean(licenseResult?.license_verified)
  const licenseFailed = Boolean(licenseResult && !licenseDone)
  return {
    parties: partiesDone ? 'done' : inParties ? 'active' : 'pending',
    fetch: fetchDone ? 'done' : inParties && !fetchDone ? 'active' : 'pending',
    connection: connectionDone ? 'done' : connectionFailed ? 'failed' : inParties && fetchDone ? 'active' : 'pending',
    company: companyDone ? 'done' : companyFailed ? 'failed' : inParties && verifyStage === 'company' ? 'active' : 'pending',
    license: licenseDone ? 'done' : licenseFailed ? 'failed' : inParties && verifyStage === 'license' ? 'active' : 'pending',
    masters: confirmed ? 'done' : inParties && verifyStage === 'confirm' ? 'active' : 'pending',
  }
}

export function isTallyConnectionReady(connection) {
  return Boolean(
    connection?.can_import === true
    && connection?.company_open === true
    && connection?.read_connected === true,
  )
}

export function tallyConnectionErrorMessage(connection) {
  const code = connection?.error_code || connection?.failure_type || ''
  if (connection?.host && connection?.port && code.startsWith('TALLY_CONNECTION')) {
    return `Unable to connect to Tally on ${connection.host}:${connection.port}.`
  }
  return connection?.message || connection?.error_message || 'Unable to reach Tally. Start TallyPrime and try again.'
}

export function shouldAutoStartImport({
  autoStartImport,
  pageReady,
  alreadyTriggered,
  importBusy,
  hasJob,
  hasResult,
}) {
  return Boolean(autoStartImport && pageReady && !alreadyTriggered && !importBusy && !hasJob && !hasResult)
}
export const RETURN_TYPE_FILE_FORMATS = Object.freeze({
  GSTR1: { label: 'JSON', extension: '.json' },
  GSTR2A: { label: 'CSV', extension: '.csv' },
  GSTR2B: { label: 'Excel', extension: '.xlsx' },
})

export function defaultFileFormat(returnType) {
  return RETURN_TYPE_FILE_FORMATS[returnType]?.label || ''
}

export function fileFormatExtension(fileFormat) {
  return { JSON: '.json', CSV: '.csv', Excel: '.xlsx' }[fileFormat] || ''
}
