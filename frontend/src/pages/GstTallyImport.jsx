import { useEffect, useState } from 'react'
import DataTransferAnimation from '../components/gst-tally/DataTransferAnimation'
import ExcelPreviewGrid from '../components/gst-tally/ExcelPreviewGrid'
import VoucherMismatchModal from '../components/gst-tally/VoucherMismatchModal'
import AppShell from '../components/common/AppShell'
import LoadingButton from '../components/common/LoadingButton'
import StatusBadge from '../components/common/StatusBadge'
import StepProgress from '../components/common/StepProgress'
import TableSkeleton from '../components/common/TableSkeleton'
import Toast from '../components/common/Toast'
import {
  fetchBatchParties,
  getBatchParties,
  getTallyConnection,
  importToTally,
  prepareTallyMasters,
  previewGSTFile,
  previewTallyVouchers,
  resolveBatchCompany,
  uploadGSTFile,
  verifyTallyLicense,
} from '../services/gstTallyApi'
import { formatDate } from '../utils/date'
import { finalImportToast } from '../utils/importOutcome'
import { getProcessingStage, getProcessingVisualMode, isProcessingComplete, PROCESSING_READY_COMPLETE_MS } from '../utils/processingAnimation'
import { buildPrepState, canStartImport, companyVerificationStatus, confirmMastersReadiness, detectFileFormat, filterMastersRows, licenseVerificationStatus, mastersSummaryFromRows, normalizeCompanyVerificationResult, readyBreakdownText, tallyConnectionErrorMessage } from '../utils/workflowState'
import '../styles/gst-tally.css'

const returnTypes = [
  { id: 'GSTR1', title: 'GSTR-1', text: 'Outward supplies' },
  { id: 'GSTR2A', title: 'GSTR-2A', text: 'Auto drafted ITC' },
  { id: 'GSTR2B', title: 'GSTR-2B', text: 'Static ITC statement' },
]
const formatOptions = ['Excel', 'CSV', 'JSON']
const supportedExtensions = ['xlsx', 'xls', 'csv', 'json']
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(value || 0))
const signedMoney = value => `${Number(value || 0) > 0 ? '+' : Number(value || 0) < 0 ? '-' : ''}${money(Math.abs(Number(value || 0)))}`
const show = value => value === null || value === undefined || value === '' ? '-' : String(value)

export default function GstTallyImport({ user, onLogout }) {
  const [screen, setScreen] = useState('upload')
  const [returnType, setReturnType] = useState('GSTR1')
  const [fileFormat, setFileFormat] = useState('Excel')
  const [file, setFile] = useState(null)
  const [fileError, setFileError] = useState('')
  const [preview, setPreview] = useState(null)
  const [batch, setBatch] = useState(null)
  const [parties, setParties] = useState(null)
  const [connectionResult, setConnectionResult] = useState(null)
  const [partyProcessError, setPartyProcessError] = useState('')
  const [companyInfo, setCompanyInfo] = useState(null)
  const [companyName, setCompanyName] = useState('')
  const [companyResult, setCompanyResult] = useState(null)
  const [licenseResult, setLicenseResult] = useState(null)
  const [verifyStage, setVerifyStage] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [masterResult, setMasterResult] = useState(null)
  const [voucherResult, setVoucherResult] = useState(null)
  const [vouchersValidated, setVouchersValidated] = useState(false)
  const [reviewRow, setReviewRow] = useState(null)
  const [skipDetailRow, setSkipDetailRow] = useState(null)
  const [importResult, setImportResult] = useState(null)
  const [errorDetail, setErrorDetail] = useState(null)
  const [busy, setBusy] = useState('')
  const [phase, setPhase] = useState('')
  const [processingStartedAt, setProcessingStartedAt] = useState(0)
  const [processingBackendDone, setProcessingBackendDone] = useState(false)
  const [toast, setToast] = useState(null)

  const notify = (message, kind = 'success') => setToast({ message, kind })
  const resetWorkflow = () => {
    setScreen('upload')
    setFile(null)
    setFileError('')
    setPreview(null)
    setBatch(null)
    setParties(null)
    setConnectionResult(null)
    setPartyProcessError('')
    setCompanyInfo(null)
    setCompanyName('')
    setCompanyResult(null)
    setLicenseResult(null)
    setVerifyStage(null)
    setConfirmed(false)
    setMasterResult(null)
    setVoucherResult(null)
    setVouchersValidated(false)
    setReviewRow(null)
    setImportResult(null)
    setPhase('')
    window.history.pushState({}, '', '/gst-tally')
  }
  const go = next => {
    setScreen(next)
    window.history.pushState({}, '', `/gst-tally/${next}`)
  }
  const back = () => {
    const order = ['upload', 'preview', 'parties', 'masters', 'vouchers', 'import', 'result']
    const index = order.indexOf(screen)
    if (index <= 0) return resetWorkflow()
    go(order[index - 1])
  }
  const chooseReturnType = next => {
    setReturnType(next)
    resetWorkflow()
  }
  const chooseFile = selected => {
    setFileError('')
    if (!selected) return setFile(null)
    const extension = selected.name.split('.').pop().toLowerCase()
    if (!supportedExtensions.includes(extension)) {
      setFile(null)
      setFileError('Please choose an Excel, CSV, or JSON return file.')
      return
    }
    const detected = detectFileFormat(selected.name)
    setFile(selected)
    if (detected) setFileFormat(detected)
  }
  const startImport = async () => {
    if (!canStartImport({ returnType, file, fileFormat })) {
      setFileError('Select a return type, file format, and matching file before importing.')
      return
    }
    setBusy('import-file')
    const startedAt = Date.now()
    setProcessingStartedAt(startedAt)
    setProcessingBackendDone(false)
    try {
      setPhase('Preparing your data')
      const sourcePreview = await previewGSTFile({ returnType, file })
      setPhase('Organizing records')
      const imported = await uploadGSTFile({ returnType, returnPeriod: '', file })
      let resolved = {
        company: imported.company_details,
        company_gstin: imported.company_gstin,
        company_gstin_candidates: imported.company_gstin_candidates,
        status: imported.company_resolution_status,
        error: imported.company_resolution_error,
      }
      if (imported.company_gstin) {
        setPhase('Building preview grid')
        try { resolved = await resolveBatchCompany(imported.id) } catch {}
      }
      setProcessingBackendDone(true)
      const elapsed = Date.now() - startedAt
      const remaining = isProcessingComplete(elapsed, true) ? 400 : PROCESSING_READY_COMPLETE_MS - elapsed
      if (remaining > 0) await wait(remaining)
      setPreview(sourcePreview)
      setBatch(imported)
      setCompanyInfo(resolved)
      notify('File preview is ready.')
      go('preview')
    } catch (error) {
      notify(error.message || 'Unable to process the selected file.', 'error')
    } finally {
      setBusy('')
      setPhase('')
      setProcessingStartedAt(0)
      setProcessingBackendDone(false)
    }
  }
  const proceedFromPreview = async () => {
    if (!batch?.id) return
    setPartyProcessError('')
    setConnectionResult(null)
    setVerifyStage(null)
    go('parties')
    setBusy('parties')
    try {
      setPhase('Fetching party details')
      let result = await getBatchParties(batch.id)
      // "Pending" means this GSTIN has never actually been looked up (no
      // GSTParty record, or one that was never queried) -- the array itself
      // is never empty once the batch has any customer GSTINs, so checking
      // only `!parties.length` skipped the real Sandbox call entirely and
      // left every party stuck on GSTIN fallback. Trigger the live lookup
      // whenever at least one party still needs it; already-fresh/cached
      // parties are skipped server-side, so this never re-fetches them.
      const needsLiveLookup = !result?.parties?.length || result.parties.some(party => party.status === 'Pending')
      if (needsLiveLookup) result = await fetchBatchParties(batch.id)
      setParties(result)
      setPhase('Checking Tally connection')
      const connection = await getTallyConnection()
      setConnectionResult(connection)
      if (!connection?.can_import) {
        throw new Error(tallyConnectionErrorMessage(connection))
      }
      setVerifyStage('company')
    } catch (error) {
      const message = error.message || 'Unable to prepare your Tally import. Please try again.'
      setPartyProcessError(message)
      notify(message, 'error')
    } finally {
      setBusy('')
      setPhase('')
    }
  }
  const resumeVerification = () => {
    if (confirmed) return go('masters')
    if (!companyResult?.company_verified) return setVerifyStage('company')
    if (!licenseResult?.license_verified) return setVerifyStage('license')
    setVerifyStage('confirm')
  }
  const verifyCompany = async event => {
    event?.preventDefault?.()
    const enteredCompany = companyName.trim()
    if (!enteredCompany || !batch?.id) return
    setBusy('company')
    try {
      const payload = { tally_company_name: enteredCompany }
      console.debug('Verify Company request', { batch_id: batch.id, payload })
      const rawResult = await prepareTallyMasters(batch.id, enteredCompany)
      console.debug('Verify Company response', rawResult)
      const result = normalizeCompanyVerificationResult(rawResult, enteredCompany)
      setCompanyResult(result)
      setMasterResult(result)
      if (result.company_verified) {
        setCompanyInfo(current => ({
          ...(current || {}),
          company: result.company,
          company_gstin: result.company_gstin,
          tally_verified: true,
        }))
        notify('Tally company verified.')
      } else {
        notify(result.message || result.error || 'Company verification failed.', 'error')
      }
    } catch (error) {
      setCompanyResult({
        company_verified: false,
        entered_company: enteredCompany,
        detected_company: '',
        company_gstin: '',
        company_state: '',
        error: error.code || error.message || 'COMPANY_VERIFICATION_FAILED',
        message: error.message || 'Unable to verify Tally company.',
      })
      notify(error.message || 'Unable to verify Tally company.', 'error')
    } finally {
      setBusy('')
    }
  }
  const confirmCompanyStage = () => {
    const shouldAutoCheckLicense = !licenseResult?.license_verified
    setVerifyStage('license')
    if (shouldAutoCheckLicense) verifyLicense()
  }
  const verifyLicense = async () => {
    if (!batch?.id) return
    setBusy('license')
    try {
      const result = await verifyTallyLicense(batch.id)
      setLicenseResult(result)
      notify(
        result.license_verified ? '✓ License verified successfully.'
          : result.license_error === 'LICENSE_IDENTITY_MISMATCH' ? 'Tally License Mismatch'
          : 'Unable to read Tally license information.',
        result.license_verified ? 'success' : 'error',
      )
    } catch (error) {
      setLicenseResult({ license_available: false, license_verified: false, license_error: 'TALLY_LICENSE_DATA_UNAVAILABLE', message: error.message })
      notify(error.message || 'Unable to read Tally license information.', 'error')
    } finally {
      setBusy('')
    }
  }
  const cancelLicenseStage = () => setVerifyStage(null)
  const confirmLicenseStage = () => setVerifyStage('confirm')
  const prepareMastersForDisplay = async () => {
    if (!masterResult && companyName.trim()) await verifyCompany({ preventDefault() {} })
    setConfirmed(true)
    setVerifyStage(null)
    go('masters')
  }
  const refreshMastersStatus = async () => {
    if (!batch?.id) return
    setBusy('masters')
    try {
      const rawResult = await prepareTallyMasters(batch.id, companyName.trim())
      const result = normalizeCompanyVerificationResult(rawResult, companyName.trim())
      setCompanyResult(result)
      setMasterResult(result)
      notify('Tally master statuses refreshed.')
    } catch (error) {
      notify(error.message || 'Unable to refresh Tally master statuses.', 'error')
    } finally {
      setBusy('')
    }
  }
  const loadVouchers = async () => {
    if (!batch?.id) return
    setBusy('vouchers')
    try {
      const result = await previewTallyVouchers(batch.id)
      setVoucherResult(result)
      setVouchersValidated((result.summary?.eligible || 0) > 0)
      notify((result.summary?.eligible || 0) > 0 ? 'Voucher preview validated.' : 'No eligible vouchers are available.', (result.summary?.eligible || 0) > 0 ? 'success' : 'error')
    } catch (error) {
      notify(error.message || 'Unable to validate vouchers.', 'error')
    } finally {
      setBusy('')
    }
  }
  const runImport = async () => {
    if (!batch?.id) return
    setBusy('tally-import')
    try {
      const result = await importToTally(batch.id)
      setImportResult(result)
      const summary = finalImportToast(result)
      notify(`${summary.title}\n${summary.message}`, summary.kind)
      go('result')
    } catch (error) {
      notify(error.message || 'Unable to import to Tally.', 'error')
    } finally {
      setBusy('')
    }
  }
  const correctVoucher = updatedPreview => {
    setVoucherResult(updatedPreview)
    setVouchersValidated((updatedPreview.summary?.eligible || 0) > 0)
  }
  const selectCompanyGstin = async gstin => {
    try {
      const result = await resolveBatchCompany(batch.id, gstin)
      setCompanyInfo(result)
      notify(`Company GSTIN ${gstin} selected.`)
    } catch (error) {
      notify(error.message || 'Unable to select company GSTIN.', 'error')
    }
  }

  const activeStep = ['upload'].includes(screen) ? 'upload' : ['preview'].includes(screen) ? 'preview' : screen === 'parties' ? 'parties' : screen
  const prepState = buildPrepState(screen, { batch, parties, connectionResult, verifyStage, companyResult, licenseResult, confirmed })

  return <AppShell user={user} onHome={resetWorkflow} onLogout={onLogout} canGoBack={screen !== 'upload'} onBack={back}>
    <StepProgress current={activeStep} />
    {screen === 'upload' && <UploadScreen returnType={returnType} fileFormat={fileFormat} file={file} fileError={fileError} loading={busy === 'import-file'} phase={phase} processingStartedAt={processingStartedAt} processingBackendDone={processingBackendDone} onReturnType={chooseReturnType} onFormat={setFileFormat} onFile={chooseFile} onImport={startImport} />}
    {screen === 'preview' && <PreviewScreen preview={preview} batch={batch} loading={busy === 'parties'} phase={phase} onProceed={proceedFromPreview} />}
    {screen === 'parties' && <>
      <PartyScreen loading={busy === 'parties'} prepState={prepState} error={partyProcessError} verifyStage={verifyStage} onRetry={proceedFromPreview} onContinue={resumeVerification} />
      {verifyStage === 'company' && <CompanyVerifyModal companyInfo={companyInfo} companyName={companyName} result={companyResult} loading={busy === 'company'} onCompanyName={setCompanyName} onVerify={verifyCompany} onSelectGstin={selectCompanyGstin} onClose={() => setVerifyStage(null)} onConfirm={confirmCompanyStage} />}
      {verifyStage === 'license' && <LicenseVerifyModal status={licenseResult} loading={busy === 'license'} onRetry={verifyLicense} onCancel={cancelLicenseStage} onConfirm={confirmLicenseStage} />}
      {verifyStage === 'confirm' && <ConfirmMastersModal companyResult={companyResult} licenseResult={licenseResult} onConfirm={prepareMastersForDisplay} />}
    </>}
    {screen === 'masters' && <MastersScreen result={masterResult} loading={busy === 'masters'} onRefresh={refreshMastersStatus} onContinue={() => go('vouchers')} />}
    {screen === 'vouchers' && <VoucherScreen result={voucherResult} loading={busy === 'vouchers'} validated={vouchersValidated} batchId={batch?.id} reviewRow={reviewRow} onValidate={loadVouchers} onCorrected={correctVoucher} onReview={setReviewRow} onViewSkip={setSkipDetailRow} onContinue={() => go('import')} />}
    {skipDetailRow && <SkipReasonModal row={skipDetailRow} onClose={() => setSkipDetailRow(null)} />}
    {screen === 'import' && <ImportScreen result={importResult} voucherResult={voucherResult} companyResult={companyResult} licenseResult={licenseResult} masterResult={masterResult} loading={busy === 'tally-import'} onImport={runImport} onReview={row => { setReviewRow(row); go('vouchers') }} onViewError={setErrorDetail} />}
    {screen === 'result' && <ResultScreen result={importResult} onNew={resetWorkflow} onViewError={setErrorDetail} />}
    <Toast toast={toast} onDismiss={() => setToast(null)} />
    {errorDetail && <ErrorDetailModal row={errorDetail} onClose={() => setErrorDetail(null)} />}
  </AppShell>
}

function UploadScreen({ returnType, fileFormat, file, fileError, loading, phase, processingStartedAt, processingBackendDone, onReturnType, onFormat, onFile, onImport }) {
  return <section className="screen upload-screen">
    <header className="screen-title"><h1>Import GST Return</h1><p>Select return type and upload your GST return file.</p></header>
    {loading ? <ProcessingOverlay startedAt={processingStartedAt} backendDone={processingBackendDone} /> : <>
      <div className="return-grid">{returnTypes.map(item => <button key={item.id} className={`return-option ${returnType === item.id ? 'active' : ''}`} onClick={() => onReturnType(item.id)}>
        <strong>{item.title}</strong><span>{item.text}</span>
        {returnType === item.id && <span className="return-check" aria-hidden="true">✓</span>}
      </button>)}</div>
      <fieldset className="format-field"><legend>File Format</legend>{formatOptions.map(option => <label key={option}><input type="radio" checked={fileFormat === option} onChange={() => onFormat(option)} /> {option}</label>)}</fieldset>
      <FileDrop file={file} fileFormat={fileFormat} disabled={loading} error={fileError} onFile={onFile} />
    </>}
    <div className="action-bar"><LoadingButton loading={loading} disabled={!canStartImport({ returnType, file, fileFormat })} onClick={onImport}>{loading ? phase || 'Importing' : 'Import File'}</LoadingButton></div>
  </section>
}

function LegacyProcessingOverlay() {
  return null
  /*
  return <div className="process-overlay" aria-live="polite">
    <div className="process-overlay-card">
      <span aria-hidden="true" />
      <h2>Processing</h2>
      <div className="process-checklist">{uploadChecklist.map((label, index) => {
        const itemNumber = index + 1
        const status = step >= itemNumber ? 'done' : step === itemNumber - 1 ? 'active' : 'pending'
        return <div key={label} className={`process-checklist-item ${status}`}><span className="check-icon">{status === 'done' ? '✓' : ''}</span>{label}</div>
      })}</div>
    </div>
  </div>
  */
}

function ProcessingOverlay({ startedAt, backendDone }) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!startedAt) return
    const update = () => setElapsed(Date.now() - startedAt)
    update()
    const timer = window.setInterval(update, 120)
    return () => window.clearInterval(timer)
  }, [startedAt])

  const stage = getProcessingStage(elapsed, backendDone)
  const visualMode = getProcessingVisualMode(elapsed, backendDone)
  const ready = stage.key === 'ready'
  const progress = Math.min(100, Math.round((Math.min(elapsed, PROCESSING_READY_COMPLETE_MS) / PROCESSING_READY_COMPLETE_MS) * 100))

  return <div className="process-overlay" aria-live="polite">
    <div className={`process-overlay-card process-stage-${stage.key} process-visual-${visualMode} ${ready ? 'is-ready' : ''}`}>
      <DataTransferAnimation mode={visualMode} ready={ready} />
      <div className="process-copy" key={stage.key}>
        <h2>{ready ? <span className="ready-inline-check">✓</span> : null}{stage.title}</h2>
        <p>{stage.subtitle}</p>
      </div>
      <div className="process-progress" aria-label="Processing progress">
        <span style={{ width: `${progress}%` }} />
      </div>
    </div>
  </div>
}

function FileDrop({ file, fileFormat, disabled, error, onFile }) {
  const [dragging, setDragging] = useState(false)
  return <label className={`file-drop ${dragging ? 'dragging' : ''}`}>
    <input type="file" accept=".xlsx,.xls,.csv,.json" disabled={disabled} onChange={event => onFile(event.target.files[0] || null)} onDragEnter={() => setDragging(true)} />
    <span className="drop-symbol">{file ? 'Ready' : 'Upload'}</span>
    {file ? <><strong>{file.name}</strong><small>{fileFormat} file selected · ✓ Ready</small></> : <><strong>Drag and drop or choose file</strong><small>Supported: Excel, CSV, JSON</small></>}
    {error && <em role="alert">{error}</em>}
  </label>
}

const PREVIEW_PAGE_SIZE = 10

function PreviewScreen({ preview, batch, loading, phase, onProceed }) {
  const [page, setPage] = useState(1)
  const allRows = preview?.rows || []
  const totalPages = Math.max(1, Math.ceil(allRows.length / PREVIEW_PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const start = (currentPage - 1) * PREVIEW_PAGE_SIZE
  const pageRows = allRows.slice(start, start + PREVIEW_PAGE_SIZE)
  return <section className="screen">
    <header className="screen-title horizontal"><div><h1>File Preview</h1><p>{preview?.return_type || batch?.return_type || ''} · {preview?.file_name || batch?.file_name || 'Uploaded return'} · {allRows.length} invoices — read only source data.</p></div><DownloadButtons preview={preview} /></header>
    <MetaStrip items={[['Return', preview?.return_type || batch?.return_type], ['Rows', allRows.length], ['Batch', batch?.id ? `#${batch.id}` : '-'], ['Sheet', preview?.sheet_name || '-']]} />
    <ExcelPreviewGrid preview={preview ? { ...preview, rows: pageRows } : { columns: [], rows: [] }} />
    {allRows.length > 0 && <div className="pagination-bar">
      <span>Showing {start + 1} to {Math.min(start + PREVIEW_PAGE_SIZE, allRows.length)} of {allRows.length} entries</span>
      <div className="pagination">
        <button disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>
        {Array.from({ length: totalPages }, (_, index) => index + 1).map(number => <button key={number} className={number === currentPage ? 'current' : ''} onClick={() => setPage(number)}>{number}</button>)}
        <button disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button>
      </div>
    </div>}
    <div className="action-bar"><LoadingButton loading={loading} onClick={onProceed}>{loading ? phase || 'Preparing' : 'Proceed'}</LoadingButton></div>
  </section>
}

const PREP_ITEMS = [
  ['parties', 'Reading invoice parties'],
  ['fetch', 'Fetching party details'],
  ['connection', 'Checking Tally connection'],
  ['company', 'Verifying company'],
  ['license', 'Checking Tally license'],
  ['masters', 'Preparing masters'],
]
const PREP_STATUS_LABEL = { done: 'Completed', active: 'In progress', failed: 'Failed', pending: 'Pending' }

function PartyScreen({ loading, prepState, error, verifyStage, onRetry, onContinue }) {
  const connectionDone = prepState.connection === 'done'
  const fullyVerified = prepState.masters === 'done'
  const showResume = !loading && !error && connectionDone && !fullyVerified && !verifyStage
  return <section className="screen preparing-import-screen">
    <header className="screen-title"><h1>Preparing Tally Import</h1><p>Fetching party details and verifying Tally connection...</p></header>
    <div className="preparing-import-layout">
      <div className="prep-checklist-detailed" aria-live="polite">{PREP_ITEMS.map(([key, label]) => {
        const status = prepState[key] || 'pending'
        return <div key={key} className={`prep-row prep-row-${status}`}>
          <span className="prep-row-icon">{status === 'done' ? '✓' : status === 'failed' ? '×' : status === 'active' ? <span className="prep-row-spinner" aria-hidden="true" /> : '○'}</span>
          <span className="prep-row-label">{label}</span>
          <span className="prep-row-status">{PREP_STATUS_LABEL[status]}</span>
        </div>
      })}</div>
      <ServerConnectionIllustration />
    </div>
    {error && <Alert kind="error">{error} <button type="button" className="text-button" onClick={onRetry}>Retry</button></Alert>}
    <p className="info-note"><i aria-hidden="true">i</i> Please do not close this window while we prepare your import process.</p>
    {showResume && <div className="action-bar"><LoadingButton onClick={onContinue}>Continue Verification</LoadingButton></div>}
  </section>
}

function ServerConnectionIllustration() {
  return <svg className="prep-illustration" viewBox="0 0 160 160" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <circle cx="80" cy="80" r="72" fill="currentColor" fillOpacity=".06" />
    <rect x="40" y="34" width="80" height="26" rx="6" fill="#fff" stroke="currentColor" strokeOpacity=".3" />
    <rect x="40" y="67" width="80" height="26" rx="6" fill="#fff" stroke="currentColor" strokeOpacity=".3" />
    <rect x="40" y="100" width="80" height="26" rx="6" fill="#fff" stroke="currentColor" strokeOpacity=".3" />
    <circle cx="52" cy="47" r="3" fill="#159447" />
    <circle cx="52" cy="80" r="3" fill="#159447" />
    <circle cx="52" cy="113" r="3" fill="#ffc928" />
    <rect x="62" y="43" width="40" height="7" rx="3" fill="currentColor" fillOpacity=".18" />
    <rect x="62" y="76" width="40" height="7" rx="3" fill="currentColor" fillOpacity=".18" />
    <rect x="62" y="109" width="40" height="7" rx="3" fill="currentColor" fillOpacity=".18" />
  </svg>
}

function CompanyVerifyModal({ companyInfo, companyName, result, loading, onCompanyName, onVerify, onSelectGstin, onClose, onConfirm }) {
  const company = result?.company || companyInfo?.company || {}
  const candidates = companyInfo?.company_gstin_candidates || []
  const verified = Boolean(result?.company_verified)
  const attempted = Boolean(result)
  const status = companyVerificationStatus(result)
  const statusTone = status.tone === 'success' ? 'success' : status.tone === 'error' ? 'error' : 'pending'
  const enteredCompany = result?.entered_company || companyName || '-'
  const detectedCompany = result?.detected_company || company.company_name || company.company || '-'
  const companyGstin = result?.company_gstin || company.gstin || companyInfo?.company_gstin || '-'
  const expectedGstin = result?.uploaded_gstin || result?.expected_gstin || '-'
  const companyState = result?.company_state || company.state || '-'
  const gstinMatched = result?.gstin_match === true
  const nameDiffersButGstinMatched = verified && result?.company_name_match === false
  const handleVerify = event => {
    event?.preventDefault()
    onVerify(event)
  }
  return <div className="modal-backdrop verify-stage-backdrop company-verification-backdrop"><div className="modal verify-modal company-verification-modal" role="dialog" aria-modal="true" aria-labelledby="company-verification-title">
    <header className="company-verification-header">
      <div className="company-verification-heading">
        <span className="company-verification-icon" aria-hidden="true" />
        <div>
          <h1 id="company-verification-title">Company Verification</h1>
          <p>Verify the selected company with the company currently open in Tally.</p>
        </div>
      </div>
      <button type="button" className="company-verification-close" aria-label="Close company verification" onClick={onClose}>&times;</button>
    </header>
    <div className="company-verification-content">
      <section className="company-input-section" aria-labelledby="tally-company-section">
        <h2 id="tally-company-section">Tally Company</h2>
        <form className="company-verify-form" onSubmit={handleVerify}>
          <label className="form-field"><span>Tally Company Name</span><input value={companyName} onChange={event => onCompanyName(event.target.value)} placeholder="Enter exact Tally company name" autoFocus /></label>
          <LoadingButton type="submit" loading={loading} disabled={!companyName.trim()}>{loading ? 'Verifying...' : verified ? 'Verified' : 'Verify Company'}</LoadingButton>
        </form>
        {companyInfo?.error === 'MULTIPLE_COMPANY_GSTINS' && <label className="form-field compact-field company-gstin-select"><span>Select Company GSTIN</span><select defaultValue="" onChange={event => event.target.value && onSelectGstin(event.target.value)}><option value="">Select GSTIN</option>{candidates.map(value => <option key={value}>{value}</option>)}</select></label>}
      </section>
      {attempted && <section className="company-verification-details" aria-labelledby="company-verification-details-title">
        <h2 id="company-verification-details-title">Verification Details</h2>
        <div className="company-result-grid">
          <IdentityItem label="Entered Company" value={enteredCompany} />
          <IdentityItem label="Detected Company" value={detectedCompany} />
          <IdentityItem label="Expected GSTIN" value={expectedGstin} />
          <IdentityItem label="Detected GSTIN" value={companyGstin} />
          <IdentityItem label="State" value={companyState} />
        </div>
        <div className={`company-status-row is-${statusTone}`} role="status" aria-live="polite">
          <span className="company-status-message"><i aria-hidden="true" />{verified ? 'Company details match' : 'Company details need review'}</span>
          <span className="company-status-badge"><i aria-hidden="true" />{status.label}</span>
        </div>
        {gstinMatched && <div className="company-status-row is-success" role="status"><span className="company-status-message"><i aria-hidden="true" />✓ GSTIN Matched</span></div>}
    </section>}
    {attempted && !verified && <Alert kind="error"><strong>Company Mismatch.</strong> {result?.message || result?.error || 'The entered company does not match the currently open Tally company.'}</Alert>}
      {verified && <Alert kind="success"><strong>Company successfully verified</strong>{nameDiffersButGstinMatched && <span> Company names differ slightly, but the GSTIN matches the currently open Tally company.</span>}</Alert>}
    </div>
    <footer className="company-verification-footer">
      <p>{verified ? 'Company identity verified with the currently open Tally company.' : 'Verify the Tally company before continuing.'}</p>
      <LoadingButton disabled={!verified} onClick={onConfirm}>Continue</LoadingButton>
    </footer>
  </div></div>
}

function LicenseVerifyModal({ status, loading, onRetry, onCancel, onConfirm }) {
  const verified = Boolean(status?.license_verified)
  const attempted = Boolean(status)
  const mismatch = status?.license_error === 'LICENSE_IDENTITY_MISMATCH'
  const administratorUnreadable = status?.license_error === 'LICENSE_ADMINISTRATOR_UNREADABLE'
  const unavailable = attempted && status?.license_available === false
  const licenseStatus = licenseVerificationStatus(status)
  return <div className="modal-backdrop verify-stage-backdrop"><div className="modal verify-modal license-verification-modal">
    <header className="screen-title license-verification-title"><h1>Tally License Verification</h1><p>License identity must come from the current Tally connection response.</p></header>
    <section className="identity-grid license-result-grid">
      <IdentityItem label="Serial Number" value={status?.serial_number || '-'} />
      <IdentityItem label="Edition" value={status?.edition || '-'} />
      <IdentityItem label="Tally Software Services" value={status?.tally_software_services || '-'} />
      <IdentityItem label="License Administrator" value={status?.license_administrator || '-'} />
      <IdentityItem label="Status" value={loading ? 'Checking' : licenseStatus.label} status={loading ? 'pending' : licenseStatus.tone} />
    </section>
    {verified && <Alert kind="success"><strong>✓ License verified successfully.</strong><span>This license identity will be used for this company.</span></Alert>}
    {mismatch && <Alert kind="error">
      <strong>⚠ License Mismatch.</strong> This company was previously verified with a different Tally license.<br />
      Expected Serial: {status.expected_serial || '-'} · Detected Serial: {status.serial_number || '-'}<br />
      Expected Administrator: {status.expected_administrator || '-'} · Detected Administrator: {status.license_administrator || '-'}<br />
      Status: Blocked
    </Alert>}
    {administratorUnreadable && <Alert kind="error">Tally returned the serial number and edition, but not a license administrator. {status.message}</Alert>}
    {unavailable && <Alert kind="error">Unable to read Tally license information. {status.message || 'Tally could not return license details for this request.'}</Alert>}
    <div className="modal-actions license-action-bar">
      {verified
        ? <LoadingButton onClick={onConfirm}>Confirm &amp; Continue</LoadingButton>
        : <>
          <button type="button" onClick={onCancel}>Cancel</button>
          <LoadingButton loading={loading} onClick={onRetry}>{loading ? 'Checking' : 'Retry'}</LoadingButton>
        </>}
    </div>
  </div></div>
}

function ConfirmMastersModal({ companyResult, licenseResult, onConfirm }) {
  const readiness = confirmMastersReadiness(companyResult, licenseResult)
  const company = companyResult?.company || {}
  const financialYear = company.financial_year || ''
  console.log("Confirm modal company response:", company)
  console.log("financial_year:", company?.financial_year)
  console.log("financial_year_from:", company?.financial_year_from)
  console.log("financial_year_to:", company?.financial_year_to)
  console.log({
    companyVerified: readiness.companyVerified,
    licenseVerified: readiness.licenseVerified,
    tallyConnected: readiness.tallyConnected,
    financialYear: company?.financial_year,
    financialYearFrom: company?.financial_year_from,
    financialYearTo: company?.financial_year_to,
    canPrepareMasters: readiness.ready,
  })
  return <div className="modal-backdrop verify-stage-backdrop"><div className="modal verify-modal confirm-masters-modal">
    <header className="screen-title confirm-masters-title"><h1>Confirm & Prepare Masters</h1><p>Review company and license details before we prepare Tally masters.</p></header>
    <section className="confirm-grid-2 confirm-masters-grid">
      <SummaryPanel title="Company Details" items={[['Company Name', company.company_name || company.company || '-'], ['GSTIN', company.gstin || '-'], ['State', company.state || '-'], ['Financial Year', financialYear || 'Unavailable']]} />
      <SummaryPanel title="Tally License Details" items={[['Serial Number', licenseResult?.serial_number || '-'], ['Edition', licenseResult?.edition || '-'], ['Tally Software Services', licenseResult?.tally_software_services || '-'], ['License Administrator', licenseResult?.license_administrator || '-']]} />
    </section>
    <div className="status-strip confirm-verification-bar">
      <span className={readiness.companyVerified ? 'is-verified' : 'is-pending'}><i>{readiness.companyVerified ? '✓' : '○'}</i> Company Verified</span>
      <span className={readiness.licenseVerified ? 'is-verified' : 'is-pending'}><i>{readiness.licenseVerified ? '✓' : '○'}</i> License Verified</span>
      <span className={readiness.tallyConnected ? 'is-verified' : 'is-pending'}><i>{readiness.tallyConnected ? '✓' : '○'}</i> Tally Connected</span>
    </div>
    <div className="modal-actions confirm-masters-action"><LoadingButton disabled={!readiness.ready} onClick={onConfirm}>Confirm &amp; Prepare Masters</LoadingButton></div>
  </div></div>
}

function MastersScreen({ result, loading, onRefresh, onContinue }) {
  const [filter, setFilter] = useState('all')
  const masters = result?.masters || []
  const summaries = mastersSummaryFromRows(masters)
  const categorySummaries = summaries.filter(item => item.key !== 'failed')
  const failedCount = summaries.find(item => item.key === 'failed')?.completed || 0
  const visibleMasters = filterMastersRows(masters, filter)
  const canContinue = Boolean(result?.ready)
  // A completed master check can legitimately return zero rows (every required
  // master already exists, or none are needed yet) -- that must read as a normal
  // completed state, not as "preparation never ran". Only the latter (or an
  // explicit failure) should block Continue / show a diagnostic reason.
  const blockingReason = !result ? 'Master preparation has not completed yet.'
    : !canContinue ? (result.message || 'Master preparation has not completed yet.') : ''
  // Existing masters are not equivalent to zero masters required -- these two
  // completed states read very differently and must not share one message.
  const requiredCount = categorySummaries.reduce((sum, item) => sum + item.total, 0)
  const readyCount = categorySummaries.reduce((sum, item) => sum + item.completed, 0)
  const createdCount = categorySummaries.reduce((sum, item) => sum + (item.byStatus?.created || 0), 0)
  const allRequiredAlreadyAvailable = canContinue && requiredCount > 0 && readyCount === requiredCount && createdCount === 0
  const emptyMessage = filter === 'failed' ? 'No failed master rows returned.'
    : !canContinue ? 'Master preparation has not completed yet.'
    : requiredCount === 0 ? 'No Tally masters are required for this batch.'
    : 'All required Tally masters are already available for this batch.'
  console.log('MASTER STATUS RESPONSE:', result)
  console.log('MASTER STATUS GATE', {
    batchId: result?.batch_id,
    partyTotal: summaries.find(item => item.key === 'parties')?.total,
    accountTotal: summaries.find(item => item.key === 'accounts')?.total,
    taxLedgerTotal: summaries.find(item => item.key === 'taxLedgers')?.total,
    otherLedgerTotal: summaries.find(item => item.key === 'otherLedgers')?.total,
    failedCount, masterRowsLength: masters.length, mastersReady: result?.ready, canContinue,
    requiredCount, readyCount, createdCount,
  })
  return <section className="screen masters-screen">
    <header className="screen-title horizontal masters-title">
      <div><h1>Tally Masters</h1><p>Statuses are shown only from backend master preparation results.</p></div>
      <LoadingButton loading={loading} disabled={loading} onClick={onRefresh}>{loading ? 'Refreshing' : 'Refresh Status'}</LoadingButton>
    </header>
    <div className="masters-summary-grid">
      {summaries.map(item => <MasterSummaryCard key={item.key} item={item} onFailedClick={() => failedCount > 0 && setFilter('failed')} />)}
    </div>
    {allRequiredAlreadyAvailable && <Alert kind="success">All required Tally masters are already available for this batch.</Alert>}
    {filter === 'failed' && <div className="masters-filter-bar"><span>Showing failed items</span><button type="button" onClick={() => setFilter('all')}>Show all</button></div>}
    <DataTable columns={['Master Type', 'Name', 'Group', 'GST Rate / Tax Type', 'Status', 'Message']} rows={visibleMasters} empty={emptyMessage} render={(row, index) => <tr key={`${row.name}-${index}`}>
      <td>{row.master_type || row.type || '-'}</td><td>{row.name || '-'}</td><td>{row.group || '-'}</td><td>{row.gst_rate || row.tax_type || '-'}</td><td><StatusBadge value={row.status || 'Pending'} /></td><td title={row.message || ''}>{row.message || '-'}</td>
    </tr>} />
    <div className="action-bar"><LoadingButton disabled={!canContinue} onClick={onContinue}>Continue to Voucher Preview</LoadingButton></div>
    {blockingReason && <p className="import-block-reason">{blockingReason}</p>}
  </section>
}

function MasterSummaryCard({ item, onFailedClick }) {
  const isFailed = item.key === 'failed'
  return <article className={`master-summary-card ${item.key}`}>
    <div className="master-summary-head">
      <span className="master-summary-icon" aria-hidden="true"><MasterSummaryIcon type={item.key} /></span>
      <strong>{item.label}</strong>
    </div>
    <div className="master-summary-value">{isFailed ? item.completed : `${item.completed} / ${item.total}`}</div>
    <div className="master-summary-subtext">
      {isFailed
        ? item.completed > 0 ? <button type="button" onClick={onFailedClick}>View failed items</button> : 'No failures'
        : <><span>{item.percent}% Ready</span>{item.completed > 0 && <small>{readyBreakdownText(item.byStatus)}</small>}</>}
    </div>
    {!isFailed && <div className="master-summary-progress" aria-hidden="true"><span style={{ width: `${item.percent}%` }} /></div>}
  </article>
}

function MasterSummaryIcon({ type }) {
  const common = { width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }
  if (type === 'parties') return <svg {...common}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
  if (type === 'accounts') return <svg {...common}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z" /><path d="M8 7h8" /><path d="M8 11h6" /></svg>
  if (type === 'taxLedgers') return <svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h5" /></svg>
  if (type === 'otherLedgers') return <svg {...common}><path d="M21 8V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v1" /><path d="M3 8h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M8 12h8" /></svg>
  return <svg {...common}><circle cx="12" cy="12" r="10" /><path d="m15 9-6 6" /><path d="m9 9 6 6" /></svg>
}

function VoucherScreen({ result, loading, validated, batchId, reviewRow, onValidate, onCorrected, onReview, onViewSkip, onContinue }) {
  const [filter, setFilter] = useState('All')
  const vouchers = (result?.vouchers || []).map(row => ({ ...row, invoice_date_iso: row.invoice_date, invoice_date: formatDate(row.invoice_date) }))
  const mismatchStatuses = ['Review Required', 'Validation Failed']
  const filtered = vouchers.filter(row => filter === 'All' || (filter === 'Needs Attention' ? mismatchStatuses.includes(row.status) : row.status === filter))
  const ready = vouchers.filter(row => String(row.status || '').startsWith('Ready')).length
  const mismatch = vouchers.filter(row => mismatchStatuses.includes(row.status)).length
  return <section className="screen">
    <header className="screen-title horizontal"><div><h1>Voucher Preview</h1><p>Validate invoice vouchers and correct mismatches before import.</p></div><LoadingButton loading={loading} onClick={onValidate}>{loading ? 'Validating' : 'Generate & Validate Vouchers'}</LoadingButton></header>
    <MetaStrip items={[['Total Invoices', vouchers.length], ['Ready', ready], ['Needs Attention', mismatch], ['Eligible', result?.summary?.eligible || 0]]} />
    <div className="filter-tabs">{['All', 'Ready', 'Needs Attention', 'Already Imported', 'Skipped'].map(item => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div>
    {loading && !result ? <TableSkeleton cols={11} /> : <DataTable columns={['Invoice', 'Date', 'Party', 'GSTIN', 'Taxable', 'CGST', 'SGST', 'IGST', 'Invoice Total', 'Difference', 'Status', 'Action']} rows={filtered} empty="No vouchers match this filter." render={(row, index) => {
      const mismatchRow = mismatchStatuses.includes(row.status)
      const partyName = row.party_name || row.party?.name || row.party?.trade_name || row.party?.legal_name || row.party_gstin || row.party?.gstin || '-'
      const partyGstin = row.party_gstin || row.party?.gstin || '-'
      return <tr key={`${row.invoice_number}-${index}`} className={mismatchRow ? 'mismatch-row' : ''}>
        <td>{row.invoice_number || '-'}</td><td>{row.invoice_date || '-'}</td><td>{partyName}</td><td>{partyGstin}</td>
        <td>{money(row.taxable_total)}</td><td>{money(row.cgst)}</td><td>{money(row.sgst)}</td><td>{money(row.igst)}</td><td>{money(row.invoice_total)}</td><td>{signedMoney(row.difference)}</td>
        <td><StatusBadge value={mismatchRow ? 'Needs Attention' : row.status || 'Invalid'} /></td><td><button className="icon-link" onClick={() => row.status === 'Skipped' || row.warning_code ? onViewSkip(row) : onReview(row)}>{mismatchRow ? '⚠ Fix' : 'View'}</button></td>
      </tr>
    }} />}
    <div className="action-bar"><LoadingButton disabled={!validated} onClick={onContinue}>Continue to Import</LoadingButton></div>
    {reviewRow && <VoucherMismatchModal row={reviewRow} batchId={batchId} onClose={() => onReview(null)} onSaved={onCorrected} />}
  </section>
}

function ImportScreen({ result, voucherResult, companyResult, licenseResult, masterResult, loading, onImport, onReview, onViewError }) {
  const summary = voucherResult?.summary || {}
  const readyCount = Number(summary.eligible || 0)
  const needsReviewCount = Number(summary.validation_failed || 0) + Number(summary.invalid || 0)
  const readiness = confirmMastersReadiness(companyResult, licenseResult)
  const mastersReady = Boolean(masterResult?.ready)
  const blockingReason = readyCount === 0 ? 'No ready vouchers available.'
    : !readiness.tallyConnected ? 'Tally connection is unavailable.'
    : !readiness.companyVerified ? 'Company verification is required.'
    : !readiness.licenseVerified ? 'License verification is required.'
    : !mastersReady ? 'Required Tally masters are not ready.'
    : ''
  const canImport = !blockingReason
  console.log('IMPORT BUTTON GATE', {
    readyCount, needsReviewCount, eligibleCount: readyCount,
    companyVerified: readiness.companyVerified, licenseVerified: readiness.licenseVerified,
    tallyConnected: readiness.tallyConnected, mastersReady, isImporting: loading, importAllowed: canImport,
  })
  return <section className="screen">
    <header className="screen-title"><h1>Import to Tally</h1><p>{needsReviewCount ? `${readyCount} vouchers are ready. ${needsReviewCount} vouchers require correction.` : 'Ready vouchers will be sent to the verified Tally company.'}</p></header>
    <MetaStrip items={[['Eligible', summary.eligible || 0], ['Ready', summary.ready || 0], ['Needs review', needsReviewCount], ['Last status', result?.import_status || '-']]} />
    {readyCount > 0 && needsReviewCount > 0 && <Alert kind="warning">{needsReviewCount} voucher{needsReviewCount === 1 ? '' : 's'} require{needsReviewCount === 1 ? 's' : ''} correction and will be skipped.<br />{readyCount} ready voucher{readyCount === 1 ? '' : 's'} can be imported now.</Alert>}
    <div className="action-bar"><LoadingButton loading={loading} disabled={!canImport} onClick={onImport}>{loading ? `Importing ${readyCount} Vouchers...` : `Import ${readyCount} Ready Voucher${readyCount === 1 ? '' : 's'} to Tally`}</LoadingButton></div>
    {!canImport && !loading && <p className="import-block-reason">{blockingReason}</p>}
    {loading && <div className="progress-indeterminate" aria-label="Importing to Tally"><i /></div>}
    {result?.results?.length && <ImportResultsTable result={result} onReview={onReview} onViewError={onViewError} />}
  </section>
}

function ResultScreen({ result, onNew, onViewError }) {
  const summary = result?.summary || {}
  const failed = summary.failed || summary.tally_failed || 0
  const allImported = failed === 0 && (summary.skipped || 0) === 0
  return <section className="screen">
    <header className="screen-title"><h1>Tally Import Complete</h1><p>{allImported ? '✓ Successfully Imported to Tally' : 'Import Completed with Issues'}</p></header>
    <MetaStrip items={[['Total', summary.total || 0], ['Imported', summary.imported || 0], ['Failed', failed], ['Skipped', summary.skipped || 0]]} />
    <ImportResultsTable result={result} onViewError={onViewError} />
    <div className="action-bar"><LoadingButton onClick={onNew}>Start New Import</LoadingButton></div>
  </section>
}

function ImportResultsTable({ result, onReview, onViewError }) {
  const rows = (result?.results || []).map(row => ({ ...row, date: formatDate(row.date) }))
  return <DataTable columns={['Invoice', 'Date', 'Party', 'GSTIN', 'Voucher Type', 'Status', 'Voucher ID', 'Reason', 'Details']} rows={rows} empty="No import result rows yet." render={(row, index) => <tr key={`${row.invoice_no}-${index}`}>
    <td>{row.invoice_no || '-'}</td><td>{row.date || '-'}</td><td>{row.party || '-'}</td><td>{row.gstin || '-'}</td><td>{row.voucher_type || '-'}</td><td><StatusBadge value={row.status || '-'} /></td><td>{row.voucher_identifier || '-'}</td><td title={row.reason || ''}>{row.reason || '-'}</td>
    <td>{row.status === 'Validation Failed' && onReview ? <button className="icon-link" onClick={() => onReview(row)}>View Details</button> : row.tally_error && onViewError ? <button className="icon-link" onClick={() => onViewError(row)}>View Details</button> : '-'}</td>
  </tr>} />
}

function SkipReasonModal({ row, onClose }) {
  const isSkipped = row.status === 'Skipped'
  const code = row.skip_reason_code || row.warning_code || '-'
  const detail = row.skip_reason || row.reason || (isSkipped ? 'This voucher was skipped.' : 'No issues.')
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="modal">
    <div className="modal-head"><div><h2>{row.invoice_number}</h2><small>{row.party?.name || '-'} · {row.invoice_date || '-'}</small></div><button onClick={onClose}>×</button></div>
    <Alert kind={isSkipped ? 'warning' : 'success'}>{detail}</Alert>
    <section className="confirm-grid">
      <SummaryPanel title={isSkipped ? 'Skip Details' : 'Details'} items={[['Status', row.status || '-'], ['Reason', detail], ['Code', code]]} />
    </section>
    <div className="modal-actions"><button onClick={onClose}>Close</button></div>
  </div></div>
}

function ErrorDetailModal({ row, onClose }) {
  const error = row.tally_error || {}
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="modal">
    <div className="modal-head"><div><h2>{row.invoice_no}</h2><small>{row.party} · {row.date || '-'} · Tally rejection detail</small></div><button onClick={onClose}>×</button></div>
    {row.reason && <Alert kind="error">{row.reason}</Alert>}
    <section className="confirm-grid">
      <SummaryPanel title="Diagnostics" items={[['Stage', error.failed_stage || 'voucher_import'], ['Transport', error.transport || 'XML'], ['HTTP Status', error.http_status || '-'], ['Voucher ID', row.voucher_identifier || '-']]} />
    </section>
    <details><summary>View Request Payload</summary><pre>{error.request_payload || '-'}</pre></details>
    <details><summary>View Raw Tally Response</summary><pre>{error.raw_response || error.actual_tally_error || error.line_error || '-'}</pre></details>
    <div className="modal-actions"><button onClick={onClose}>Close</button></div>
  </div></div>
}

function DownloadButtons({ preview }) {
  const downloadCsv = () => {
    const columns = preview?.columns || []
    const rows = preview?.rows || []
    const escape = value => `"${show(value).replace(/"/g, '""')}"`
    const csv = [columns.map(escape).join(','), ...rows.map(row => row.map(escape).join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${preview?.file_name || 'gstr-preview'}.csv`
    link.click()
    URL.revokeObjectURL(link.href)
  }
  return <div className="download-buttons"><button className="download-excel" onClick={downloadCsv} disabled={!preview}>Excel ↓</button><button className="download-pdf" onClick={() => window.print()} disabled={!preview}>PDF ↓</button></div>
}

function DataTable({ columns, rows, render, empty }) {
  return <div className="data-table-wrap"><table className="data-table"><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows?.length ? rows.map(render) : <tr><td className="empty" colSpan={columns.length}>{empty}</td></tr>}</tbody></table></div>
}

function MetaStrip({ items }) {
  return <div className="meta-strip">{items.map(([label, value]) => <span key={label}><small>{label}</small><strong>{show(value)}</strong></span>)}</div>
}

function IdentityItem({ label, value, status }) {
  return <div className={`identity-item ${status || ''}`}><small>{label}</small><strong>{show(value)}</strong></div>
}

function SummaryPanel({ title, items }) {
  return <section className="summary-panel"><h2>{title}</h2>{items.map(([label, value]) => <div key={label}><span>{label}</span><strong>{show(value)}</strong></div>)}</section>
}

function Alert({ kind = 'warning', children }) {
  return <div className={`alert ${kind}`}>{children}</div>
}
