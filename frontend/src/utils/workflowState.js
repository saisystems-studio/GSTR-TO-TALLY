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

export function licenseVerificationStatus(status) {
  if (!status) return { label: 'Pending', tone: 'pending' }
  if (status.license_verified) return { label: 'Verified', tone: 'success' }
  if (status.license_available === false) return { label: 'Unavailable', tone: 'error' }
  return { label: 'Blocked', tone: 'error' }
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
  return filter === 'failed' ? (rows || []).filter(isFailedMaster) : rows
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
  const connectionDone = Boolean(connectionResult?.read_connected && connectionResult?.company_open && connectionResult?.can_import !== false)
  const connectionFailed = Boolean(connectionResult && !connectionDone)
  const companyDone = Boolean(companyResult?.company_verified)
  const licenseDone = Boolean(licenseResult?.license_verified)
  return {
    parties: partiesDone ? 'done' : inParties ? 'active' : 'pending',
    fetch: fetchDone ? 'done' : inParties && !fetchDone ? 'active' : 'pending',
    connection: connectionDone ? 'done' : connectionFailed ? 'failed' : inParties && fetchDone ? 'active' : 'pending',
    company: companyDone ? 'done' : inParties && verifyStage === 'company' ? 'active' : 'pending',
    license: licenseDone ? 'done' : inParties && verifyStage === 'license' ? 'active' : 'pending',
    masters: confirmed ? 'done' : inParties && verifyStage === 'confirm' ? 'active' : 'pending',
  }
}

export function tallyConnectionErrorMessage(connection) {
  const code = connection?.error_code || connection?.failure_type || ''
  if (connection?.host && connection?.port && code.startsWith('TALLY_CONNECTION')) {
    return `Unable to connect to Tally on ${connection.host}:${connection.port}.`
  }
  return connection?.message || connection?.error_message || 'Unable to reach Tally. Start TallyPrime and try again.'
}
