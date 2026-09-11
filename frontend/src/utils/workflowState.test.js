import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildPrepState,
  canStartImport,
  companyVerificationStatus,
  confirmMastersReadiness,
  detectFileFormat,
  defaultFileFormat,
  fileFormatExtension,
  filterMastersRows,
  isTallyConnectionReady,
  licenseVerificationStatus,
  licenseStatusFromPayload,
  mastersSummaryFromRows,
  nextWorkflowStep,
  normalizeCompanyVerificationResult,
  readyBreakdownText,
  shouldAutoStartImport,
  tallyConnectionErrorMessage,
} from './workflowState.js'

test('return types have one fixed upload format', () => {
  assert.equal(defaultFileFormat('GSTR1'), 'JSON')
  assert.equal(defaultFileFormat('GSTR2A'), 'CSV')
  assert.equal(defaultFileFormat('GSTR2B'), 'Excel')
  assert.equal(fileFormatExtension('Excel'), '.xlsx')
})

test('detectFileFormat returns the supported upload format from the file name', () => {
  assert.equal(detectFileFormat('gstr1.xlsx'), 'Excel')
  assert.equal(detectFileFormat('gstr2a.csv'), 'CSV')
  assert.equal(detectFileFormat('gstr2b.json'), 'JSON')
  assert.equal(detectFileFormat('notes.pdf'), '')
})

test('canStartImport requires return type, matching file format, and an actual file', () => {
  assert.equal(canStartImport({ returnType: 'GSTR1', file: { name: 'r1.xlsx' }, fileFormat: 'Excel' }), true)
  assert.equal(canStartImport({ returnType: '', file: { name: 'r1.xlsx' }, fileFormat: 'Excel' }), false)
  assert.equal(canStartImport({ returnType: 'GSTR1', file: null, fileFormat: 'Excel' }), false)
  assert.equal(canStartImport({ returnType: 'GSTR1', file: { name: 'r1.csv' }, fileFormat: 'Excel' }), false)
})

test('licenseStatusFromPayload verifies only backend supplied active Tally license identity', () => {
  assert.deepEqual(licenseStatusFromPayload({
    serial_number: 'TLY123',
    edition: 'Gold',
    tss_status: 'Active',
    administrator: 'admin@example.com',
    verified: true,
  }), {
    verified: true,
    blocked: false,
    serial: 'TLY123',
    edition: 'Gold',
    tssStatus: 'Active',
    administrator: 'admin@example.com',
    message: 'Tally license verified.',
  })

  assert.equal(licenseStatusFromPayload({ serial_number: 'TLY123', tss_status: 'Expired', verified: true }).verified, false)
  assert.equal(licenseStatusFromPayload(null).blocked, true)
})

test('companyVerificationStatus shows matched only from backend verification', () => {
  assert.deepEqual(companyVerificationStatus(null), { label: 'Pending', tone: 'pending' })
  assert.deepEqual(companyVerificationStatus({ company_verified: false }), { label: 'Mismatch', tone: 'error' })
  assert.deepEqual(companyVerificationStatus({ company_verified: true }), { label: 'Matched', tone: 'success' })
})

test('normalizeCompanyVerificationResult maps backend company_verified success fields', () => {
  const result = normalizeCompanyVerificationResult({
    company_verified: true,
    uploaded_company_name: ' SRI MAHALAKSHMI TRADERS, ',
    tally_company_name: 'SRI MAHALAKSHMI TRADERS',
    tally_gstin: '33AFHPM6103Q1Z8',
    company_read: {
      state: 'Tamil Nadu',
      financial_year_from: '2025-04-01',
      financial_year_to: '2026-03-31',
      financial_year: '01 Apr 2025 - 31 Mar 2026',
    },
    error: null,
  }, 'Ignored stale input')

  assert.equal(result.company_verified, true)
  assert.equal(result.entered_company, 'SRI MAHALAKSHMI TRADERS,')
  assert.equal(result.detected_company, 'SRI MAHALAKSHMI TRADERS')
  assert.equal(result.company_gstin, '33AFHPM6103Q1Z8')
  assert.equal(result.company_state, 'Tamil Nadu')
  assert.equal(result.error, null)
  assert.deepEqual(result.company, {
    company_name: 'SRI MAHALAKSHMI TRADERS',
    company: 'SRI MAHALAKSHMI TRADERS',
    gstin: '33AFHPM6103Q1Z8',
    state: 'Tamil Nadu',
    financial_year_from: '2025-04-01',
    financial_year_to: '2026-03-31',
    financial_year: '01 Apr 2025 - 31 Mar 2026',
    financial_year_error: '',
  })
})

test('normalizeCompanyVerificationResult keeps a real company mismatch unverified', () => {
  const result = normalizeCompanyVerificationResult({
    company_verified: false,
    entered_company: 'SRI MAHALAKSHMI TRADERS,',
    detected_company: 'DIFFERENT COMPANY',
    company_gstin: '29ABCDE1234F1Z5',
    company_state: 'Tamil Nadu',
    verification_code: 'TALLY_COMPANY_NOT_OPEN',
  })

  assert.equal(result.company_verified, false)
  assert.equal(result.error, 'TALLY_COMPANY_NOT_OPEN')
})

test('confirmMastersReadiness requires active Tally financial period from company result', () => {
  const license = { license_verified: true }
  const completeCompany = {
    company_verified: true,
    company: {
      company_name: 'SRI MAHALAKSHMI TRADERS',
      gstin: '33AFHPM6103Q1Z8',
      state: 'Tamil Nadu',
      financial_year_from: '2025-04-01',
      financial_year_to: '2026-03-31',
    },
  }

  assert.equal(confirmMastersReadiness({ ...completeCompany, company: { ...completeCompany.company, financial_year_to: '' } }, license).ready, false)
  assert.equal(confirmMastersReadiness({
    ...completeCompany,
    company: { ...completeCompany.company, financial_year_from: '', financial_year_to: '', financial_year: '01 Apr 2025 - 31 Mar 2026' },
  }, license).ready, true)
  assert.deepEqual(confirmMastersReadiness(completeCompany, license), {
    companyVerified: true,
    licenseVerified: true,
    tallyConnected: true,
    financialYearVerified: true,
    ready: true,
  })
})

test('normalizeCompanyVerificationResult preserves backend financial year error', () => {
  const result = normalizeCompanyVerificationResult({
    company_verified: true,
    entered_company: 'SRI MAHALAKSHMI TRADERS,',
    detected_company: 'SRI MAHALAKSHMI TRADERS',
    company_gstin: '33AFHPM6103Q1Z8',
    company_read: {
      state: 'Tamil Nadu',
      financial_year_error: 'TALLY_FINANCIAL_YEAR_UNAVAILABLE',
    },
  })

  assert.equal(result.company.financial_year, '')
  assert.equal(result.company.financial_year_error, 'TALLY_FINANCIAL_YEAR_UNAVAILABLE')
})

test('licenseVerificationStatus shows verified only from backend license verification', () => {
  assert.deepEqual(licenseVerificationStatus(null), { label: 'Pending', tone: 'pending' })
  assert.deepEqual(licenseVerificationStatus({ license_verified: true }), { label: 'Verified', tone: 'success' })
  // A code with no specific branch (e.g. the legacy, no-longer-emitted
  // LICENSE_IDENTITY_MISMATCH) still gets a generic-but-real title/detail,
  // never a silent blank state.
  assert.deepEqual(licenseVerificationStatus({ license_error: 'LICENSE_IDENTITY_MISMATCH' }), {
    label: 'Blocked', tone: 'error', title: 'License Verification Failed', detail: 'Contact your administrator.',
  })
  assert.deepEqual(licenseVerificationStatus({ license_available: false }), { label: 'Unavailable', tone: 'error' })
  assert.deepEqual(licenseVerificationStatus({ license_verified: false }), {
    label: 'Blocked', tone: 'error', title: 'License Verification Failed', detail: 'Contact your administrator.',
  })
})

test('licenseVerificationStatus explains concrete backend license failures', () => {
  assert.deepEqual(licenseVerificationStatus({
    license_available: false,
    license_error: 'TALLY_LICENSE_DATA_UNAVAILABLE',
    license_error_detail: 'SerialNumber returned an empty value.',
  }), {
    label: 'Unavailable',
    tone: 'error',
    title: 'Unable to Read Tally License',
    detail: 'SerialNumber returned an empty value.',
  })
  assert.deepEqual(licenseVerificationStatus({ license_error: 'TALLY_NOT_CONNECTED' }), {
    label: 'Unavailable',
    tone: 'error',
    title: 'Unable to Connect to Tally',
    detail: 'Check your Tally connection and try again.',
  })
  assert.deepEqual(licenseVerificationStatus({ license_error: 'PRODUCT_LICENSE_NOT_CONFIGURED' }), {
    label: 'Blocked',
    tone: 'error',
    title: 'License Setup Required',
    detail: 'Registered Tally Serial has not been configured.',
  })
  assert.deepEqual(licenseVerificationStatus({
    license_error: 'TALLY_SERIAL_MISMATCH',
    registered_tally_serial: '735149529',
    detected_tally_serial: '845621773',
  }), {
    label: 'Blocked',
    tone: 'error',
    title: 'Tally License Mismatch',
    detail: 'Registered Serial: 735149529 | Detected Serial: 845621773',
  })
  assert.deepEqual(licenseVerificationStatus({
    license_error: 'COMPANY_GSTIN_MISMATCH',
    licensed_gstin: '33AFHPM6103Q1Z8',
    current_company_gstin: '33ZZZZZ0000Z1Z9',
  }), {
    label: 'Blocked',
    tone: 'error',
    title: 'Company GSTIN Mismatch',
    detail: 'Licensed GSTIN: 33AFHPM6103Q1Z8 | Current GSTIN: 33ZZZZZ0000Z1Z9',
  })
  assert.deepEqual(licenseVerificationStatus({ license_error: 'LICENSE_EXPIRED', expiry_date: '2026-01-15' }), {
    label: 'Blocked',
    tone: 'error',
    title: 'License Expired',
    detail: 'Expired on: 15-01-2026',
  })
  assert.deepEqual(licenseVerificationStatus({ license_error: 'LICENSE_SUSPENDED' }), {
    label: 'Blocked',
    tone: 'error',
    title: 'License Suspended',
    detail: 'Contact your administrator to reactivate this license.',
  })
  assert.deepEqual(licenseVerificationStatus({ license_error: 'LICENSE_REVOKED' }), {
    label: 'Blocked',
    tone: 'error',
    title: 'License Revoked',
    detail: 'Contact your administrator.',
  })
  assert.deepEqual(licenseVerificationStatus({ license_error: 'DEVICE_LIMIT_REACHED', registered_device: 'OFFICE-PC-01' }), {
    label: 'Blocked',
    tone: 'error',
    title: 'Device Approval Required',
    detail: 'Registered Device: OFFICE-PC-01',
  })
})

test('confirmMastersReadiness requires verified company and verified license before proceeding', () => {
  assert.deepEqual(confirmMastersReadiness(null, null), {
    companyVerified: false,
    licenseVerified: false,
    tallyConnected: false,
    financialYearVerified: false,
    ready: false,
  })
  assert.equal(confirmMastersReadiness({ company_verified: true }, null).ready, false)
  assert.equal(confirmMastersReadiness(null, { license_verified: true }).ready, false)
  assert.deepEqual(confirmMastersReadiness({
    company_verified: true,
    company: { financial_year_from: '2025-04-01', financial_year_to: '2026-03-31' },
  }, { license_verified: true }), {
    companyVerified: true,
    licenseVerified: true,
    tallyConnected: true,
    financialYearVerified: true,
    ready: true,
  })
})

test('mastersSummaryFromRows builds five dynamic summary cards from backend rows', () => {
  const rows = [
    { master_type: 'Party', status: 'Existing' },
    { master_type: 'Party', status: 'Ready to Create' },
    { master_type: 'Sales', status: 'Created' },
    { master_type: 'Purchase', status: 'Failed' },
    { master_type: 'Tax', status: 'Already Exists' },
    { master_type: 'Ledger', status: 'Reused' },
  ]

  assert.deepEqual(mastersSummaryFromRows(rows).map(item => [item.key, item.completed, item.total, item.percent]), [
    ['parties', 1, 2, 50],
    ['accounts', 1, 2, 50],
    ['taxLedgers', 1, 1, 100],
    ['otherLedgers', 1, 1, 100],
    ['failed', 1, 1, 0],
  ])
})

test('mastersSummaryFromRows counts existing masters as ready -- 13 required, 13 existing shows 13/13', () => {
  const rows = Array.from({ length: 13 }, (_, i) => ({ master_type: 'Party', name: `Party ${i}`, status: 'Existing' }))

  const [parties] = mastersSummaryFromRows(rows)

  assert.equal(parties.key, 'parties')
  assert.equal(parties.total, 13)
  assert.equal(parties.completed, 13)
  assert.equal(parties.percent, 100)
  assert.deepEqual(parties.byStatus, { existing: 13 })
})

test('mastersSummaryFromRows tracks a mixed existing/created/pending breakdown per category', () => {
  const rows = [
    { master_type: 'Party', status: 'Existing' },
    { master_type: 'Party', status: 'Existing' },
    { master_type: 'Party', status: 'Created' },
    { master_type: 'Party', status: 'Ready to Create' },
  ]

  const [parties] = mastersSummaryFromRows(rows)

  assert.equal(parties.total, 4)
  assert.equal(parties.completed, 3)
  assert.deepEqual(parties.byStatus, { existing: 2, created: 1 })
})

test('readyBreakdownText formats the ready-count breakdown for card secondary text', () => {
  assert.equal(readyBreakdownText({ existing: 13 }), '13 Existing')
  assert.equal(readyBreakdownText({ existing: 5, created: 2 }), '5 Existing, 2 Created')
  assert.equal(readyBreakdownText({}), '')
  assert.equal(readyBreakdownText(), '')
})

test('filterMastersRows filters failed rows locally without changing backend data', () => {
  const rows = [
    { name: 'A', status: 'Existing' },
    { name: 'B', status: 'Failed' },
    { name: 'C', status: 'Validation Failed' },
  ]

  assert.deepEqual(filterMastersRows(rows, 'failed').map(row => row.name), ['B', 'C'])
  assert.deepEqual(filterMastersRows(rows, 'all'), rows)
})

test('nextWorkflowStep advances only after each backend-backed stage is ready', () => {
  assert.equal(nextWorkflowStep({ preview: null }), 'upload')
  assert.equal(nextWorkflowStep({ preview: {}, batch: { id: 7 } }), 'preview')
  assert.equal(nextWorkflowStep({ preview: {}, batch: { id: 7 }, parties: { parties: [{ tally_ready: true }] } }), 'company')
  assert.equal(nextWorkflowStep({ preview: {}, batch: { id: 7 }, parties: { parties: [{ tally_ready: true }] }, companyVerified: true }), 'license')
  assert.equal(nextWorkflowStep({ preview: {}, batch: { id: 7 }, parties: { parties: [{ tally_ready: true }] }, companyVerified: true, licenseVerified: true }), 'confirm')
  assert.equal(nextWorkflowStep({ preview: {}, batch: { id: 7 }, parties: { parties: [{ tally_ready: true }] }, companyVerified: true, licenseVerified: true, confirmed: true, mastersReady: true, vouchersValidated: true }), 'import')
})

test('buildPrepState marks Tally connection failed and keeps later checks pending', () => {
  assert.deepEqual(buildPrepState('parties', {
    batch: { id: 7 },
    parties: { parties: [{ tally_ready: true }] },
    connectionResult: {
      read_connected: false,
      company_open: false,
      error_code: 'TALLY_CONNECTION_REFUSED',
    },
  }), {
    parties: 'done',
    fetch: 'done',
    connection: 'failed',
    company: 'pending',
    license: 'pending',
    masters: 'pending',
  })
})

test('XML fallback is a valid Tally connection for Step 3', () => {
  const connection = {
    requested_transport: 'JSON',
    actual_transport: 'XML',
    json_connected: false,
    xml_connected: true,
    fallback_used: true,
    read_connected: true,
    tcp_connected: true,
    http_connected: true,
    odbc_connected: true,
    company_open: true,
    can_import: true,
    company_name: 'KUMARAN SUPER MARKET',
    company_gstin: '33EUJPM9654K1ZX',
  }

  assert.equal(isTallyConnectionReady(connection), true)
  assert.deepEqual(buildPrepState('parties', {
    batch: { id: 7 },
    parties: { parties: [{ tally_ready: true }] },
    connectionResult: connection,
    verifyStage: 'company',
  }), {
    parties: 'done',
    fetch: 'done',
    connection: 'done',
    company: 'active',
    license: 'pending',
    masters: 'pending',
  })
})

test('Step 3 rejects a connection that cannot import', () => {
  assert.equal(isTallyConnectionReady({
    read_connected: true,
    company_open: true,
    can_import: false,
  }), false)
})

test('Step 3 rejects a connection when no Tally company is open', () => {
  assert.equal(isTallyConnectionReady({
    read_connected: true,
    company_open: false,
    can_import: true,
  }), false)
})

test('tallyConnectionErrorMessage names the configured Tally endpoint concisely', () => {
  assert.equal(tallyConnectionErrorMessage({
    host: '127.0.0.1',
    port: 9000,
    error_code: 'TALLY_CONNECTION_REFUSED',
    message: 'Tally refused the TCP connection',
  }), 'Unable to connect to Tally on 127.0.0.1:9000.')
  assert.equal(tallyConnectionErrorMessage({ message: 'ODBC driver missing' }), 'ODBC driver missing')
})

test('shouldAutoStartImport allows Step 6 auto-start only once after readiness', () => {
  assert.equal(shouldAutoStartImport({
    autoStartImport: true,
    pageReady: true,
    alreadyTriggered: false,
    importBusy: false,
    hasJob: false,
    hasResult: false,
  }), true)

  assert.equal(shouldAutoStartImport({
    autoStartImport: true,
    pageReady: true,
    alreadyTriggered: true,
    importBusy: false,
    hasJob: false,
    hasResult: false,
  }), false)
  assert.equal(shouldAutoStartImport({
    autoStartImport: true,
    pageReady: true,
    alreadyTriggered: false,
    importBusy: false,
    hasJob: true,
    hasResult: false,
  }), false)
  assert.equal(shouldAutoStartImport({
    autoStartImport: false,
    pageReady: true,
    alreadyTriggered: false,
    importBusy: false,
    hasJob: false,
    hasResult: false,
  }), false)
})
