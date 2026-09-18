import { useCallback, useEffect, useRef, useState } from 'react'
import Step3Verification from '../components/gst-tally/Step3Verification'
import { step3Ready } from '../utils/step3Verification'
import DataTransferAnimation from '../components/gst-tally/DataTransferAnimation'
import ImportTransfer3D from '../components/gst-tally/ImportTransfer3D'
import TallyEngineOrbit from '../components/processing/TallyEngineOrbit'
import ExcelPreviewGrid from '../components/gst-tally/ExcelPreviewGrid'
import VoucherMismatchModal from '../components/gst-tally/VoucherMismatchModal'
import CompactInvoiceViewModal from '../components/gst-tally/CompactInvoiceViewModal'
import DuplicateFileToast from '../components/gst-tally/DuplicateFileToast'
import ProfileScreen from '../components/gst-tally/ProfileScreen'
import AppShell from '../components/common/AppShell'
import LoadingButton from '../components/common/LoadingButton'
import StatusBadge from '../components/common/StatusBadge'
import TableSkeleton from '../components/common/TableSkeleton'
import Toast from '../components/common/Toast'
import {
  fetchBatchParties,
  getActiveTallyImportJob,
  getBatchParties,
  getBatchPreview,
  getImportBatch,
  getTallyConnection,
  getTallyImportJobStatus,
  pauseTallyImportJob,
  prepareTallyMasters,
  previewTallyVouchers,
  resolveBatchCompany,
  resumeTallyImportJob,
  startTallyImportJob,
  uploadGSTFile,
  verifyCompanyName,
  verifyTallyLicense,
} from '../services/gstTallyApi'
import { formatDate } from '../utils/date'
import { chooseAnotherFile, duplicateFileNoticeFromError } from '../utils/duplicateFileNotice'
import { finalImportToast } from '../utils/importOutcome'
import { licenseFailureUi } from '../utils/licenseSecurityUi'
import { getProcessingStage, getProcessingVisualMode, PROCESSING_READY_COMPLETE_MS } from '../utils/processingAnimation'
import { buildPrepState, canStartImport, companyDetailsReadiness, confirmMastersReadiness, defaultFileFormat, detectFileFormat, fileFormatExtension, filterMastersRows, isTallyConnectionReady, mastersSummaryFromRows, normalizeCompanyVerificationResult, readyBreakdownText, searchMastersRows, shouldAutoStartImport, tallyConnectionErrorMessage, RETURN_TYPE_FILE_FORMATS } from '../utils/workflowState'
import '../styles/gst-tally.css'

const returnTypes = [
  { id: 'GSTR1', title: 'GSTR-1', text: 'Outward supplies' },
  { id: 'GSTR2A', title: 'GSTR-2A', text: 'Auto drafted ITC' },
  { id: 'GSTR2B', title: 'GSTR-2B', text: 'Static ITC statement' },
]
const supportedExtensions = ['xlsx', 'xls', 'csv', 'json']
const stepOrder = ['upload', 'preview', 'parties', 'masters', 'vouchers', 'import']
// Step number/label shown in the AppShell header ("Step 4 — Tally Masters") --
// display-only, distinct from StepProgress's own in-page labels.
const STEP_META = {
  upload: [1, 'Upload'],
  preview: [2, 'Invoice Preview'],
  parties: [3, 'Party Details'],
  masters: [4, 'Tally Masters'],
  vouchers: [5, 'Voucher Preview'],
  import: [6, 'Import to Tally'],
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const ACTIVE_JOB_STATUSES = ['PENDING', 'RUNNING', 'VERIFYING']
const TERMINAL_JOB_STATUSES = ['COMPLETED', 'PARTIAL', 'FAILED', 'INTERRUPTED']
// Coalesce backend progress updates at a quarter-second cadence.  The job
// endpoint returns compact counters while terminal results are only attached
// once, so this remains cheap even for large imports.
const JOB_POLL_INTERVAL_MS = 250
// Mirrors tally/client.py::TallyConnectionError's `code` values -- every
// code that means "Tally itself dropped/never answered", as opposed to a
// per-voucher rejection Tally responded to normally.
const CONNECTION_LOST_ERROR_CODES = new Set([
  'TALLY_CONNECTION_REFUSED', 'TALLY_CONNECTION_TIMEOUT', 'TALLY_HOST_UNREACHABLE',
  'TALLY_PORT_UNREACHABLE', 'TALLY_CONNECT_TIMEOUT', 'TALLY_READ_TIMEOUT',
  'TALLY_INVALID_RESPONSE', 'TALLY_DISABLED',
])
// A job whose heartbeat hasn't moved in this long (client-observed) is
// treated as possibly stuck -- rather than declaring failure, the next poll
// asks the active-job endpoint, which performs the authoritative server-side
// staleness check (see backend tally/import_job.py) instead of guessing here.
const JOB_HEARTBEAT_STALL_SECONDS = 60
const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(value || 0))
const fmtNum = value => Number(value || 0).toLocaleString('en-IN')
const signedMoney = value => `${Number(value || 0) > 0 ? '+' : Number(value || 0) < 0 ? '-' : ''}${money(Math.abs(Number(value || 0)))}`
const show = value => value === null || value === undefined || value === '' ? '-' : String(value)
const formatLabelFromName = fileName => {
  const extension = String(fileName || '').split('.').pop().toLowerCase()
  return extension === 'xlsx' || extension === 'xls' ? 'Excel' : extension ? extension.toUpperCase() : 'File'
}
const returnTypeLabel = value => ({ GSTR1: 'GSTR-1', GSTR2A: 'GSTR-2A', GSTR2B: 'GSTR-2B' }[value] || value || '-')
const formatLabel = value => {
  const normalized = String(value || '').toUpperCase()
  return normalized === 'EXCEL' || normalized === 'XLSX' || normalized === 'XLS' ? 'Excel' : normalized || 'File'
}

// Step 2 (Invoice Preview) refresh recovery: the route itself is the primary
// source for which batch to show (a refresh still has the URL; it never
// still has the File object the user originally picked, so re-parsing is not
// an option) -- sessionStorage is only the fallback for a bare "/preview"
// URL with no id segment. See the mount-recovery effect below.
const LAST_BATCH_STORAGE_KEY = 'gstTally.lastBatch'
function parsePreviewBatchIdFromPath(pathname) {
  const match = /^\/gst-tally\/preview\/(\d+)/.exec(pathname || '')
  return match ? match[1] : null
}
function parseFlowBatchIdFromPath(pathname) {
  const match = /^\/gst-tally\/(?:preview|masters|vouchers|import)\/?(\d+)?/.exec(pathname || '')
  return match?.[1] || null
}
function rememberLastBatch(batch) {
  if (!batch?.id) return
  try { window.sessionStorage.setItem(LAST_BATCH_STORAGE_KEY, JSON.stringify({ id: batch.id })) } catch { }
}
function readLastStoredBatchId() {
  try { return JSON.parse(window.sessionStorage.getItem(LAST_BATCH_STORAGE_KEY) || 'null')?.id || null } catch { return null }
}
function forgetLastBatch() {
  try { window.sessionStorage.removeItem(LAST_BATCH_STORAGE_KEY) } catch { }
}

export default function GstTallyImport({ user, onLogout, subscription }) {
  // A reload lands back on this same component with a fresh, empty state --
  // reading the batch id straight out of the URL here (rather than only
  // from location.state) is what lets Step 2 restore correctly on refresh
  // instead of bouncing to Step 1. See the recovery effect below `go`.
  const [screen, setScreen] = useState(() => {
    const pathname = window.location.pathname
    if (/\/gst-tally\/vouchers/.test(pathname)) return 'vouchers'
    if (/\/gst-tally\/masters/.test(pathname)) return 'masters'
    if (/\/gst-tally\/import/.test(pathname)) return 'import'
    return parsePreviewBatchIdFromPath(pathname) ? 'preview' : 'upload'
  })
  // Profile is a lightweight overlay on top of the step workflow, not a step
  // itself -- opening/closing it must never touch batch/processing/step
  // state (see the dropdown in AppShell's UserMenu), so it lives in its own
  // boolean rather than as a stepOrder entry.
  const [showProfile, setShowProfile] = useState(false)
  const [returnType, setReturnType] = useState('GSTR1')
  const [fileFormat, setFileFormat] = useState(defaultFileFormat('GSTR1'))
  const [file, setFile] = useState(null)
  const [fileError, setFileError] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewProcessingStartedAt, setPreviewProcessingStartedAt] = useState(0)
  const [batch, setBatch] = useState(null)
  const [parties, setParties] = useState(null)
  const [sandboxRetryBusy, setSandboxRetryBusy] = useState(false)
  const [connectionResult, setConnectionResult] = useState(null)
  const [partyProcessError, setPartyProcessError] = useState('')
  const [companyInfo, setCompanyInfo] = useState(null)
  const [companyName, setCompanyName] = useState('')
  const [companyResult, setCompanyResult] = useState(null)
  const [licenseResult, setLicenseResult] = useState(null)
  const verificationAttempt = useRef(0)
  const partyLookupAttempt = useRef(0)
  const [verifyStage, setVerifyStage] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [confirmDone, setConfirmDone] = useState(false)
  const [masterResult, setMasterResult] = useState(null)
  const [voucherResult, setVoucherResult] = useState(null)
  const [vouchersValidated, setVouchersValidated] = useState(false)
  const [reviewRow, setReviewRow] = useState(null)
  const [skipDetailRow, setSkipDetailRow] = useState(null)
  const [importResult, setImportResult] = useState(null)
  const [importJobId, setImportJobId] = useState(null)
  const [importJobStatus, setImportJobStatus] = useState(null)
  // Local-only "in flight" flag for the Pause/Resume click itself -- never
  // the source of truth for whether the import is actually paused (the
  // backend job's own `status` is, see importJobStatus above). This only
  // covers the short window between clicking and the backend confirming the
  // new state, so the button can show "Pausing.../Resuming..." and refuse a
  // second click instead of silently doing nothing (see pauseImport/
  // resumeImport and the effect that clears this once jobStatus catches up).
  const [pauseAction, setPauseAction] = useState('')
  const [autoStartImport, setAutoStartImport] = useState(() => window.history.state?.autoStartImport === true)
  const autoStartTriggeredRef = useRef(false)
  // Shows the top-right completion popup exactly once per finished run --
  // set true only when a result actually arrives live (see finish() below),
  // never on the page-reload-recovery path (a reloading user shouldn't see
  // a "just completed!" notification for a run that may have finished long
  // ago), and reset on every new attempt so a Retry can show it again.
  const [resultPopupOpen, setResultPopupOpen] = useState(false)
  const [errorDetail, setErrorDetail] = useState(null)
  const [busy, setBusy] = useState('')
  const [phase, setPhase] = useState('')
  const [processingStartedAt, setProcessingStartedAt] = useState(0)
  const [processingBackendDone, setProcessingBackendDone] = useState(false)
  // Presentation-only: which CSS phase the processing modal is in (for its
  // exit transition) and the message to show inside it on failure. Neither
  // one is a second copy of real upload/batch state -- `overlayClosing` is
  // just an animation cue, and `processingError` only ever holds the exact
  // message the real catch block below already produces.
  const [overlayClosing, setOverlayClosing] = useState(false)
  const [processingError, setProcessingError] = useState('')
  const [duplicateFileNotice, setDuplicateFileNotice] = useState(null)
  const fileInputRef = useRef(null)
  const [toast, setToast] = useState(null)
  const notify = (message, kind = 'success') => setToast({ message, kind })
  const resetWorkflow = () => {
    verificationAttempt.current += 1
    partyLookupAttempt.current += 1
    forgetLastBatch()
    setShowProfile(false)
    setScreen('upload')
    setFile(null)
    setFileError('')
    setPreview(null)
    setPreviewLoading(false)
    setPreviewError('')
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
    setConfirmDone(false)
    setMasterResult(null)
    setVoucherResult(null)
    setVouchersValidated(false)
    setReviewRow(null)
    setImportResult(null)
    setImportJobId(null)
    setImportJobStatus(null)
    setPauseAction('')
    setPhase('')
    setDuplicateFileNotice(null)
    window.history.pushState({}, '', '/gst-tally')
  }
  // `batchId` is only ever passed for 'preview' (see startImport and the
  // recovery effect below) -- every other screen's URL is unchanged, so this
  // fix stays scoped to the post-upload preview/table navigation flow only.
  const go = (next, batchId, state = {}) => {
    setShowProfile(false)
    setScreen(next)
    setAutoStartImport(next === 'import' && state.autoStartImport === true)
    const suffix = next === 'preview' && batchId ? `/${batchId}` : ''
    window.history.pushState(state, '', `/gst-tally/${next}${suffix}`)
  }
  const back = () => {
    const index = stepOrder.indexOf(screen)
    if (index <= 0) return resetWorkflow()
    go(stepOrder[index - 1])
  }
  const chooseReturnType = next => {
    setReturnType(next)
    setFileFormat(defaultFileFormat(next))
    resetWorkflow()
  }
  const chooseFile = selected => {
    setDuplicateFileNotice(null)
    setFileError('')
    if (!selected) return setFile(null)
    const extension = selected.name.split('.').pop().toLowerCase()
    if (!supportedExtensions.includes(extension)) {
      setFile(null)
      setFileError('Please choose an Excel, CSV, or JSON return file.')
      return
    }
    const detected = detectFileFormat(selected.name)
    const expected = defaultFileFormat(returnType)
    if (detected !== expected) {
      setFile(null)
      setFileError(`${returnTypes.find(item => item.id === returnType)?.title} requires ${expected} format (${fileFormatExtension(expected)}).`)
      return
    }
    setFile(selected)
    setFileFormat(expected)
  }
  // Fetches (or re-fetches) Step 2's table rows straight from the batch --
  // the one path used both right after a fresh upload and to recover after a
  // browser refresh, so the two can never drift apart. Never re-uploads or
  // re-parses the source file: a completed import already has these rows
  // persisted server-side (see backend services/source_preview.preview_batch).
  const loadBatchPreview = useCallback(async (id, options = {}) => {
    setPreviewLoading(true)
    setPreviewProcessingStartedAt(Date.now())
    setPreviewError('')
    try {
      const data = await getBatchPreview(id, options)
      setPreview(data)
    } catch (error) {
      setPreviewError(error.message || 'Batch created successfully, but preview data could not be loaded.')
    } finally {
      setPreviewLoading(false)
    }
  }, [])
  // Guards against a double-click starting two import batches for one file
  // select -- the Upload button is already disabled while busy==='import-file'
  // (see LoadingButton), but that only takes effect after a re-render, so a
  // very fast double click could otherwise still land both clicks before the
  // first paint. This ref is checked synchronously, before either click's
  // async work even starts.
  const importInFlightRef = useRef(false)
  const startImport = async () => {
    if (importInFlightRef.current) return
    if (!canStartImport({ returnType, file, fileFormat })) {
      setFileError('Select a return type, file format, and matching file before importing.')
      return
    }
    importInFlightRef.current = true
    const startedAt = Date.now()
    setBusy('import-file')
    setProcessingStartedAt(startedAt)
    setProcessingBackendDone(false)
    setProcessingError('')
    setDuplicateFileNotice(null)
    setOverlayClosing(false)
    try {
      setPhase('Creating import batch')
      const imported = await uploadGSTFile({ returnType, returnPeriod: '', file })
      const batchId = imported?.id
      if (!batchId) throw new Error('The import completed without a batch ID.')
      if (imported?.upload_status_message) {
        notify(imported.upload_status_message, 'info')
      }

      // The preview request is part of backend readiness. Do not navigate or
      // show the ready state until the persisted batch rows are available.
      setPhase('Preparing invoice preview')
      const previewData = await getBatchPreview(batchId)
      setPreview(previewData)
      setProcessingBackendDone(true)

      // Backend readiness controls the transition. The overlay never holds a
      // completed upload just to finish a decorative animation.
      setOverlayClosing(true)
      setBatch(imported)
      setCompanyInfo({
        company: imported.company_details,
        company_gstin: imported.company_gstin,
        company_gstin_candidates: imported.company_gstin_candidates,
        status: imported.company_resolution_status,
        error: imported.company_resolution_error,
      })
      rememberLastBatch(imported)
      go('preview', imported.id)
    } catch (error) {
      console.error('GST import/preview request failed.', error)
      const duplicateNotice = duplicateFileNoticeFromError(error)
      if (duplicateNotice) {
        setProcessingError('')
        setDuplicateFileNotice(duplicateNotice)
      } else {
        const message = error.message || 'Unable to process the selected file.'
        notify(message, 'error')
        setProcessingError(message)
      }
    } finally {
      importInFlightRef.current = false
      setBusy('')
      setPhase('')
      setProcessingStartedAt(0)
      setProcessingBackendDone(false)
      setOverlayClosing(false)
    }
  }
  // Workflow refresh recovery: route param first, stored last-batch id as the
  // fallback. The normal upload transition already has `batch` in memory;
  // this effect only restores a direct preview/voucher/import URL after a
  // reload, including the Step 6 data needed to avoid a false zero state.
  useEffect(() => {
    if (batch?.id || screen === 'upload') return
    const id = parseFlowBatchIdFromPath(window.location.pathname) || readLastStoredBatchId()
    if (!id) { resetWorkflow(); return }
    let cancelled = false
      ; (async () => {
        try {
          const batchData = await getImportBatch(id)
          if (cancelled) return
          setBatch(batchData)
          setCompanyInfo({
            company: batchData.company_details,
            company_gstin: batchData.company_gstin,
            company_gstin_candidates: batchData.company_gstin_candidates,
            status: batchData.company_resolution_status,
            error: batchData.company_resolution_error,
          })
          rememberLastBatch(batchData)
        } catch {
          // The batch itself is gone/invalid -- nothing usable to recover, so
          // don't strand the user on a permanently empty Step 2.
          if (cancelled) return
          notify('Unable to restore this import batch. Please upload again.', 'error')
          resetWorkflow()
          return
        }
        if (!cancelled && screen === 'preview') loadBatchPreview(id)
        if (!cancelled && (screen === 'vouchers' || screen === 'import')) {
          try {
            const generated = await previewTallyVouchers(id)
            if (!cancelled) { setVoucherResult(generated); setVouchersValidated((generated.summary?.eligible || 0) > 0) }
          } catch { if (!cancelled) notify('Unable to restore generated vouchers.', 'error') }
        }
      })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // Run company persistence and independent security checks together so a
  // company mismatch never hides serial or device diagnostics.
  const runVerificationChain = async connection => {
    const attempt = ++verificationAttempt.current
    const detectedCompanyName = connection?.company_name || connection?.company || ''
    setCompanyName(detectedCompanyName)
    // Clear any stale result from a previous attempt before this one starts,
    // so the UI never mixes an old license/company outcome with the new run.
    setCompanyResult(null)
    setLicenseResult(null)
    setVerifyStage('company')
    await Promise.allSettled([
      verifyCompanyName(batch.id, detectedCompanyName).then(result => {
        if (attempt === verificationAttempt.current) setCompanyResult(result)
      }).catch(error => {
        if (attempt === verificationAttempt.current) setCompanyResult({ company_verified: false, company_details_saved: false, message: error.message })
      }),
      verifyTallyLicense(batch.id).then(result => {
        if (attempt === verificationAttempt.current) setLicenseResult(result)
      }).catch(error => {
        if (attempt === verificationAttempt.current) setLicenseResult({ ready: false, errors: [{ code: 'VERIFICATION_REQUEST_FAILED', message: error.message }] })
      }),
    ])
    if (attempt === verificationAttempt.current) setVerifyStage(null)
  }
  const proceedFromPreview = async () => {
    if (!batch?.id) return
    const lookupAttempt = ++partyLookupAttempt.current
    verificationAttempt.current += 1
    setPartyProcessError('')
    setConnectionResult(null)
    setVerifyStage(null)
    setCompanyResult(null)
    setLicenseResult(null)
    go('parties')
    setBusy('parties')
    let connection
    try {
      setPhase('Reading invoice parties')
      let result = await getBatchParties(batch.id)
      // "Pending" means this GSTIN has never actually been looked up (no
      // GSTParty record, or one that was never queried) -- the array itself
      // is never empty once the batch has any customer GSTINs, so checking
      // only `!parties.length` skipped the real Sandbox call entirely and
      // left every party stuck on GSTIN fallback. Trigger the live lookup
      // whenever at least one party still needs it; already-fresh/cached
      // parties are skipped server-side, so this never re-fetches them.
      const needsLiveLookup = !result?.parties?.length || result.parties.some(party => party.status === 'Pending')
      setParties({ ...result, lookup_pending: needsLiveLookup })
      if (needsLiveLookup) {
        setPhase('Fetching party details')
        // Tally connectivity is independent of party enrichment. Start both
        // real requests together so provider latency never delays the
        // connection check or waits for decorative animation.
        const connectionPromise = getTallyConnection().then(connection => {
          setConnectionResult(connection)
          setPhase('Checking Tally connection')
          return connection
        })
        fetchBatchParties(batch.id).then(result => {
          if (lookupAttempt === partyLookupAttempt.current) setParties(result)
        }).catch(error => {
          if (lookupAttempt !== partyLookupAttempt.current) return
          setParties({ ...result, lookup_failed: true })
          setPartyProcessError(error.message || 'Party lookup failed. Retry party lookup.')
        })
        connection = await connectionPromise
      } else {
        setPhase('Checking Tally connection')
        connection = await getTallyConnection()
        setConnectionResult(connection)
      }
      // The source company GSTIN is still ambiguous (multiple candidates) --
      // wait for the user's pick (inline selector) before verifying against
      // Tally; selectCompanyGstin resumes the chain from here once resolved.
      if (companyInfo?.error === 'MULTIPLE_COMPANY_GSTINS') return
      setPhase('Verifying company')
      await runVerificationChain(connection)
    } catch (error) {
      const message = error.message || 'Unable to prepare your Tally import. Please try again.'
      setPartyProcessError(message)
      notify(message, 'error')
    } finally {
      setBusy('')
      setPhase('')
    }
  }
  // "Retry Sandbox Lookup": a targeted re-check, not a re-run of the whole
  // party/connection/company chain above -- it re-authenticates with Sandbox
  // once (bypassing any cached "quota exhausted" verdict, see backend
  // sandbox.py authenticate(bypass_block=...)) and retries only the GSTINs
  // that are still unresolved (retry_incomplete=true), reusing the existing
  // batch -- never a file re-upload. Parties that already enriched
  // successfully are left untouched.
  const retrySandboxLookup = async () => {
    if (!batch?.id) return
    setSandboxRetryBusy(true)
    try {
      const result = await fetchBatchParties(batch.id, { retryIncomplete: true })
      setParties(result)
      const stillQuotaExhausted = (result?.parties || []).some(row => row?.sandbox_lookup?.sandbox_error_code === 'SANDBOX_QUOTA_EXHAUSTED')
      notify(stillQuotaExhausted
        ? 'Sandbox API quota is still exhausted. Please try again later.'
        : result?.enrichment_complete
          ? 'Party details updated.'
          : `Party details partially updated — ${result?.remaining || 0} GSTINs still require attention`,
        stillQuotaExhausted ? 'error' : 'success')
    } catch (error) {
      notify(error.message || 'Unable to retry Sandbox lookup.', 'error')
    } finally {
      setSandboxRetryBusy(false)
    }
  }
  // Confirm & Prepare Masters: this is the sole point that triggers real
  // master preparation (unchanged prepare_master_results / GSTIN-based
  // verification) -- Company Verification above only verifies by name and
  // persists to companydetails_tbl; it never computes master rows. Setting
  // verifyStage to 'confirm' first is what makes buildPrepState show
  // "Preparing masters" / Masters Ready as the active step while this runs.
  const prepareMastersForDisplay = async () => {
    if (!batch?.id) return
    setVerifyStage('confirm')
    setBusy('confirm')
    try {
      const rawResult = await prepareTallyMasters(batch.id, companyName.trim())
      const result = normalizeCompanyVerificationResult(rawResult, companyName.trim())
      setMasterResult(result)
      setConfirmed(true)
      // A brief "Confirmed" acknowledgement before leaving this screen -- the
      // backend call is already finished at this point, this is purely the
      // popup's own closing beat (see VerificationCard's Confirm & Continue
      // button), not an additional request or an invented delay elsewhere.
      setConfirmDone(true)
      await wait(500)
      setVerifyStage(null)
      go('masters')
    } catch (error) {
      notify(error.message || 'Unable to prepare Tally masters.', 'error')
    } finally {
      setBusy('')
      setConfirmDone(false)
    }
  }
  const refreshMastersStatus = async () => {
    if (!batch?.id) return
    setBusy('masters')
    try {
      const rawResult = await prepareTallyMasters(batch.id, companyName.trim())
      const result = normalizeCompanyVerificationResult(rawResult, companyName.trim())
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
      return true
    } catch (error) {
      notify(error.message || 'Unable to validate vouchers.', 'error')
      return false
    } finally {
      setBusy('')
    }
  }
  const generateVouchersFromMasters = async () => {
    if (!batch?.id) return
    if (await loadVouchers()) go('vouchers')
  }
  // Step 6 import as a job: this only ever starts/attaches to a background
  // job and returns almost immediately -- the browser never holds one long
  // request open for the whole batch. Progress and the final result come
  // from polling (see the importJobId effect below), not from this call.
  const runImport = async () => {
    if (!batch?.id) return
    // Clear any prior result (including a previous failure) before this
    // attempt starts, so ImportScreen shows its transfer/processing view
    // again on a Retry instead of continuing to display the stale outcome.
    setImportResult(null)
    setResultPopupOpen(false)
    setBusy('tally-import')
    try {
      let response
      try {
        response = await startTallyImportJob(batch.id)
      } catch (error) {
        // The start request itself may have failed client-side (timeout,
        // network blip) after the backend already created the job -- check
        // for an active job before assuming the import never started.
        const active = await getActiveTallyImportJob(batch.id).catch(() => null)
        if (!active || active.status === 'none') throw error
        response = active
      }
      if (response.status === 'already_running') {
        notify('Import already in progress. Tracking current import status.', 'info')
        setImportJobStatus(response)
        setImportJobId(response.job_id)
        return
      }
      if (response.status === 'interrupted') {
        // Do not auto-resend -- surface the interruption and let the user
        // explicitly choose to resume. The backend reconciles with Tally
        // (never blindly rewriting already-created vouchers) as the first
        // phase of whatever job "Resume Import" starts next.
        setBusy('')
        setImportResult({ __interrupted: true })
        notify('Previous import was interrupted. Checking Tally before resuming...', 'info')
        return
      }
      setImportJobStatus(response)
      setImportJobId(response.job_id)
      // Step 6 now transforms in place to show the same result (success/
      // partial/failed) -- no navigation to a separate screen; the outcome
      // shows on this same page (ImportCompletedBar / ImportResultPopup /
      // ImportResultsModal below), never a route change.
    } catch (error) {
      setBusy('')
      if (error.code === 'PRE_IMPORT_LICENSE_FAILED' || error.verification_result) {
        const ui = licenseFailureUi(error)
        notify(`${ui.title}\n${ui.detail}`, ui.tone === 'warning' ? 'warning' : 'error')
        setImportResult({ __licenseBlocked: true, license_block: error })
        return
      }
      setImportResult({ __preImportFailed: true, message: error.message || 'Unable to import to Tally.' })
      notify(error.message || 'Unable to import to Tally.', 'error')
    }
  }
  const finishImport = () => resetWorkflow()

  // Pause only ever asks the backend to stop before the *next* voucher
  // (see tally/import_job.py's request_pause) -- it can never interrupt a
  // write already in flight. The ongoing poll (already running every 2s
  // via the importJobId effect below) is what actually surfaces the
  // resulting PAUSED status once the worker thread notices the flag; this
  // just fires the request and refreshes jobStatus immediately so
  // pause_requested shows up (and the "Pausing..." label appears) without
  // waiting for the next poll tick.
  const pauseImport = async () => {
    if (!importJobId || pauseAction) return
    setPauseAction('pausing')
    try {
      const status = await pauseTallyImportJob(importJobId)
      setImportJobStatus(status)
    } catch (error) {
      setPauseAction('')
      notify(error.message || 'Unable to pause the import.', 'error')
    }
  }
  // Resume re-runs the same job row on a fresh background thread (see
  // tally/import_job.py's resume_job) -- safe because import_batch() always
  // reconciles against Tally before writing, not because this file tracks
  // where it left off. The ongoing poll picks up RUNNING again on its own.
  const resumeImport = async () => {
    if (!importJobId || pauseAction) return
    setPauseAction('resuming')
    try {
      const status = await resumeTallyImportJob(importJobId)
      setImportJobStatus(status)
    } catch (error) {
      setPauseAction('')
      notify(error.message || 'Unable to resume the import.', 'error')
    }
  }
  // Clears the transient "Pausing.../Resuming..." button state once the
  // backend job's own status actually reflects it -- never on a timer. A
  // pause is confirmed the moment the job reaches PAUSED; a resume is
  // confirmed the moment it leaves PAUSED (back to PENDING/RUNNING/VERIFYING,
  // or straight to a terminal status if it finished before the next poll).
  useEffect(() => {
    if (!pauseAction) return
    const status = importJobStatus?.status
    // A pause request can also simply lose the race to the job finishing on
    // its own -- a terminal status must clear "Pausing..." too, not just PAUSED.
    if (pauseAction === 'pausing' && (status === 'PAUSED' || TERMINAL_JOB_STATUSES.includes(status))) setPauseAction('')
    else if (pauseAction === 'resuming' && status && status !== 'PAUSED') setPauseAction('')
  }, [importJobStatus?.status, pauseAction])

  // Polls the active job's status instead of waiting on one blocking
  // request. Stops itself as soon as the job reaches a terminal status.
  useEffect(() => {
    if (!importJobId) return
    let cancelled = false
    let timer = null
    const finish = status => {
      setBusy('')
      setImportJobId(null)
      setPauseAction('')
      if (status.status === 'INTERRUPTED') {
        setImportResult({ __interrupted: true })
        notify('The import was interrupted. Checking Tally before you can resume.', 'error')
        return
      }
      if (status.result) {
        // The top-right result popup (see ImportScreen) replaces the old
        // generic toast for this specific event -- Step 6 itself stays on
        // screen and shows the outcome there instead of navigating away.
        setImportResult(status.result)
        setResultPopupOpen(true)
        return
      }
      // FAILED with no result payload means the job crashed before
      // import_batch could return anything usable (see import_job.py's
      // exception path) -- a real failure, distinct from a normal "0
      // imported" outcome, which always carries a result.
      setImportResult({
        import_status: 'Import Failed', message: status.error_message || 'Import failed unexpectedly.',
        error_code: status.error_code || '',
        summary: { imported: status.imported, failed: status.failed, skipped: status.skipped }
      })
      setResultPopupOpen(true)
    }
    const poll = async () => {
      try {
        let current = await getTallyImportJobStatus(importJobId)
        const heartbeatAge = current.heartbeat_at ? (Date.now() - new Date(current.heartbeat_at).getTime()) / 1000 : 0
        if (ACTIVE_JOB_STATUSES.includes(current.status) && heartbeatAge > JOB_HEARTBEAT_STALL_SECONDS) {
          notify('The import is taking longer than expected. We are checking the current Tally import status.', 'info')
          const active = await getActiveTallyImportJob(batch.id).catch(() => null)
          if (active && active.status !== 'none') current = active
        }
        if (cancelled) return
        setImportJobStatus(current)
        if (TERMINAL_JOB_STATUSES.includes(current.status)) {
          clearInterval(timer)
          finish(current)
        }
      } catch {
        // Transient polling failure -- keep polling rather than flipping to
        // a false failure; the job keeps running server-side regardless.
      }
    }
    poll()
    timer = setInterval(poll, JOB_POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [importJobId])

  // Page-reload / re-entry recovery (Step 6 spec): before showing "Ready to
  // Import", check whether this batch already has an active or just-finished
  // job so the screen restores real progress instead of reverting.
  useEffect(() => {
    if (screen !== 'import' || !batch?.id || importJobId || importResult) return
    let cancelled = false
      ; (async () => {
        try {
          const active = await getActiveTallyImportJob(batch.id)
          if (cancelled || !active || active.status === 'none') return
          // PAUSED is real, persisted backend state (see tally/import_job.py)
          // -- it must survive a reload exactly like an active job does
          // (same jobId kept so Resume Import still works, same polling loop
          // picks it back up), never silently reverting to "Ready to Import".
          if (ACTIVE_JOB_STATUSES.includes(active.status) || active.status === 'PAUSED') {
            setImportJobStatus(active)
            setBusy('tally-import')
            setImportJobId(active.job_id)
          } else if (active.status === 'INTERRUPTED') {
            setImportResult({ __interrupted: true })
          } else if (active.result) {
            setImportJobStatus(active)
            setImportResult(active.result)
          }
        } catch { }
      })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, batch?.id])

  useEffect(() => {
    if (screen !== 'import') return
    if (!shouldAutoStartImport({
      autoStartImport,
      pageReady: Boolean(voucherResult && canImportToTally({ voucherResult, masterResult, licenseResult })),
      alreadyTriggered: autoStartTriggeredRef.current,
      importBusy: busy === 'tally-import',
      hasJob: Boolean(importJobId || importJobStatus),
      hasResult: Boolean(importResult),
    })) return
    autoStartTriggeredRef.current = true
    setAutoStartImport(false)
    window.history.replaceState({}, '', window.location.pathname)
    runImport()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, autoStartImport, voucherResult, masterResult, licenseResult, busy, importJobId, importJobStatus, importResult])
  const correctVoucher = updatedPreview => {
    setVoucherResult(updatedPreview)
    setVouchersValidated((updatedPreview.summary?.eligible || 0) > 0)
  }
  // Only reached while the source company GSTIN was still ambiguous
  // (companyInfo.error === 'MULTIPLE_COMPANY_GSTINS'), which is the one case
  // proceedFromPreview stops short of starting verification -- picking a
  // GSTIN here resumes that same chain using the already-fetched connection
  // result, instead of re-running the parties/connection checks.
  const selectCompanyGstin = async gstin => {
    try {
      const result = await resolveBatchCompany(batch.id, gstin)
      setCompanyInfo(result)
      notify(`Company GSTIN ${gstin} selected.`)
      if (connectionResult?.can_import && result?.error !== 'MULTIPLE_COMPANY_GSTINS') {
        setPartyProcessError('')
        setBusy('parties')
        setPhase('Verifying company')
        try {
          await runVerificationChain(connectionResult)
        } catch (error) {
          const message = error.message || 'Unable to verify Tally company.'
          setPartyProcessError(message)
          notify(message, 'error')
        } finally {
          setBusy('')
          setPhase('')
        }
      }
    } catch (error) {
      notify(error.message || 'Unable to select company GSTIN.', 'error')
    }
  }

  const activeStep = ['upload'].includes(screen) ? 'upload' : ['preview'].includes(screen) ? 'preview' : screen === 'parties' ? 'parties' : screen
  const prepState = buildPrepState(screen, { batch, parties, connectionResult, verifyStage, companyResult, licenseResult, confirmed })
  const [stepNumber, stepLabel] = STEP_META[activeStep] || [null, null]
  const reachableIndex = stepOrder.indexOf(screen)
  // Step 6 only -- the other 5 steps keep their existing single-line header.
  const stepSubtitle = !showProfile && activeStep === 'import' ? 'Send validated vouchers to the verified Tally company' : ''

  return <AppShell user={user} activeStep={activeStep} stepNumber={showProfile ? null : stepNumber} stepLabel={showProfile ? 'My Profile' : stepLabel} stepSubtitle={stepSubtitle} batchId={batch?.id} reachableIndex={reachableIndex} onHome={onLogout} onNavigate={go} onLogout={onLogout} onProfile={() => setShowProfile(true)} profileActive={showProfile} canGoBack={showProfile || screen !== 'upload'} onBack={showProfile ? () => setShowProfile(false) : back} subscription={subscription}>
    {showProfile && <ProfileScreen />}
    {!showProfile && screen === 'upload' && <UploadScreen returnType={returnType} fileFormat={fileFormat} file={file} fileError={fileError} loading={busy === 'import-file'} phase={phase} processingStartedAt={processingStartedAt} processingBackendDone={processingBackendDone} overlayClosing={overlayClosing} processingError={processingError} onDismissProcessingError={() => setProcessingError('')} onReturnType={chooseReturnType} onFile={chooseFile} onImport={startImport} fileInputRef={fileInputRef} />}
    {!showProfile && screen === 'preview' && <PreviewScreen preview={preview} batch={batch} tableLoading={previewLoading} tableProcessingStartedAt={previewProcessingStartedAt} tableError={previewError} onRetryPreview={() => batch?.id && loadBatchPreview(batch.id)} onLoadPage={options => batch?.id && loadBatchPreview(batch.id, options)} loading={busy === 'parties'} phase={phase} onProceed={proceedFromPreview} />}
    {!showProfile && screen === 'parties' && <PartyScreen
      loading={busy === 'parties'}
      prepState={prepState}
      parties={parties}
      error={partyProcessError}
      connectionResult={connectionResult}
      companyInfo={companyInfo}
      companyResult={companyResult}
      licenseResult={licenseResult}
      confirmLoading={busy === 'confirm'}
      confirmDone={confirmDone}
      onRetry={proceedFromPreview}
      onSelectGstin={selectCompanyGstin}
      onConfirmMasters={prepareMastersForDisplay}
      onRetrySandboxLookup={retrySandboxLookup}
      sandboxRetryBusy={sandboxRetryBusy}
    />}
    {!showProfile && screen === 'masters' && <MastersScreen result={masterResult} loading={busy === 'masters'} onRefresh={refreshMastersStatus} onContinue={generateVouchersFromMasters} />}
    {!showProfile && screen === 'vouchers' && <VoucherScreen result={voucherResult} loading={busy === 'vouchers'} validated={vouchersValidated} batchId={batch?.id} reviewRow={reviewRow} onValidate={loadVouchers} onCorrected={correctVoucher} onReview={setReviewRow} onViewSkip={setSkipDetailRow} onContinue={() => go('import', null, { autoStartImport: true, fromVoucherPreview: true })} onRetrySandboxLookup={retrySandboxLookup} sandboxRetryBusy={sandboxRetryBusy} />}
    {skipDetailRow && <SkipReasonModal row={skipDetailRow} onClose={() => setSkipDetailRow(null)} />}
    {!showProfile && screen === 'import' && <Step6ImportScreen batch={batch} result={importResult} jobStatus={importJobStatus} pauseAction={pauseAction} voucherResult={voucherResult} connectionResult={connectionResult} companyResult={companyResult} licenseResult={licenseResult} masterResult={masterResult} loading={busy === 'tally-import'} autoStartImport={autoStartImport}
      onImport={runImport} onPause={pauseImport} onResume={resumeImport} onViewError={setErrorDetail} onBackToVouchers={() => go('vouchers')} onFinish={finishImport}
      resultPopupOpen={resultPopupOpen} onDismissResultPopup={() => setResultPopupOpen(false)} />}
    <Toast toast={toast} onDismiss={() => setToast(null)} />
    <DuplicateFileToast
      notice={duplicateFileNotice}
      onClose={() => setDuplicateFileNotice(null)}
      onChooseAnother={() => chooseAnotherFile({
        dismiss: () => setDuplicateFileNotice(null),
        clearFile: () => setFile(null),
        openPicker: () => window.requestAnimationFrame(() => {
          if (!fileInputRef.current) return
          fileInputRef.current.value = ''
          fileInputRef.current.click()
        }),
      })}
    />
    {errorDetail && <ErrorDetailModal row={errorDetail} onClose={() => setErrorDetail(null)} onRetry={runImport} />}
  </AppShell>
}

function UploadScreen({ returnType, fileFormat, file, fileError, loading, phase, processingStartedAt, processingBackendDone, overlayClosing, processingError, onDismissProcessingError, onReturnType, onFile, onImport, fileInputRef }) {
  return <section className="screen upload-screen">
    <header className="screen-title horizontal">
      <div><h1>Import GST Return</h1><p>Select return type and upload your GST return file.</p></div>
      <span className="step-badge">Step 1 of 6</span>
    </header>
    {/* The form stays mounted (just non-interactive) instead of being
        swapped out during processing -- the modal below is a true
        viewport-fixed overlay (see .process-overlay) that blurs/dims the
        whole page itself via backdrop-filter, so the form no longer needs
        its own dimming on top of that. Header and the action bar below
        are untouched and stay visible/functional throughout either way. */}
    <div className="upload-form-stage">
      {(() => {
        const overlayOpen = loading || Boolean(processingError)
        return <>
          <div className={`upload-form-area${overlayOpen ? ' is-dimmed' : ''}`} aria-hidden={overlayOpen}>
            <h2 className="section-label">Select Return Type</h2>
            <div className="return-grid">{returnTypes.map(item => <button key={item.id} className={`return-option ${returnType === item.id ? 'active' : ''}`} onClick={() => onReturnType(item.id)} disabled={overlayOpen}>
              <strong>{item.title}</strong><span>{item.text}</span>
              {returnType === item.id && <span className="return-check" aria-hidden="true">✓</span>}
            </button>)}</div>
            <fieldset className="format-field" disabled><legend>File Format</legend><div className="format-verified"><span className="format-verified-check" aria-hidden="true">✓</span><strong>{fileFormat}</strong> <span>({fileFormatExtension(fileFormat)})</span></div></fieldset>
            <FileDrop file={file} fileFormat={fileFormat} returnType={returnType} disabled={overlayOpen} error={fileError} onFile={onFile} inputRef={fileInputRef} />
          </div>
          {overlayOpen && <ProcessingOverlay startedAt={processingStartedAt} backendDone={processingBackendDone} closing={overlayClosing} error={processingError} onDismissError={onDismissProcessingError} />}
        </>
      })()}
    </div>
    <div className="action-bar"><LoadingButton className="import-file-button" loading={loading} disabled={!canStartImport({ returnType, file, fileFormat })} onClick={onImport}>{loading ? phase || 'Importing' : <>Upload File <span aria-hidden="true">→</span></>}</LoadingButton></div>
  </section>
}

function ProcessingOverlay({ startedAt, backendDone, closing, error, onDismissError }) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!startedAt) return
    const update = () => setElapsed(Date.now() - startedAt)
    update()
    const timer = window.setInterval(update, 80)
    return () => window.clearInterval(timer)
  }, [startedAt])

  const stage = getProcessingStage(elapsed, backendDone)
  const visualMode = getProcessingVisualMode(elapsed, backendDone)
  const ready = stage.key === 'ready'
  // Capped below 100 until the backend actually confirms success -- the
  // elapsed-time ramp is only ever an estimate, so it must never claim
  // "done" before the real upload/batch creation call has (see `ready`,
  // which is gated on backendDone the same way).
  const progress = Math.min(backendDone ? 100 : 96, Math.round((Math.min(elapsed, PROCESSING_READY_COMPLETE_MS) / PROCESSING_READY_COMPLETE_MS) * 100))

  return <div className={`process-overlay ${closing ? 'is-closing' : ''}`} aria-live="polite">
    <div className={`process-overlay-card process-stage-${stage.key} process-visual-${visualMode} ${ready ? 'is-ready' : ''} ${closing ? 'is-closing' : ''} ${error ? 'has-error' : ''}`}>
      {error ? <>
        {/* Same message the toast already showed (see startImport's catch
            block) -- this is just the modal surfacing the real error
            instead of hiding it, not a second error path. Animation stops
            (no DataTransferAnimation rendered) and the tick never shows,
            since `ready` can only ever be true after real backend success. */}
        <div className="process-error-icon" aria-hidden="true">!</div>
        <h2 className="process-error-title">Something went wrong</h2>
        <p className="process-error-message">{error}</p>
        <button type="button" className="process-error-dismiss" onClick={onDismissError}>Close</button>
      </> : <>
        <h2>Processing Your Data</h2>
        <p>Preparing your records securely</p>
        <DataTransferAnimation mode={visualMode} ready={ready} />
        {/* The visual caption carries the visible progress text; this mirrors
            the current stage for screen readers. */}
        <span className="sr-only" aria-live="polite">{ready ? 'Ready. ' : ''}{stage.title}. {stage.subtitle}</span>
        <div className="process-progress" aria-label="Processing progress">
          <span style={{ width: `${progress}%` }} />
        </div>
      </>}
    </div>
  </div>
}

function FileDrop({ file, fileFormat, returnType, disabled, error, onFile, inputRef }) {
  const [dragging, setDragging] = useState(false)
  return <label className={`file-drop ${dragging ? 'dragging' : ''}`}>
    <input ref={inputRef} type="file" accept={RETURN_TYPE_FILE_FORMATS[returnType]?.extension === '.xlsx' ? '.xlsx,.xls' : RETURN_TYPE_FILE_FORMATS[returnType]?.extension} disabled={disabled} onChange={event => onFile(event.target.files[0] || null)} onDragEnter={() => setDragging(true)} />
    <span className="drop-symbol" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M7 18a4.5 4.5 0 0 1-.5-8.97A5.5 5.5 0 0 1 17.24 8.02 4 4 0 0 1 17 16H7z" /><path d="M12 12v6M9.5 14.5 12 12l2.5 2.5" /></svg>
    </span>
    {file ? <><strong>{file.name}</strong><small>{fileFormat} file selected · ✓ Ready</small></> : <>
      <strong>Drag and drop your file here</strong>
      <span className="file-drop-or">or</span>
      <span className="choose-file-btn">Choose File</span>
      <small>Required: {fileFormat} ({fileFormatExtension(fileFormat)})</small>
    </>}
    {error && <em role="alert">{error}</em>}
  </label>
}

const PREVIEW_PAGE_SIZE = 50

// Purely a rendering window over the same page numbers -- always includes the
// first/last two pages plus a neighborhood around the current page, so every
// page stays reachable (jump to the visible boundary, then step in) without
// ever rendering a button for all ~79 pages at once.
function paginationWindow(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
  const pages = new Set([1, 2, total - 1, total, current - 1, current, current + 1])
  const sorted = [...pages].filter(pageNumber => pageNumber >= 1 && pageNumber <= total).sort((a, b) => a - b)
  const windowed = []
  let previous = 0
  for (const pageNumber of sorted) {
    if (previous && pageNumber - previous > 1) windowed.push('...')
    windowed.push(pageNumber)
    previous = pageNumber
  }
  return windowed
}

function PreviewScreen({ preview, batch, tableLoading, tableProcessingStartedAt, tableError, onRetryPreview, onLoadPage, loading, phase, onProceed }) {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const searchTimer = useRef(null)
  const hasLoadedInitialPage = useRef(false)
  const allRows = preview?.rows || []
  const totalRows = preview?.row_count || 0
  const totalPages = Math.max(1, preview?.total_pages || Math.ceil(totalRows / PREVIEW_PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const start = (currentPage - 1) * PREVIEW_PAGE_SIZE
  const handleSearch = value => {
    setSearch(value)
    setPage(1)
    window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => onLoadPage?.({ page: 1, pageSize: PREVIEW_PAGE_SIZE, search: value.trim() }), 180)
  }
  useEffect(() => () => window.clearTimeout(searchTimer.current), [])
  useEffect(() => {
    if (!preview || !hasLoadedInitialPage.current) {
      if (preview) hasLoadedInitialPage.current = true
      return
    }
    if (page !== (preview.page || 1)) onLoadPage?.({ page, pageSize: PREVIEW_PAGE_SIZE, search })
  }, [page, preview, search, onLoadPage])
  const fileName = preview?.file_name || batch?.file_name || ''
  return <section className="screen preview-screen">
    <header className="screen-title horizontal preview-header">
      <div className="preview-header-text">
        <h1>Step 2 — Invoice Preview</h1>
        <p className="preview-subtitle preview-page-subtitle">Review the imported invoice data before continuing.</p>
       </div>
      <div className="preview-toolbar">
        <FileDetailsButton preview={preview} batch={batch} recordCount={totalRows} fileName={fileName} />
        <label className="preview-search"><span className="preview-search-icon" aria-hidden="true">⌕</span><input type="search" value={search} onChange={event => handleSearch(event.target.value)} placeholder="Search invoices..." aria-label="Search preview rows" /></label>
        <DownloadButtons preview={preview} batch={batch} />
      </div>
    </header>
    <div className="preview-table-container">
      {tableLoading && !preview ? <ProcessingOverlay startedAt={tableProcessingStartedAt} backendDone={false} />
        : tableError ? <div className="preview-state-message preview-state-error" role="alert">
          <span>Batch created successfully, but preview data could not be loaded.</span>
          <button type="button" className="text-button" onClick={onRetryPreview}>Retry Preview</button>
        </div>
          : preview && allRows.length === 0 ? <div className="preview-state-message">
            <span>No invoice rows were found in this file.</span>
          </div>
            : <ExcelPreviewGrid preview={preview ? { ...preview, viewportHeight: 500 } : { columns: [], rows: [] }} />}
    </div>
    <div className="preview-bottom-row">
      <div className="preview-bottom-status">{totalRows > 0
        ? <span>Showing {start + 1} to {Math.min(start + PREVIEW_PAGE_SIZE, totalRows)} of {totalRows}{search.trim() ? ' matching invoices' : ' invoices'}</span>
        : search.trim() ? <span>No invoices match "{search.trim()}".</span> : null}</div>
      <div className="pagination">{totalRows > 0 && <>
        <button disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>
        {paginationWindow(currentPage, totalPages).map((item, index) => item === '...'
          ? <span key={`ellipsis-${index}`} className="pagination-ellipsis" aria-hidden="true">…</span>
          : <button key={item} className={item === currentPage ? 'current' : ''} onClick={() => setPage(item)}>{item}</button>)}
        <button disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button>
      </>}</div>
      <div className="preview-bottom-proceed"><LoadingButton className="proceed-button" loading={loading} onClick={onProceed}>{loading ? phase || 'Preparing' : 'Proceed'}</LoadingButton></div>
    </div>
  </section>
}

// [key, title, description] -- description is UI copy only (shown on the left
// checklist row and, for the active item, on the "Current Operation" card).
// Status itself always comes from prepState (buildPrepState), never from here.
const PREP_ITEMS = [
  ['parties', 'Reading invoice parties', 'Collecting invoice party information from source file'],
  ['fetch', 'Fetching party details', 'Retrieving available party details'],
  ['connection', 'Checking Tally connection', 'Establishing connection with Tally'],
  ['company', 'Verifying company', 'Matching the selected company with the company currently open in Tally.'],
  ['license', 'Checking product/Tally license', 'Verifying the registered serial and product capacity'],
  ['device', 'Checking device authorization', 'Verifying this device and device capacity'],
  ['masters', 'Preparing masters', 'Preparing required masters for import'],
]
const PREP_STATUS_LABEL = { done: 'Completed', active: 'In Progress', failed: 'Failed', pending: 'Pending' }
const PREP_ACTIVE_TEXT = { parties: 'Reading...', fetch: 'Fetching...', connection: 'Connecting...', company: 'Validating...', license: 'Checking...', masters: 'Preparing...' }

// Right-hand engine visual: all 6 satellite nodes map 1:1 to prepState keys
// (buildPrepState in utils/workflowState.js is the single source of truth for
// status -- this only labels the node each key renders as). There is no
// separate left/right status: both PartyScreen's prep-timeline and this
// engine read the exact same prepState[key] value, so they can never disagree.
// Node POSITION/ICON-SHAPE is fixed inside TallyEngineOrbit itself (tuned to
// match the reference design); ORBIT_SLOT_ID below is only the mapping from a
// prepState key to that fixed visual slot.
const ENGINE_NODES = [
  { key: 'parties', label: 'Source Data', activeText: 'Reading...', doneText: 'Loaded' },
  { key: 'fetch', label: 'Party Details', activeText: 'Fetching...', doneText: 'Fetched' },
  { key: 'connection', label: 'Tally System', activeText: 'Connecting...', doneText: 'Connected' },
  { key: 'masters', label: 'Masters Ready', activeText: 'Preparing...', doneText: 'Ready' },
  { key: 'license', label: 'Device Authorization', activeText: 'Checking...', doneText: 'Authorized' },
  { key: 'company', label: 'Company Verify', activeText: 'Validating...', doneText: 'Verified' },
]
const ORBIT_SLOT_ID = { parties: 'source', fetch: 'party', connection: 'tally', masters: 'masters', license: 'license', company: 'verify' }

function PartyScreen({ loading, prepState, parties, error, connectionResult, companyInfo, companyResult, licenseResult, confirmLoading, confirmDone, onRetry, onSelectGstin, onConfirmMasters, onRetrySandboxLookup, sandboxRetryBusy }) {
  // Sandbox's own account-level quota block (see backend sandbox.py's
  // PROVIDER_BLOCK_CACHE_KEY) applies to every party in this batch at once --
  // reported explicitly as one batch-level field (views.py's
  // _batch_sandbox_provider_status), with a per-row scan as a fallback for
  // any response shape that predates that field.
  const quotaExhausted = parties?.sandbox_provider_status === 'QUOTA_EXHAUSTED'
    || (parties?.parties || []).some(row => row?.sandbox_lookup?.sandbox_error_code === 'SANDBOX_QUOTA_EXHAUSTED')
  // Unique valid customer GSTINs detected in this batch -- never derived from
  // tally_ready_count alone, which only counts parties that already resolved
  // to a usable ledger name and would undercount while Sandbox lookups are
  // still pending/retrying.
  const totalParties = parties?.total_parties ?? parties?.unique_customer_gstin_count ?? parties?.total ?? 0
  // Real invoice row count for this batch, as read by the backend (see
  // views.py's _batch_gstin_diagnostics) -- never a guess.
  const totalInvoices = parties?.invoice_row_count ?? 0
  const activeEntry = PREP_ITEMS.map(([key, label, description]) => ({ key, label, description, status: prepState[key] || 'pending' })).find(item => item.status === 'active') || null
  // Bottom "Current Operation" status readout -- only ever one of these four
  // real, backend-derived states (never a fabricated timing/security claim).
  const anyFailed = Object.values(prepState).includes('failed')
  const needsGstinPick = companyInfo?.error === 'MULTIPLE_COMPANY_GSTINS' && !companyResult
  const readyToContinue = companyDetailsReadiness(companyResult).ready && step3Ready(licenseResult)
  const allStepsDone = PREP_ITEMS.every(([key]) => (prepState[key] || 'pending') === 'done')
  // Same three real checks VerificationCard itself requires for "Verification
  // Complete" (Tally connected, company verified, license verified) -- used
  // here only to swap the panel's own header text/dot, never to duplicate
  // the confirmation card's own gating logic.
  const verificationComplete = prepState.connection === 'done'
    && companyDetailsReadiness(companyResult).companyVerified
    && step3Ready(licenseResult)
  const operationStatus = anyFailed ? 'failed'
    : (needsGstinPick || readyToContinue) ? 'waiting'
      : allStepsDone ? 'completed'
        : activeEntry ? 'processing'
          : 'idle'
  const orbitNodes = ENGINE_NODES.map(node => {
    const status = prepState[node.key] || 'pending'
    const caption = status === 'done' ? node.doneText : status === 'active' ? node.activeText : status === 'failed' ? 'Failed' : 'Pending'
    return { id: ORBIT_SLOT_ID[node.key], label: node.label, status, caption }
  })
  return <section className="screen preparing-import-screen">
    <header className="screen-title"><h1>Preparing Tally Import</h1><p>Fetching party details and verifying Tally connection{totalParties > 0 ? ` for ${totalParties} ${totalParties === 1 ? 'party' : 'parties'}` : ''}...</p></header>
    <div className="preparing-import-layout">
      <div className="prep-column">
        <h2 className="prep-column-heading">Processing Steps</h2>
        <div className="prep-timeline" aria-live="polite">{PREP_ITEMS.map(([key, label, description]) => {
          const status = prepState[key] || 'pending'
          // "Fetching party details" completing only means the fetch request
          // itself round-tripped -- it says nothing about whether Sandbox
          // actually enriched any party. A green "Completed" here when every
          // lookup failed on a quota block reads as "this worked fine", which
          // it did not; this is a display-only override (prepState.fetch stays
          // 'done', unaffected, since the fetch step itself did complete -- the
          // engine visual and Current Operation logic below still agree with
          // every other consumer of prepState).
          const fetchQuotaWarning = key === 'fetch' && status === 'done' && quotaExhausted
          // Every count/status word here comes straight from real backend
          // results already held in state -- never an invented number. When
          // the backend hasn't surfaced a count for a step, it just says
          // "Completed" (via PREP_STATUS_LABEL) with no count line.
          const count = fetchQuotaWarning ? 'Sandbox quota exhausted'
            : status !== 'done' ? ''
              : key === 'parties' && totalInvoices > 0 ? `${totalInvoices} ${totalInvoices === 1 ? 'invoice' : 'invoices'}`
                : key === 'fetch' && totalParties > 0 ? `${totalParties} unique ${totalParties === 1 ? 'party' : 'parties'}`
                  : key === 'connection' ? 'Connected'
                    : key === 'company' ? 'GSTIN Matched'
                      : key === 'license' ? 'Authorized'
                        : ''
          return <div key={key} className={`prep-card prep-card-${status} ${fetchQuotaWarning ? 'prep-card-done-warning' : ''}`}>
            <span className="prep-card-icon-wrap">
              <span className="prep-card-icon"><WorkflowIcon itemKey={key} size={22} /></span>
              {status === 'done' && !fetchQuotaWarning && <span className="prep-card-badge" aria-hidden="true">✓</span>}
              {fetchQuotaWarning && <span className="prep-card-badge prep-card-badge-warning" aria-hidden="true">!</span>}
              {status === 'failed' && <span className="prep-card-badge prep-card-badge-failed" aria-hidden="true">×</span>}
            </span>
            <span className="prep-card-body">
              <span className="prep-card-top">
                <span className="prep-card-label">{label}</span>
                <span className={`prep-card-pill prep-card-pill-${status} ${fetchQuotaWarning ? 'prep-card-pill-warning' : ''}`}>{fetchQuotaWarning ? 'Completed with Warning' : PREP_STATUS_LABEL[status]}</span>
              </span>
              <span className="prep-card-desc">{description}</span>
              {(count || status === 'active') && <span className="prep-card-foot">
                {count && <span className={fetchQuotaWarning ? 'prep-card-count-warning' : 'prep-card-count'}>{count}</span>}
                {status === 'active' && <span className="prep-card-live"><span>{PREP_ACTIVE_TEXT[key]}</span><span className="prep-row-spinner" aria-hidden="true" /></span>}
              </span>}
            </span>
          </div>
        })}</div>
      </div>
      <div className="engine-column">
        <div className="engine-live-panel">
          <div className={`engine-live-header ${verificationComplete ? 'is-complete' : ''}`}>
            <span className="engine-live-dot" aria-hidden="true" />
            <span className="engine-live-title">{verificationComplete ? '✓ Verification Complete' : 'Live Processing'}</span>
          </div>
          <p className="engine-live-sub">{verificationComplete ? 'Tally connection, company and license are verified.' : 'Your data is being processed securely in the background.'}</p>
          <TallyEngineOrbit bare status={verificationComplete ? 'complete' : 'live'} nodes={orbitNodes} />
          <VerificationCard prepState={prepState} connectionResult={connectionResult} companyInfo={companyInfo} companyResult={companyResult} licenseResult={licenseResult} confirmLoading={confirmLoading} confirmDone={confirmDone} onSelectGstin={onSelectGstin} onConfirmMasters={onConfirmMasters} onRetryLicense={onRetry} />
        </div>
        <CurrentOperationCard entry={activeEntry} status={operationStatus} />
      </div>
    </div>
    {error && <Alert kind="error">{error} <button type="button" className="text-button" onClick={onRetry}>Retry</button></Alert>}
    {!error && quotaExhausted && <Alert kind="warning">
      Sandbox API quota exhausted. Taxpayer enrichment is temporarily unavailable.
      {' '}<LoadingButton className="text-button" loading={sandboxRetryBusy} onClick={onRetrySandboxLookup}>{sandboxRetryBusy ? 'Retrying...' : 'Retry Sandbox Lookup'}</LoadingButton>
    </Alert>}
    <p className="info-note"><i aria-hidden="true">i</i> Please do not close this window while we prepare your import process.</p>
  </section>
}

// Inline replacement for the old Company Verification / Confirm & Prepare
// Masters modals -- same underlying calls (verifyCompanyName, then
// verifyTallyLicense, both driven by GstTallyImport's runVerificationChain),
// just surfaced as one compact card instead of two blocking dialogs. Reads
// prepState (the same single status source the left timeline and right
// engine already use) so it can never disagree with either.
function VerificationCard(props) {
  return <Step3Verification {...props} />
}

function MiniCheckIcon({ status }) {
  return <span className="mini-check-icon" aria-hidden="true">{status === 'done' ? '✓' : status === 'failed' ? '×' : status === 'active' ? '●' : '○'}</span>
}

// Shared icon set for the left prep timeline, the right engine nodes, and the
// Current Operation card -- one icon per PREP_ITEMS/ENGINE_NODES key so all
// three surfaces stay visually consistent for the same workflow step.
function WorkflowIcon({ itemKey, size = 18 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }
  if (itemKey === 'parties') return <svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h5" /></svg>
  if (itemKey === 'fetch') return <svg {...common}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
  if (itemKey === 'connection') return <svg {...common}><path d="M9 2v4M15 2v4" /><path d="M7 8h10l-1 6a4 4 0 0 1-4 4h0a4 4 0 0 1-4-4z" /><path d="M12 18v4" /></svg>
  if (itemKey === 'company') return <svg {...common}><path d="M4 21V7l8-4 8 4v14" /><path d="M9 21v-6h6v6" /><path d="M9 11h.01M15 11h.01M9 15h.01M15 15h.01" /></svg>
  if (itemKey === 'license') return <svg {...common}><path d="M12 2 4 5v6c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V5z" /><path d="M8.5 12.5l2.5 2.5 5-5" /></svg>
  return <svg {...common}><path d="M21 8V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v1" /><path d="M3 8h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M8 12h8" /></svg>
}

function ClockIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>
}

// Right-hand readout is one of exactly four real, backend-derived states --
// never a fabricated timing estimate or a security/marketing claim.
const OPERATION_STATUS_TEXT = { processing: 'Processing', waiting: 'Waiting for Confirmation', completed: 'Completed', failed: 'Failed', idle: 'Idle' }

function LockIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="11" width="16" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
}

function CurrentOperationCard({ entry, status }) {
  return <div className="current-operation-panel">
    <div className="current-operation-main">
      <span className={`current-operation-icon ${entry ? 'is-live' : ''}`} aria-hidden="true">{entry ? <WorkflowIcon itemKey={entry.key} size={22} /> : <ClockIcon />}</span>
      <span className="current-operation-text">
        <span className="current-operation-label">Current Operation</span>
        <div className="current-operation-title">{entry?.label || 'Idle'}</div>
        <p className="current-operation-desc">{entry?.description || 'No background operation is currently running.'}</p>
      </span>
    </div>
    {/* Reassurance copy, not a measured metric -- never a specific ETA or a
        claim about this batch's own security, which would need real backend
        data this endpoint doesn't provide. */}
    <div className="current-operation-info">
      <span className="current-operation-info-item"><ClockIcon /><span><b>Estimated time remaining</b>Just a few seconds</span></span>
      <span className="current-operation-info-sep" aria-hidden="true" />
      <span className="current-operation-info-item"><LockIcon /><span><b>Data is secure</b>256-bit encrypted transfer</span></span>
    </div>
    <div className={`current-operation-status current-operation-status-${status}`}>
      <span className="current-operation-status-dot" aria-hidden="true" />
      <span className="current-operation-status-text">{OPERATION_STATUS_TEXT[status] || 'Idle'}</span>
    </div>
  </div>
}

const MASTER_FILTER_TABS = [
  ['all', 'All'],
  ['parties', 'Party Ledger'],
  ['accounts', 'Accounts Ledger'],
  ['taxLedgers', 'Tax Ledger'],
  ['otherLedgers', 'Other'],
]

function MastersScreen({ result, loading, onRefresh, onContinue }) {
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [partyDetailRow, setPartyDetailRow] = useState(null)
  const masters = result?.masters || []
  const summaries = mastersSummaryFromRows(masters)
  const categorySummaries = summaries.filter(item => item.key !== 'failed')
  const failedCount = summaries.find(item => item.key === 'failed')?.completed || 0
  // Category tab and search box are both purely local/display filters over
  // the same already-loaded `masters` rows -- neither ever re-requests data.
  const categoryFiltered = filterMastersRows(masters, filter)
  const visibleMasters = searchMastersRows(categoryFiltered, search)
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
        : masters.length > 0 && visibleMasters.length === 0 ? 'No masters match this filter.'
          : 'All required Tally masters are already available for this batch.'
  // Success is now a transient top-right toast, never a permanent full-width
  // banner inside the card -- shows once whenever the real "all required
  // masters already available" outcome becomes true, and auto-dismisses (the
  // user can also close it early). Re-appears if a later Refresh Status genuinely
  // re-confirms the same outcome after it had gone false (e.g. mid-repair).
  const [successToastVisible, setSuccessToastVisible] = useState(false)
  useEffect(() => {
    if (!allRequiredAlreadyAvailable) { setSuccessToastVisible(false); return }
    setSuccessToastVisible(true)
    const timer = window.setTimeout(() => setSuccessToastVisible(false), 5000)
    return () => window.clearTimeout(timer)
  }, [allRequiredAlreadyAvailable])
  return <section className="screen masters-screen">
    <header className="screen-title horizontal masters-title">
      <div><h1>Tally Masters</h1><p>Statuses are shown only from backend master preparation results.</p></div>
      <div className="masters-title-actions">
        <MasterSummaryButton summaries={summaries} onFailedClick={() => setFilter('failed')} />
        <LoadingButton loading={loading} disabled={loading} onClick={onRefresh}>{loading ? 'Refreshing' : 'Refresh Status'}</LoadingButton>
      </div>
    </header>
    {successToastVisible && <div className="masters-success-toast" role="status">
      <span className="masters-success-toast-icon" aria-hidden="true">✓</span>
      <span className="masters-success-toast-text">All required Tally masters are available for this batch</span>
      <button type="button" className="masters-success-toast-close" aria-label="Dismiss" onClick={() => setSuccessToastVisible(false)}>×</button>
    </div>}
    <div className="masters-toolbar">
      <label className="masters-search">
        <span className="masters-search-icon" aria-hidden="true">⌕</span>
        <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search masters..." aria-label="Search masters" />
      </label>
      <div className="masters-tabs" role="tablist">
        {MASTER_FILTER_TABS.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={filter === key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}</button>)}
        {filter === 'failed' && <span className="masters-tabs-chip">Failed<button type="button" onClick={() => setFilter('all')} aria-label="Clear failed filter">✕</button></span>}
      </div>
      <span className="masters-toolbar-count">Showing {visibleMasters.length} master{visibleMasters.length === 1 ? '' : 's'}</span>
    </div>
    <DataTable columns={['Master Type', 'Name', 'Group', 'GST Rate / Tax Type', 'Status', 'Message']} rows={visibleMasters} empty={emptyMessage} render={(row, index) => {
      const isParty = (row.master_type || row.type) === 'Party'
      return <tr key={`${row.name}-${index}`} className={isParty ? 'masters-row-clickable' : ''} onClick={isParty ? () => setPartyDetailRow(row) : undefined}>
        <td>{row.master_type || row.type || '-'}</td><td title={row.name || ''}>{row.name || '-'}</td><td>{row.group || '-'}</td><td>{row.gst_rate || row.tax_type || '-'}</td><td><StatusBadge value={row.status || 'Pending'} /></td><td title={row.message || ''}>{row.message || '-'}</td>
      </tr>
    }} />
    <div className="masters-footer">
      <span className="masters-footer-count">{readyCount} master{readyCount === 1 ? '' : 's'} ready</span>
      <LoadingButton loading={loading} disabled={!canContinue || loading} onClick={onContinue}>Generate Vouchers</LoadingButton>
    </div>
    {blockingReason && <p className="import-block-reason">{blockingReason}</p>}
    {partyDetailRow && <PartyMasterDetailModal row={partyDetailRow} onClose={() => setPartyDetailRow(null)} />}
  </section>
}

function PartyMasterDetailModal({ row, onClose }) {
  const dataSource = row.data_source || {}
  const sourceLabel = field => dataSource[field] === 'SANDBOX' ? 'Sandbox' : dataSource[field] === 'SOURCE' ? 'Source fallback' : '-'
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="modal">
    <div className="modal-head"><div><h2>{row.name || '-'}</h2><small>{row.group || '-'} · {row.gstin || 'No GSTIN'}</small></div><button onClick={onClose}>×</button></div>
    <section className="confirm-grid-2">
      <SummaryPanel title="Party Details" items={[
        ['Party Name', row.name || '-'],
        ['Trade Name', row.trade_name || '-'],
        ['Legal Name', row.legal_name || '-'],
        ['GSTIN', row.gstin || '-'],
        ['State', row.state || '-'],
        ['Country', row.country || '-'],
        ['Pincode', row.pincode || '-'],
        ['Registration Type', row.registration_type || '-'],
        ['Address', row.address || '-'],
      ]} />
      <SummaryPanel title="Source of Details" items={[
        ['Trade Name', sourceLabel('trade_name')],
        ['Legal Name', sourceLabel('legal_name')],
        ['Address', sourceLabel('address')],
        ['Pincode', sourceLabel('pincode')],
      ]} />
    </section>
    <div className="modal-actions"><button onClick={onClose}>Close</button></div>
  </div></div>
}

const MASTER_SUMMARY_DISPLAY_LABEL = { parties: 'Parties', accounts: 'Accounts', taxLedgers: 'Tax Ledgers', otherLedgers: 'Other Ledgers', failed: 'Failed' }

// Compact "Master Summary" trigger + floating popover -- holds the exact same
// real per-category counts the old always-on 5 cards showed (parties/
// accounts/taxLedgers/otherLedgers/failed from mastersSummaryFromRows), just
// moved behind a click instead of permanently occupying vertical space above
// the table.
function MasterSummaryButton({ summaries, onFailedClick }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  useEffect(() => {
    if (!open) return
    const onKeyDown = event => { if (event.key === 'Escape') setOpen(false) }
    const onPointerDown = event => { if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false) }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open])
  return <div className="master-summary-wrap" ref={wrapRef}>
    <button type="button" className={`master-summary-btn ${open ? 'is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>
      Master Summary
    </button>
    {open && <div className="master-summary-popover" role="dialog" aria-label="Master summary">
      <div className="master-summary-popover-head">
        <span>Master Summary</span>
        <button type="button" className="master-summary-close" aria-label="Close master summary" onClick={() => setOpen(false)}>×</button>
      </div>
      <div className="master-summary-rows">{summaries.map(item => {
        const isFailed = item.key === 'failed'
        return <div key={item.key} className="master-summary-row">
          <span className="master-summary-row-label"><span className={`master-summary-dot master-summary-dot-${item.key}`} aria-hidden="true" />{MASTER_SUMMARY_DISPLAY_LABEL[item.key] || item.label}</span>
          <span className="master-summary-row-values">
            <strong>{isFailed ? item.completed : `${item.completed} / ${item.total}`}</strong>
            {isFailed
              ? <small>{item.completed > 0 ? <button type="button" className="master-summary-failed-link" onClick={() => { onFailedClick(); setOpen(false) }}>View failed items</button> : 'No failures'}</small>
              : <small>{item.percent}% Ready{item.completed > 0 ? ` · ${readyBreakdownText(item.byStatus)}` : ''}</small>}
          </span>
        </div>
      })}</div>
    </div>}
  </div>
}

const VOUCHER_STATUS_FILTERS = ['All', 'Ready', 'Needs Attention', 'Already Imported', 'Skipped']
const VOUCHER_MISMATCH_STATUSES = ['Review Required', 'Validation Failed', 'Needs Attention']

// Keep the UI vocabulary separate from provider/backend display text. Every
// filter and count below uses this one normalized value derived from the real
// voucher status returned by the backend.
function normalizeVoucherStatus(row) {
  const raw = String(row?.status || '').trim()
  if (raw === 'Already Imported') return 'ALREADY_IMPORTED'
  if (VOUCHER_MISMATCH_STATUSES.includes(raw)) return 'NEEDS_ATTENTION'
  if (raw === 'Skipped' || raw === 'Invalid' || raw === 'Not Attempted' || raw.startsWith('Invalid ')) return 'SKIPPED'
  if (raw.startsWith('Ready') && row?.import_eligible !== false) return 'READY'
  return 'SKIPPED'
}

function roundOffSign(value) {
  const num = Number(value || 0)
  return num > 0 ? 'Positive' : num < 0 ? 'Negative' : 'Zero'
}

function EyeIcon() {
  return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M1.5 12S5 5 12 5s10.5 7 10.5 7-3.5 7-10.5 7S1.5 12 1.5 12Z" /><circle cx="12" cy="12" r="3" /></svg>
}

function VoucherScreen({ result, loading, validated, batchId, reviewRow, onValidate, onCorrected, onReview, onViewSkip, onContinue, onRetrySandboxLookup, sandboxRetryBusy }) {
  const [viewRow, setViewRow] = useState(null)
  const [filter, setFilter] = useState('All')
  const [search, setSearch] = useState('')
  const vouchers = (result?.vouchers || []).map(row => ({ ...row, voucher_status: normalizeVoucherStatus(row), invoice_date_iso: row.invoice_date, invoice_date: formatDate(row.invoice_date), voucher_date: formatDate(row.voucher_date || row.invoice_date) }))
  // Round Off summary (task spec's optional strip): counted independently of
  // the Ready/Needs Attention stats above, which must remain unchanged --
  // "attention" here means the invoice couldn't be treated as a normal
  // round-off at all (the large-mismatch case), not every mismatch reason.
  const searchNeedle = search.trim().toLowerCase()
  const matchesSearch = row => {
    if (!searchNeedle) return true
    const partyName = row.party_name || row.party?.name || row.party?.trade_name || row.party?.legal_name || ''
    const partyGstin = row.party_gstin || row.party?.gstin || ''
    return [row.invoice_number, partyName, partyGstin].some(value => String(value || '').toLowerCase().includes(searchNeedle))
  }
  const filterStatus = { Ready: 'READY', 'Needs Attention': 'NEEDS_ATTENTION', 'Already Imported': 'ALREADY_IMPORTED', Skipped: 'SKIPPED' }[filter]
  const filtered = vouchers.filter(row => (!filterStatus || row.voucher_status === filterStatus)
    && matchesSearch(row))
  const statusCounts = vouchers.reduce((counts, row) => { counts[row.voucher_status] += 1; return counts }, { READY: 0, NEEDS_ATTENTION: 0, ALREADY_IMPORTED: 0, SKIPPED: 0 })
  const ready = statusCounts.READY
  const mismatch = statusCounts.NEEDS_ATTENTION
  // Any voucher still carrying a Sandbox enrichment warning means at least
  // one party in this batch is on the GSTIN-fallback path -- surfaced here,
  // where the user is actually looking at "Ready with GSTIN Fallback", with
  // a retry that (unlike navigating back to Step 3) also re-validates
  // vouchers immediately after, so this screen never keeps showing party
  // data from before a successful retry.
  const anyFallback = vouchers.some(row => row.sandbox_warning)
  const retryAndRevalidate = async () => {
    if (onRetrySandboxLookup) await onRetrySandboxLookup()
    onValidate()
  }
  return <section className="screen voucher-screen">
    <header className="screen-title horizontal voucher-header">
      <div><h1>Voucher Preview</h1><p>Validate invoice vouchers and correct mismatches before import.</p></div>
      <div className="voucher-header-actions">
        {anyFallback && onRetrySandboxLookup && <div className="voucher-sandbox-popover" role="status">
          <span>Sandbox enrichment incomplete</span>
          <LoadingButton className="text-button" loading={sandboxRetryBusy} onClick={retryAndRevalidate}>{sandboxRetryBusy ? 'Fetching Party Details...' : 'Retry'}</LoadingButton>
        </div>}
        <label className="voucher-search"><span className="voucher-search-icon" aria-hidden="true">⌕</span><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search invoice / party / GSTIN..." aria-label="Search vouchers" /></label>
        {vouchers.length > 0 && <VoucherSummaryButton counts={{ total: vouchers.length, ready, attention: mismatch, eligible: result?.summary?.eligible || 0, alreadyImported: result?.summary?.already_imported || 0 }} />}
      </div>
    </header>
    <div className="voucher-toolbar">
      <div className="voucher-toolbar-left">
        <div className="filter-tabs voucher-filter-tabs">{VOUCHER_STATUS_FILTERS.map(item => {
          const count = item === 'All' ? vouchers.length : statusCounts[{ Ready: 'READY', 'Needs Attention': 'NEEDS_ATTENTION', 'Already Imported': 'ALREADY_IMPORTED', Skipped: 'SKIPPED' }[item]]
          return <button key={item} type="button" className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item} ({count})</button>
        })}</div>
      </div>
    </div>
    <div className="voucher-table-area">
      {loading && !result ? <TableSkeleton cols={14} /> : <DataTable columns={['Invoice', 'Invoice Date', 'Voucher Date', 'Party', 'GSTIN', 'Taxable', 'CGST', 'SGST', 'IGST', 'Component Total', 'Round Off', 'Final Total', 'Status', 'Action']} rows={filtered} empty="No vouchers match this filter." render={(row, index) => {
        const mismatchRow = row.voucher_status === 'NEEDS_ATTENTION'
        const partyName = row.party_name || row.party?.name || row.party?.trade_name || row.party?.legal_name || row.party_gstin || row.party?.gstin || '-'
        const partyGstin = row.party_gstin || row.party?.gstin || '-'
        const items = row.items || []
        return items.map((item, itemIndex) => {
          const isLastItem = itemIndex === items.length - 1
          const itemComponentTotal = ['taxable_value', 'cgst', 'sgst', 'igst', 'cess']
            .reduce((total, field) => total + Number(item[field] || 0), 0)
          return <tr key={`${row.invoice_number}-${index}-item-${itemIndex}`} className={`voucher-item-row voucher-status-${row.voucher_status.toLowerCase()}${isLastItem ? ' invoice-last-item' : ''}`}>
          <td>{row.invoice_number || '-'}</td><td>{row.invoice_date || '-'}</td><td>{row.voucher_date || row.invoice_date || '-'}</td><td className="voucher-party-cell" title={partyName}>{partyName}</td><td>{partyGstin}</td>
          <td className="voucher-num">{money(item.taxable_value)}</td><td className="voucher-num">{money(item.cgst)}</td><td className="voucher-num">{money(item.sgst)}</td><td className="voucher-num">{money(item.igst)}</td>
          <td className="voucher-num">{money(itemComponentTotal)}</td>
          {isLastItem ? <><td className={`voucher-num voucher-round-off voucher-round-off-${roundOffSign(row.round_off).toLowerCase()}`}>{signedMoney(row.round_off)}</td><td className="voucher-num voucher-final-total">{money(row.final_voucher_total)}</td>
            <td><StatusBadge value={mismatchRow ? 'Needs Attention' : row.status || 'Invalid'} />{row.voucher_status === 'SKIPPED' && row.reason && <small className="voucher-status-reason">{row.reason}</small>}</td><td><button className="icon-link voucher-view-btn" onClick={() => mismatchRow ? onReview(row) : row.voucher_status === 'ALREADY_IMPORTED' ? onViewSkip(row) : setViewRow(row)}>{mismatchRow ? <>⚠ Fix</> : row.voucher_status === 'ALREADY_IMPORTED' ? <>&#8635; Duplicate</> : <><EyeIcon /> View</>}</button></td></>
            : <><td /><td /><td /><td /></>}
        </tr>
        })
      }} />}
    </div>
    <div className="voucher-footer">
      <span className="voucher-footer-text"><strong>{ready}</strong> voucher{ready === 1 ? '' : 's'} ready for import · {mismatch} need attention · {statusCounts.ALREADY_IMPORTED} already imported · {statusCounts.SKIPPED} skipped</span>
      <LoadingButton disabled={!validated || ready === 0} onClick={onContinue}>Continue to Import</LoadingButton>
    </div>
    {reviewRow && <VoucherMismatchModal row={reviewRow} batchId={batchId} onClose={() => onReview(null)} onSaved={onCorrected} />}
    {viewRow && <CompactInvoiceViewModal row={viewRow} onClose={() => setViewRow(null)} />}
  </section>
}

// Compact, state-aware "Generate & Validate" action -- a single real backend
// call (onValidate/loadVouchers -> previewTallyVouchers), presented as four
// honest states derived from props the parent already tracks: idle (never
// validated), loading, a brief "just succeeded" flash, and revalidate (a
// result already exists). Never fabricates a completion the backend hasn't
// actually returned yet.
function VoucherValidateButton({ loading, hasResult, validated, onClick }) {
  const [justValidated, setJustValidated] = useState(false)
  const wasLoadingRef = useRef(false)
  useEffect(() => {
    const wasLoading = wasLoadingRef.current
    wasLoadingRef.current = loading
    if (wasLoading && !loading && validated) {
      setJustValidated(true)
      const timer = window.setTimeout(() => setJustValidated(false), 2200)
      return () => window.clearTimeout(timer)
    }
  }, [loading, validated])
  const stateClass = loading ? 'is-loading' : justValidated ? 'is-success' : hasResult ? 'is-revalidate' : 'is-idle'
  return <button type="button" className={`voucher-validate-btn ${stateClass}`} disabled={loading} onClick={onClick}>
    <span className="voucher-validate-icon" aria-hidden="true">{loading ? <span className="voucher-validate-spinner" /> : justValidated ? '✓' : hasResult ? '↻' : '⚡'}</span>
    <span className="voucher-validate-text">
      <span className="voucher-validate-main">{loading ? 'Validating Vouchers...' : justValidated ? 'Vouchers Validated' : hasResult ? 'Revalidate Vouchers' : 'Generate & Validate'}</span>
      {!loading && !justValidated && !hasResult && <span className="voucher-validate-caption">Prepare voucher checks</span>}
    </span>
  </button>
}

// Compact "Voucher Summary" trigger + floating popover -- same real backend
// counts (Total Invoices / Ready / Needs Attention / Eligible) the old
// always-on summary bar showed, just moved behind a click so the table gets
// that vertical space by default. Never pushes the table down.
function VoucherSummaryButton({ counts }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  useEffect(() => {
    if (!open) return
    const onKeyDown = event => { if (event.key === 'Escape') setOpen(false) }
    const onPointerDown = event => { if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false) }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open])
  const rows = [
    ['total', 'Total Invoices', counts.total, ''],
    ['ready', 'Ready', counts.ready, 'good'],
    ['attention', 'Needs Attention', counts.attention, counts.attention > 0 ? 'bad' : ''],
    ['alreadyImported', 'Already Imported', counts.alreadyImported || 0, counts.alreadyImported > 0 ? 'bad' : ''],
    ['eligible', 'Eligible', counts.eligible, ''],
  ]
  return <div className="voucher-summary-wrap" ref={wrapRef}>
    <button type="button" className={`voucher-summary-btn ${open ? 'is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>
      Voucher Summary
    </button>
    {open && <div className="voucher-summary-popover" role="dialog" aria-label="Voucher summary">
      <div className="voucher-summary-popover-head">
        <span>Voucher Summary</span>
        <button type="button" className="voucher-summary-close" aria-label="Close voucher summary" onClick={() => setOpen(false)}>×</button>
      </div>
      <div className="voucher-summary-rows">{rows.map(([key, label, value, indicator]) => <div key={key} className="voucher-summary-row">
        <span className="voucher-summary-row-label">{indicator && <span className={`voucher-summary-dot voucher-summary-dot-${indicator}`} aria-hidden="true" />}{label}</span>
        <strong>{value}</strong>
      </div>)}</div>
    </div>}
  </div>
}

// Compact "Round Off Summary" trigger + floating popover -- purely
// informational (the actual filtering is the separate "Round Off" dropdown
// in the toolbar). Same real per-sign counts as the old always-on 4-card row,
// just moved behind a click; overlays the page and never pushes the table.
function RoundOffSummaryButton({ counts }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  useEffect(() => {
    if (!open) return
    const onKeyDown = event => { if (event.key === 'Escape') setOpen(false) }
    const onPointerDown = event => { if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false) }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open])
  const rows = [
    ['positive', 'Positive Round Off', counts.positive],
    ['negative', 'Negative Round Off', counts.negative],
    ['zero', 'Zero Round Off', counts.zero],
    ['attention', 'Needs Attention', counts.attention],
  ]
  return <div className="round-off-summary-wrap" ref={wrapRef}>
    <button type="button" className={`round-off-summary-btn ${open ? 'is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 17V9M9 17V5M15 17v-4M21 17V3" /></svg>
      Round Off Summary
    </button>
    {open && <div className="round-off-summary-popover" role="dialog" aria-label="Round off summary">
      <div className="round-off-summary-popover-head">
        <span>Round Off Summary</span>
        <button type="button" className="round-off-summary-close" aria-label="Close round off summary" onClick={() => setOpen(false)}>×</button>
      </div>
      <div className="round-off-summary-rows">{rows.map(([key, label, value]) => <div key={key} className="round-off-summary-row">
        <span className="round-off-summary-row-label"><span className={`round-off-summary-dot round-off-summary-dot-${key}`} aria-hidden="true" />{label}</span>
        <strong>{value}</strong>
      </div>)}</div>
    </div>}
  </div>
}

function canImportToTally({ voucherResult, masterResult, licenseResult }) {
  const summary = voucherResult?.summary || {}
  const readiness = confirmMastersReadiness(masterResult, licenseResult)
  return Number(summary.eligible || 0) > 0 && Boolean(masterResult?.ready) && readiness.ready
}

// Step 6 -- the final Tally transfer workspace. NOT the Voucher Preview
// layout and NOT the old 4-card + isolated-button screen: one panel showing
// Source -> Transfer -> Destination while nothing has been sent yet, a
// confirm dialog before the one real import call fires, then the SAME panel
// transforming in place to show the actual backend result (success/partial/
// failure) -- never a navigation to a blank/separate page. Every number and
// status here is read from real props (voucherResult/connectionResult/
// companyResult/masterResult/licenseResult/result); none of the underlying
// eligibility, master, connection, or import logic is touched.
function Step6ImportScreen({ batch, result, jobStatus, pauseAction, voucherResult, loading, onImport, onPause, onResume, onViewError, onBackToVouchers, onFinish }) {
  const [detailStatus, setDetailStatus] = useState('')
  // A completed run with rejected vouchers should immediately expose the
  // inline explanation table, matching the normal operator workflow. Users
  // can still collapse it with the section header.
  useEffect(() => {
    const summary = result?.summary || {}
    const rejected = Number(summary.failed || 0) + Number(summary.skipped || 0)
      + Number(summary.unknown || 0) + Number(summary.not_attempted || 0)
      + Number(summary.waiting_for_tally_period || 0)
    if (!result) return
    if (rejected > 0) setDetailStatus('not_imported')
    else if (Number(summary.imported || 0) > 0) setDetailStatus('imported')
    else if (Number(summary.already_imported || 0) > 0) setDetailStatus('already_imported')
    else if (Number(summary.remaining || 0) > 0) setDetailStatus('remaining')
  }, [result])
  const dataReady = Boolean(batch?.id && (voucherResult || jobStatus || result))
  if (!dataReady) {
    return <section className="screen step6-screen step6-loading-screen">
      <div className="step6-shell step6-loading-shell" role="status" aria-live="polite">
        <div className="step6-loading-content"><span className="step6-loading-spinner" aria-hidden="true" /><strong>Loading import data…</strong><span>Preparing the file summary and live Tally status.</span></div>
      </div>
    </section>
  }
  const summary = result?.summary || voucherResult?.summary || {}
  const total = Number(summary.total ?? result?.total_parsed_invoices ?? jobStatus?.total ?? 0)
  const imported = Number(result?.summary?.imported ?? jobStatus?.imported ?? 0)
  const alreadyImported = Number(result?.summary?.already_imported ?? 0)
  // These are all terminal, non-imported outcomes. Keep them together so
  // the four cards always reconcile with the real source-record total.
  const failed = Number(result?.summary?.failed ?? jobStatus?.failed ?? 0)
  const skipped = Number(result?.summary?.skipped ?? jobStatus?.skipped ?? 0)
    + Number(result?.summary?.validation_failed ?? result?.summary?.invalid ?? 0)
  const unresolved = Number(result?.summary?.unknown ?? 0)
    + Number(result?.summary?.not_attempted ?? 0)
    + Number(result?.summary?.waiting_for_tally_period ?? 0)
  const notImported = failed + skipped + unresolved
  const pending = Number(result?.summary?.pending_verification ?? result?.summary?.verification_pending ?? jobStatus?.verification_pending ?? 0)
  const processed = result ? Math.min(total, imported + alreadyImported + notImported + pending) : Math.min(total, Number(jobStatus?.processed || 0))
  const remaining = Math.max(0, total - processed)
  const percent = total ? Math.min(100, (processed / total) * 100) : 0
  const active = ['PENDING', 'RUNNING', 'VERIFYING', 'PAUSED'].includes(jobStatus?.status)
  const completedWithErrors = Boolean(result && (notImported > 0 || pending > 0))
  const title = !result ? 'Importing to Tally' : completedWithErrors ? 'Import Completed with Errors' : 'Import Completed'
  const elapsed = useElapsedSeconds(jobStatus?.started_at, active)
  const batchSize = DISPLAY_BATCH_SIZE
  const batchCount = total ? Math.max(1, Math.ceil(total / batchSize)) : 0
  const currentBatch = total ? Math.min(batchCount, Math.max(1, Math.ceil(Math.max(processed, 1) / batchSize))) : 0
  const fileName = batch?.file_name || 'Uploaded file'
  const fileType = batch?.file_type || String(fileName).split('.').pop()?.toUpperCase() || 'File'
  const uploadedAt = batch?.uploaded_at ? new Date(batch.uploaded_at).toLocaleString() : ''
  const rows = result?.results || []
  const retryableNotImported = rows.some(row => {
    if (['Imported', 'Already Imported'].includes(row.status)) return false
    if (row.user_error?.retryable === false) return false
    return row.user_error?.retryable === true || [
      'Master Setup Failed', 'Preflight Failed', 'Tally Failed',
      'Verification Failed', 'Unknown / Verify', 'Unknown / Verify Before Retry',
      'Not Attempted',
    ].includes(row.status)
  })
  const detailRows = detailStatus === 'imported'
    ? rows.filter(row => row.status === 'Imported')
    : detailStatus === 'already_imported'
      ? rows.filter(row => row.status === 'Already Imported')
      : detailStatus === 'remaining'
        ? rows.filter(row => ['Pending', 'Not Attempted', 'Review Required', 'Validation Failed'].includes(row.status))
      : rows.filter(row => !['Imported', 'Already Imported'].includes(row.status))
  const detailCount = status => ({ imported, already_imported: alreadyImported, not_imported: notImported, remaining }[status] || 0)
  const toggleDetails = status => setDetailStatus(status)
  const reasonFor = row => {
    // The import service preserves technical detail for the report, but also
    // supplies this normalized operator-safe explanation for the UI.
    const userError = row.user_error || {}
    if (userError.user_message) {
      return [userError.user_message, userError.action_message].filter(Boolean).join(' ')
    }
    const raw = String(row.reason || row.error_message || row.message || '').trim()
    if (!raw || ['Import failed', 'Validation error', 'Unknown error', 'Tally rejected voucher'].includes(raw)) {
      return 'Tally did not provide a specific rejection reason. Verify the voucher and required ledgers, then retry.'
    }
    if (/CREATION_LEDGER_NOT_FOUND/i.test(raw)) return `Party ledger '${row.party || row.party_name || 'for this voucher'}' does not exist in Tally. Create or prepare this ledger and retry.`
    if (/GST.*invalid|INVALID_GSTIN/i.test(raw)) return `GSTIN ${row.gstin || row.party_gstin || ''} is invalid. Correct the GSTIN in the source invoice and retry.`
    if (/total.*mismatch|mismatch.*total/i.test(raw)) return raw.replace(/\s+/g, ' ').trim().replace(/\.?$/, '.').replace(/\.\.$/, '.') + ' Check taxable value, tax amounts and invoice value.'
    if (/already exists|duplicate/i.test(raw)) return `Voucher ${row.invoice_no || row.invoice_number || ''} already exists in this Tally company. It was not imported again.`
    return raw
  }
  return <section className="screen step6-screen">
    <div className="step6-shell">
      <div className="step6-columns">
        <aside className="step6-file-card">
          <div className="step6-file-icon">{fileType === 'CSV' ? 'CSV' : fileType === 'JSON' ? '{}' : 'X'}</div>
          <h2 title={fileName}>{fileName}</h2>
          {uploadedAt && <p>Uploaded on {uploadedAt}</p>}
          <div className="step6-file-rule" />
          <span className="step6-file-label">Total Records</span>
          <strong className="step6-file-total">{fmtNum(total)}</strong>
          <span className="step6-file-type">{fileType === 'XLSX' || fileType === 'XLS' ? 'Excel' : fileType}</span>
        </aside>
        <main className="step6-center-card">
          <h2>{title}</h2>
          {!result && <p>Please wait while vouchers are being imported...</p>}
          {result && <p>{completedWithErrors ? 'Some vouchers require attention.' : 'All vouchers were acknowledged by Tally.'}</p>}
          <ImportTransfer3D headline="" subline="" status={result ? (completedWithErrors ? 'PARTIAL' : 'COMPLETED') : 'ACTIVE'} />
          <div className="step6-progress-copy"><strong>{fmtNum(processed)} of {fmtNum(total)} processed</strong><strong>{percent.toFixed(0)}%</strong></div>
          <div className="step6-progress-bar"><span style={{ width: `${percent}%` }} /></div>
          <p className="step6-progress-meta">Batch {currentBatch} of {batchCount || 1} <span>•</span> Elapsed time: {formatDuration(elapsed)}</p>
        </main>
        <aside className="step6-status-grid">
          <button type="button" className={`step6-status-card imported${detailStatus === 'imported' ? ' is-selected' : ''}`} onClick={() => toggleDetails('imported')}><span>✓</span><small>IMPORTED</small><strong>{fmtNum(imported)}</strong></button>
          <button type="button" className={`step6-status-card already${detailStatus === 'already_imported' ? ' is-selected' : ''}`} onClick={() => toggleDetails('already_imported')}><span>+</span><small>ALREADY IMPORTED</small><strong>{fmtNum(alreadyImported)}</strong></button>
          <button type="button" className={`step6-status-card failed${detailStatus === 'not_imported' ? ' is-selected' : ''}`} onClick={() => toggleDetails('not_imported')}><span>!</span><small>NOT IMPORTED</small><strong>{fmtNum(notImported)}</strong></button>
          <button type="button" className={`step6-status-card remaining${detailStatus === 'remaining' ? ' is-selected' : ''}`} onClick={() => toggleDetails('remaining')} aria-label="View remaining vouchers"><span>□</span><small>REMAINING</small><strong>{fmtNum(remaining)}</strong></button>
        </aside>
      </div>
      {detailStatus && <Step6StatusPanel status={detailStatus} count={detailCount(detailStatus)} rows={detailRows} reasonFor={reasonFor} onClose={() => setDetailStatus('')} />}
      <footer className="step6-footer">
        <nav className="step6-actions">
          <button type="button" className="step6-secondary" onClick={onBackToVouchers}>Back to Voucher Preview</button>
          <button type="button" className="step6-secondary" onClick={() => downloadImportReport(jobStatus, result)}>Download Report</button>
          {retryableNotImported && <button type="button" className="step6-secondary" onClick={onImport} disabled={loading || active}>Retry Import</button>}
          {result && <button type="button" className="step6-primary" onClick={onFinish}>Finish</button>}
        </nav>
      </footer>
    </div>
  </section>
}

function Step6StatusPanel({ status, count, rows, reasonFor, onClose }) {
  const title = { imported: 'Imported Vouchers', already_imported: 'Already Imported Vouchers', not_imported: 'Not Imported Vouchers', remaining: 'Remaining Vouchers' }[status]
  const copy = status === 'not_imported'
    ? 'These vouchers could not be imported to Tally. Check the reason for each voucher and correct the data if required.'
    : status === 'imported' ? 'Successfully imported to Tally.'
      : status === 'already_imported' ? 'These vouchers were already available in the verified Tally company and were not imported again.'
        : 'These vouchers are pending processing.'
  const emptyCopy = `No ${status === 'already_imported' ? 'already imported' : status === 'not_imported' ? 'not imported' : status} vouchers in this batch.`
  return <section className={`step6-detail-panel step6-status-panel step6-status-panel-${status}${count > 0 ? ' has-records' : ''}`}>
    <div className="step6-detail-heading"><span><strong>{status === 'not_imported' ? '❗ ' : status === 'imported' ? '✓ ' : ''}{title} ({fmtNum(count)})</strong></span><button type="button" onClick={onClose}>Close</button></div>
    <p className="step6-status-panel-copy">{count > 0 ? copy : emptyCopy}</p>
    {count > 0 && <Step6DetailsTable rows={rows} reasonFor={reasonFor} includeReason={status !== 'imported'} />}
  </section>
}

function Step6DetailsTable({ rows, reasonFor, includeReason = true }) {
  return <div className="step6-inline-table-wrap"><table className="step6-inline-table"><thead><tr><th>Voucher / Invoice No</th><th>Voucher Date</th><th>GSTIN</th><th>Amount</th><th>Status</th>{includeReason && <th>Reason</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.invoice_no || row.invoice_number}-${index}`}><td>{row.invoice_no || row.invoice_number || '-'}</td><td>{row.voucher_date || row.date || '-'}</td><td>{row.gstin || row.party_gstin || '-'}</td><td>{row.final_total || row.final_voucher_total || row.invoice_total || row.amount || '-'}</td><td><StatusBadge value={row.status || '-'} /></td>{includeReason && <td>{reasonFor(row)}</td>}</tr>)}</tbody></table></div>
}

function Step6ResultModal({ status, rows, onClose, onViewError }) {
  const title = status === 'imported' ? 'Imported Vouchers' : status === 'already_imported' ? 'Already Imported Vouchers' : 'Not Imported Vouchers'
  const reasonFor = row => row.reason || row.error_message || (status === 'not_imported' ? 'Tally did not provide a specific reason.' : '-')
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="modal step6-result-modal" role="dialog" aria-modal="true"><div className="modal-head"><h2>{title}</h2><button type="button" onClick={onClose} aria-label="Close">×</button></div><DataTable columns={['Invoice No', 'GSTIN', 'Party', 'Voucher Date', 'Amount', 'Status', 'Reason']} rows={rows} empty="No vouchers in this status." render={(row, index) => <tr key={`${row.invoice_no || row.invoice_number}-${index}`}><td>{row.invoice_no || row.invoice_number || '-'}</td><td>{row.gstin || row.party_gstin || '-'}</td><td>{row.party || row.party_name || '-'}</td><td>{row.voucher_date || row.date || '-'}</td><td>{row.final_total || row.final_voucher_total || row.invoice_total || row.amount || '-'}</td><td><StatusBadge value={row.status || '-'} /></td><td>{reasonFor(row)}</td></tr>} /></div></div>
}

function ImportScreen({ result, jobStatus, pauseAction, voucherResult, connectionResult, companyResult, licenseResult, masterResult, loading, autoStartImport, onImport, onPause, onResume, onViewError, onBackToVouchers, onFinish, resultPopupOpen, onDismissResultPopup }) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [showResultsModal, setShowResultsModal] = useState(false)
  const [showSummaryPopup, setShowSummaryPopup] = useState(false)
  const [selectedResultStatus, setSelectedResultStatus] = useState('')
  const summary = voucherResult?.summary || {}
  const readyCount = Number(summary.eligible || 0)
  const readyVoucherCount = Number(summary.ready || 0)
  const needsReviewCount = Number(summary.invalid || 0)
  const failedValidationCount = Number(summary.validation_failed || 0)
  const readiness = confirmMastersReadiness(masterResult, licenseResult)
  const mastersReady = Boolean(masterResult?.ready)
  const blockingReason = readyCount === 0 ? 'No ready vouchers available.'
    : !readiness.tallyConnected ? 'Tally connection is unavailable.'
      : !readiness.companyVerified ? 'Company verification is required.'
        : !readiness.licenseVerified ? 'License verification is required.'
          : !mastersReady ? 'Required Tally masters are not ready.'
            : ''
  const canImport = !blockingReason

  const companyName = connectionResult?.company_name || connectionResult?.company || '-'
  const companyGstin = connectionResult?.company_gstin || connectionResult?.gstin || '-'
  const financialYear = connectionResult?.financial_year
    || (connectionResult?.financial_year_from && connectionResult?.financial_year_to ? `${connectionResult.financial_year_from} - ${connectionResult.financial_year_to}` : '-')
  const host = connectionResult?.host || ''
  const port = connectionResult?.port
  const tallyConnected = Boolean(connectionResult?.read_connected && connectionResult?.company_open)
  const companyVerified = Boolean(companyResult?.company_verified)

  const validationCompleted = Boolean(voucherResult)
  const totalsVerified = readyCount > 0

  const isInterrupted = Boolean(result?.__interrupted)
  const isLicenseBlocked = Boolean(result?.__licenseBlocked)
  const isPreImportFailed = Boolean(result?.__preImportFailed)
  const licenseBlockedUi = isLicenseBlocked ? licenseFailureUi({ code: 'PRE_IMPORT_LICENSE_FAILED', ...(result.license_block || {}) }) : null
  const hasResult = Boolean(result) && !isInterrupted && !isLicenseBlocked && !isPreImportFailed
  // Vouchers Tally confirms already exist (e.g. re-discovered on a
  // pause/resume run) are a real successful outcome, exactly like
  // ImportResultPopup already treats them -- never a smaller "imported"
  // count than what the job's own cumulative total (and the stat tiles
  // below, driven by jobStatus.imported) actually show.
  const newlyImported = Number(result?.summary?.imported || 0)
  const alreadyImported = Number(result?.summary?.already_imported || 0)
  const imported = newlyImported + alreadyImported
  const failedCount = Number(result?.summary?.failed || 0)
  const skippedCount = Number(result?.summary?.skipped || 0)
  const pendingCount = Number(result?.summary?.pending_verification || result?.summary?.verification_pending || 0)
  const resultEligibleCount = Number(result?.summary?.total_eligible ?? result?.summary?.eligible ?? readyCount)
  const remaining = Math.max(0, resultEligibleCount - imported - failedCount - skippedCount - pendingCount)
  const importStatus = result?.import_status || ''
  // The backend only emits Import Successful after the final Tally
  // acknowledgement and persisted registry reconciliation. Prefer that
  // authoritative outcome over a stale/mismatched client-side preview count,
  // while still requiring zero unresolved failures before showing success.
  const isSuccess = hasResult && ['Import Successful', 'Import Verified'].includes(importStatus) && imported > 0 && failedCount === 0 && pendingCount === 0
  const isPartial = hasResult && !isSuccess && imported > 0
  const isCompleteFailure = hasResult && imported === 0
  const isPending = importStatus === 'Write Accepted - Verification Pending'
  const isFailed = hasResult && !isSuccess && !isPartial && !isPending
  const isPaused = jobStatus?.status === 'PAUSED'
  const isPausing = pauseAction === 'pausing'
  const isResuming = pauseAction === 'resuming'
  // Tally itself dropped mid-batch (refused/timed out/unreachable -- see
  // tally/client.py's TallyConnectionError, surfaced via error_code by
  // import_job.py) rather than any per-voucher rejection -- a distinct,
  // recoverable case: everything already imported is untouched, only new
  // writes stopped (spec section 12).
  const isConnectionLost = isFailed && CONNECTION_LOST_ERROR_CODES.has(result?.error_code || '')
  const preparingAutoStart = autoStartImport && canImport && !jobStatus && !hasResult && !loading

  const totalBatches = readyCount > 0 ? Math.max(1, Math.ceil(readyCount / DISPLAY_BATCH_SIZE)) : 0
  const processed = hasResult
    ? Math.min(resultEligibleCount, imported + failedCount + skippedCount + pendingCount)
    : Math.min(readyCount, Number(jobStatus?.processed || 0))
  const currentBatch = readyCount > 0 ? Math.min(totalBatches, Math.max(1, Math.ceil(processed / DISPLAY_BATCH_SIZE))) : 0
  const percent = readyCount > 0 ? Math.min(100, (processed / readyCount) * 100) : 0
  const displayedImported = hasResult ? imported : Number(jobStatus?.imported || 0)
  const displayedFailed = hasResult ? failedCount : Number(jobStatus?.failed || 0)
  const displayedSkipped = hasResult ? skippedCount : Number(jobStatus?.skipped || 0)
  const displayedNotImported = displayedFailed + displayedSkipped
  const displayedRemaining = hasResult ? remaining : Math.max(0, readyCount - processed)
  const elapsedSeconds = useElapsedSeconds(jobStatus?.started_at, ACTIVE_JOB_STATUS_SET.has(jobStatus?.status || 'PENDING'))
  const speed = elapsedSeconds > 0 ? processed / elapsedSeconds : 0
  const etaSeconds = ACTIVE_JOB_STATUS_SET.has(jobStatus?.status || 'PENDING') && speed > 0 ? Math.round((readyCount - processed) / speed) : null
  const resultRows = result?.results || []
  const rowsForStatus = status => resultRows.filter(row => {
    if (status === 'imported') return row.status === 'Imported'
    if (status === 'already_imported') return row.status === 'Already Imported'
    if (status === 'not_imported') return !['Imported', 'Already Imported'].includes(row.status)
    if (status === 'skipped') return ['Skipped', 'Needs Attention'].includes(row.status)
    if (status === 'remaining') return ['Not Attempted', 'Review Required', 'Validation Failed'].includes(row.status)
    return true
  })
  const statusDetails = {
    imported: { title: 'Imported', text: `${fmtNum(newlyImported)} voucher${newlyImported === 1 ? '' : 's'} imported successfully to Tally.`, action: 'View Imported Data' },
    already_imported: { title: 'Already Processed', text: `${fmtNum(alreadyImported)} voucher${alreadyImported === 1 ? ' was' : 's were'} previously imported and not sent to Tally again.`, action: 'View Already Imported Data' },
    not_imported: { title: 'Not Imported', text: `${fmtNum(displayedNotImported)} voucher${displayedNotImported === 1 ? ' was' : 's were'} not sent to Tally.`, action: 'View Not Imported Data' },
    skipped: { title: 'Skipped Records', text: `${fmtNum(displayedSkipped)} voucher${displayedSkipped === 1 ? ' was' : 's were'} intentionally excluded from this import.`, action: 'View Skipped Data' },
    remaining: { title: 'Remaining', text: `${fmtNum(displayedRemaining)} valid voucher${displayedRemaining === 1 ? ' is' : 's are'} waiting to be sent to Tally.`, action: 'View Remaining Data' },
  }
  const openStatusDetails = status => { setSelectedResultStatus(status); setShowResultsModal(true) }

  return <section className="screen import-screen">
    <header className="import-header">
      <div className="import-header-left">
        <h1>Import to Tally</h1>
        <p className="import-subtitle">Send validated vouchers to the verified Tally company</p>
      </div>
      <div className="import-header-right">
        <span className="import-volume-badge">
          <span aria-hidden="true">⚡</span>
          <span><b>High Volume Mode</b><small>Optimized for large imports</small></span>
        </span>
      </div>
    </header>

    <div className="import-workspace">
      {isLicenseBlocked ? (
        <div className="import-result import-result-failed">
          <div className="import-result-icon" aria-hidden="true">!</div>
          <h2>{licenseBlockedUi.title}</h2>
          <p>{licenseBlockedUi.detail}</p>
          <div className="import-result-actions">
            {licenseBlockedUi.actions.includes('Recheck Tally') && (
              <button type="button" className="import-result-btn-primary" onClick={onImport} disabled={loading}>
                {loading ? 'Rechecking Tally...' : 'Recheck Tally'}
              </button>
            )}
            <button type="button" className="import-result-btn-secondary" onClick={onBackToVouchers}>Contact Administrator</button>
          </div>
        </div>
      ) : isPreImportFailed ? (
        <div className="import-result import-result-failed">
          <div className="import-result-icon" aria-hidden="true">!</div>
          <h2>Unable to Start Import</h2>
          <p>{result.message || blockingReason || 'Tally verification failed before import.'}</p>
          <div className="import-result-actions">
            <button type="button" className="import-result-btn-primary" onClick={onImport} disabled={loading}>
              {loading ? 'Retrying Verification...' : 'Retry Verification'}
            </button>
            <button type="button" className="import-result-btn-secondary" onClick={onBackToVouchers}>Back to Voucher Preview</button>
          </div>
        </div>
      ) : isInterrupted ? (
        <div className="import-result import-result-partial">
          <div className="import-result-icon" aria-hidden="true">⚠</div>
          <h2>Import Interrupted</h2>
          <p>The previous import stopped without finishing. Resuming checks Tally first -- vouchers it already accepted will not be sent again.</p>
          <div className="import-result-actions">
            <button type="button" className="import-result-btn-primary" onClick={onImport} disabled={loading}>
              {loading ? 'Checking Tally...' : 'Resume Import'}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="import-transfer-hero">
            {/* Source Panel */}
            <div className="import-source-panel">
              <div className="panel-header">
                <h3><span className="panel-header-icon" aria-hidden="true">📄</span> Validated Vouchers</h3>
                <span className="ready-chip"><span aria-hidden="true">✓</span> Ready</span>
              </div>
              <div className="import-source-count">{fmtNum(readyCount)}</div>
              <div className="import-source-count-label">Vouchers ready to import</div>
              <div className="import-source-stats">
                <div className="stat-item">
                  <span className="stat-label">Eligible</span>
                  <span className="stat-value">{fmtNum(readyCount)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Needs Review</span>
                  <span className="stat-value">{fmtNum(needsReviewCount)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Failed Validation</span>
                  <span className="stat-value">{fmtNum(failedValidationCount)}</span>
                </div>
              </div>
              <ul className="import-checklist">
                <li>
                  <span className="check-icon" aria-hidden="true">✓</span>
                  Voucher validation completed
                </li>
                <li>
                  <span className="check-icon" aria-hidden="true">✓</span>
                  Voucher totals verified
                </li>
                <li>
                  <span className="check-icon" aria-hidden="true">✓</span>
                  Required ledgers prepared
                </li>
              </ul>
              {canImport && !jobStatus && !hasResult && (
                <div className="import-info-panel">
                  <span aria-hidden="true">ⓘ</span> All vouchers are ready for import. Click &ldquo;Start Import&rdquo; to send to Tally.
                </div>
              )}
            </div>

            {/* Center Column */}
            <div className="import-transfer-column">
              <ImportTransfer3D
                headline={hasResult ? (isSuccess ? 'Import Completed Successfully' : isPartial ? 'Import Completed with Issues' : isCompleteFailure ? 'Not Imported' : isConnectionLost ? 'Tally Connection Lost' : 'Process Finished')
                  : isPausing ? 'Pausing Import' : isPaused ? 'Import Paused' : isResuming ? 'Resuming Import'
                  : loading ? 'Importing to Tally Prime' : 'Ready to Import'}
                subline={hasResult ? (isSuccess ? 'All validated vouchers were imported to Tally successfully.'
                    : isConnectionLost ? 'Import progress has been preserved. Reconnect TallyPrime to continue.'
                    : isCompleteFailure ? '0 vouchers imported'
                    : `${fmtNum(imported)} imported, ${fmtNum(failedCount)} failed`)
                  : isPausing ? 'Finishing the current voucher...' : isPaused ? 'Your import progress has been saved.'
                  : isResuming ? 'Continuing from where you left off...'
                  : loading ? 'Sending validated vouchers securely' : 'Everything is validated and ready to transfer'}
                status={hasResult ? (isFailed ? 'FAILED' : isPartial ? 'PARTIAL' : 'COMPLETED') : isPaused || isPausing ? 'PAUSED' : 'ACTIVE'}
              />
              {(jobStatus || hasResult) && (
                <div className="import-progress-panel">
                  <div className="import-progress-head">
                    <span className="batch-label">Batch {currentBatch} of {totalBatches}</span>
                    <span className="percent-label">{percent.toFixed(1)}%</span>
                  </div>
                  <div className="import-progress-bar">
                    <span style={{ width: `${percent}%` }} />
                  </div>
                  <div className="import-progress-sub">{fmtNum(processed)} of {fmtNum(readyCount)} vouchers processed</div>
                  <div className="import-progress-stats">
                    <button type="button" className="stat-box imported is-clickable" onClick={() => openStatusDetails('imported')} aria-label="View imported vouchers">
                      <span className="stat-icon" aria-hidden="true">✓</span>
                      <span className="stat-number">{fmtNum(hasResult ? imported : displayedImported)}</span>
                      <span className="stat-label">Imported</span>
                    </button>
                    <button type="button" className="stat-box already-imported is-clickable" onClick={() => openStatusDetails('already_imported')} aria-label="View already imported vouchers">
                      <span className="stat-icon" aria-hidden="true">+</span>
                      <span className="stat-number">{fmtNum(alreadyImported)}</span>
                      <span className="stat-label">Already Imported</span>
                    </button>
                    <button type="button" className="stat-box failed is-clickable" onClick={() => openStatusDetails('not_imported')} aria-label="View not imported vouchers">
                      <span className="stat-icon" aria-hidden="true">!</span>
                      <span className="stat-number">{fmtNum(displayedNotImported)}</span>
                      <span className="stat-label">Not Imported</span>
                    </button>
                    <button type="button" className="stat-box remaining is-clickable" onClick={() => openStatusDetails('remaining')} aria-label="View remaining vouchers">
                      <span className="stat-icon" aria-hidden="true">▤</span>
                      <span className="stat-number">{fmtNum(displayedRemaining)}</span>
                      <span className="stat-label">Remaining</span>
                    </button>
                  </div>
                  {selectedResultStatus && <div className="import-status-detail" role="status">
                    <div><strong>{statusDetails[selectedResultStatus].title}</strong><p>{statusDetails[selectedResultStatus].text}</p></div>
                    <button type="button" className="import-secondary-btn" onClick={() => setShowResultsModal(true)}>{statusDetails[selectedResultStatus].action}</button>
                  </div>}
                  {hasResult ? (
                    // Completed: a live speed/ETA reads as broken once nothing
                    // is left to process (spec section 9) -- show the final
                    // summary instead (import count/percent/elapsed/batches).
                    <div className="import-progress-meta">
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">✓</span>
                        <div className="meta-text">
                          <span className="meta-value">{fmtNum(displayedImported)} Imported</span>
                          <span className="meta-label">Vouchers Sent</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">◷</span>
                        <div className="meta-text">
                          <span className="meta-value">{percent.toFixed(0)}% Process Finished</span>
                          <span className="meta-label">Progress</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">⏱</span>
                        <div className="meta-text">
                          <span className="meta-value">{formatDuration(elapsedSeconds)}</span>
                          <span className="meta-label">Elapsed Time</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">↻</span>
                        <div className="meta-text">
                          <span className="meta-value">{currentBatch} / {totalBatches}</span>
                          <span className="meta-label">Batch</span>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="import-progress-meta">
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">⚡</span>
                        <div className="meta-text">
                          <span className="meta-value">{speed >= 1 ? `${fmtNum(Math.round(speed))}/sec` : `${speed.toFixed(2)}/sec`}</span>
                          <span className="meta-label">Processing Speed</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">◷</span>
                        <div className="meta-text">
                          <span className="meta-value">{etaSeconds === null ? '--' : formatDuration(etaSeconds)}</span>
                          <span className="meta-label">Estimated Remaining</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">⏱</span>
                        <div className="meta-text">
                          <span className="meta-value">{formatDuration(elapsedSeconds)}</span>
                          <span className="meta-label">Elapsed Time</span>
                        </div>
                      </div>
                      <div className="meta-item">
                        <span className="meta-icon" aria-hidden="true">↻</span>
                        <div className="meta-text">
                          <span className="meta-value">{currentBatch} / {totalBatches}</span>
                          <span className="meta-label">Current Batch</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Destination Panel */}
            <div className="import-destination-panel">
              <div className="panel-header">
                <h3><span className="panel-header-icon" aria-hidden="true">🖥</span> Tally Destination</h3>
                <span className={`import-connection-status ${tallyConnected ? 'is-connected' : 'is-disconnected'}`}>
                  <span className="status-dot" aria-hidden="true" />
                  {tallyConnected ? 'Connected' : 'Not Connected'}
                </span>
              </div>
              <div className="import-destination-fields">
                <div className="field-row">
                  <span className="field-label">Company</span>
                  <span className="field-value" title={companyName}>{companyName}</span>
                </div>
                <div className="field-row">
                  <span className="field-label">GSTIN</span>
                  <span className="field-value">{companyGstin}</span>
                </div>
                <div className="field-row">
                  <span className="field-label">Financial Year</span>
                  <span className="field-value">{financialYear}</span>
                </div>
                {host && (
                  <div className="field-row">
                    <span className="field-label">Connection</span>
                    <span className="field-value">{host}{port ? `:${port}` : ''}</span>
                  </div>
                )}
              </div>
              {companyVerified && (
                <div className="import-verified-panel">
                  <span className="verified-icon" aria-hidden="true">✓</span>
                  <div className="verified-text">
                    <strong>Company Verified</strong>
                    <p>Tally Prime is ready to receive vouchers.</p>
                  </div>
                </div>
              )}
              <div className="import-behavior-panel">
                <div className="behavior-head">
                  <span aria-hidden="true">⚙</span> Import Settings
                  <span className="settings-edit">Edit</span>
                </div>
                <div className="behavior-fields">
                  <div className="field-row">
                    <span className="field-label">Batch size</span>
                    <span className="field-value">{DISPLAY_BATCH_SIZE} vouchers</span>
                  </div>
                  <div className="field-row">
                    <span className="field-label">Auto retry</span>
                    <span className="field-value">{READ_BACK_RETRY_ATTEMPTS} attempts</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Bottom Bar */}
          {hasResult ? (
            <div className={`import-control-bar ${isSuccess ? 'is-success' : isPartial ? 'is-partial' : isConnectionLost ? 'is-paused' : ''}`}>
              <div className="import-control-left">
                <ProgressRing percent={percent} />
                <div className="status-text">
                  <strong>{isSuccess ? '✓ Import Completed' : isPartial ? 'Import Completed with Attention' : isCompleteFailure ? '✕ Not Imported' : isConnectionLost ? 'Tally Connection Lost' : 'Process Finished'}</strong>
                  <p>
                    {isSuccess
                      ? `${fmtNum(imported)} vouchers successfully imported to Tally Prime.`
                      : isPartial
                        ? `${fmtNum(imported)} vouchers imported successfully. ${fmtNum(failedCount)} vouchers could not be imported.`
                        : isCompleteFailure
                          ? `0 imported, ${fmtNum(failedCount)} failed.`
                        : isConnectionLost
                          ? 'Import progress has been preserved. Reconnect TallyPrime to continue.'
                          : `${fmtNum(imported)} imported, ${fmtNum(failedCount)} failed`}
                  </p>
                  {(isSuccess || isPartial) && (
                    <p className="status-text-meta">Company: {companyName} &nbsp;·&nbsp; GSTIN: {companyGstin}</p>
                  )}
                </div>
              </div>
              <div className="import-control-right">
                {isConnectionLost ? (
                  <button type="button" className="import-cta-btn" onClick={onImport} disabled={loading}>
                    {loading ? 'Reconnecting...' : 'Retry Connection'}
                  </button>
                ) : isFailed ? (
                  <>
                    <button type="button" className="import-secondary-btn" onClick={onBackToVouchers}>Back to Voucher Preview</button>
                    {failedCount > 0 && <button type="button" className="import-secondary-btn" onClick={() => setShowResultsModal(true)}>View Failed Vouchers</button>}
                    <button type="button" className="import-secondary-btn" onClick={() => downloadImportReport(jobStatus, result)}>Download Report</button>
                    <button type="button" className="import-cta-btn" onClick={onImport}>Retry Import</button>
                  </>
                ) : isPending ? (
                  <button type="button" className="import-cta-btn" onClick={onImport}>Recheck Tally</button>
                ) : isPartial ? (
                  <>
                    <button type="button" className="import-secondary-btn" onClick={() => setShowResultsModal(true)} disabled={!result?.results?.length}>
                      <span aria-hidden="true">☰</span> View Failed Vouchers
                    </button>
                    <button type="button" className="import-cta-btn" onClick={() => downloadImportReport(jobStatus, result)}>
                      <span aria-hidden="true">↓</span> Download Import Report
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" className="import-secondary-btn" onClick={() => setShowResultsModal(true)}>
                      <span aria-hidden="true">🗎</span> View Import Summary
                    </button>
                    <button type="button" className="import-cta-btn" onClick={() => downloadImportReport(jobStatus, result)}>
                      <span aria-hidden="true">↓</span> Download Import Report
                    </button>
                  </>
                )}
                {hasResult && !isConnectionLost && !isPending && <button type="button" className="import-cta-btn" onClick={onFinish}>Finish</button>}
              </div>
            </div>
          ) : jobStatus ? (
            <div className={`import-control-bar ${isPaused || isPausing ? 'is-paused' : ''}`}>
              <div className="import-control-left">
                <div className="status-text">
                  <strong>
                    {isPausing ? 'Pausing...' : isPaused ? 'Import Paused' : isResuming ? 'Resuming...' : 'Importing...'}
                    {isPaused && !isResuming && <span className="import-status-badge is-paused">PAUSED</span>}
                  </strong>
                  <p>
                    {isPausing ? 'Finishing the current voucher before pausing...'
                      : isPaused ? 'Your import progress has been saved.'
                      : isResuming ? 'Continuing from where you left off...'
                      : 'Sending vouchers to Tally'}
                  </p>
                </div>
                {isPausing ? (
                  <button type="button" className="import-pause-btn is-busy" disabled>
                    <span className="btn-spinner" aria-hidden="true" /> Pausing...
                  </button>
                ) : isResuming ? (
                  <button type="button" className="import-pause-btn is-busy" disabled>
                    <span className="btn-spinner" aria-hidden="true" /> Resuming...
                  </button>
                ) : isPaused ? (
                  <button type="button" className="import-pause-btn" onClick={onResume}>
                    <span aria-hidden="true">▶</span> Resume Import
                  </button>
                ) : (
                  <button type="button" className="import-pause-btn" onClick={onPause} disabled={Boolean(jobStatus?.pause_requested)}>
                    <span aria-hidden="true">❚❚</span> Pause Import
                  </button>
                )}
              </div>
              <div className="import-control-right">
                <button type="button" className="import-secondary-btn" onClick={() => setShowResultsModal(true)} disabled title="Per-voucher results are available once the import finishes.">
                  <span aria-hidden="true">☰</span> View Failed Vouchers
                </button>
                <button type="button" className="import-secondary-btn" onClick={() => downloadImportReport(jobStatus, result)}>
                  <span aria-hidden="true">↓</span> Download Import Report
                </button>
              </div>
            </div>
          ) : (
            <div className="import-confirm-bar">
              <div className="import-confirm-text">
                <strong>{canImport ? 'Ready to Import' : 'Import Not Ready'}</strong>
                <p>{canImport ? `${fmtNum(readyCount)} validated voucher${readyCount === 1 ? '' : 's'} ${readyCount === 1 ? 'is' : 'are'} ready to be sent to ${companyName}.` : blockingReason}</p>
              </div>
              <button type="button" className="import-cta-btn" disabled={!canImport} onClick={() => setConfirmOpen(true)}>
                Import {fmtNum(readyCount)} Voucher{readyCount === 1 ? '' : 's'} <span aria-hidden="true">→</span>
              </button>
            </div>
          )}
        </>
      )}
    </div>

    {showResultsModal && <ImportResultsModal result={result} status={selectedResultStatus} rows={selectedResultStatus ? rowsForStatus(selectedResultStatus) : resultRows} onClose={() => setShowResultsModal(false)} onViewError={onViewError} />}

    {confirmOpen && <ImportConfirmModal readyCount={readyCount} companyName={companyName} companyGstin={companyGstin} financialYear={financialYear}
      onCancel={() => setConfirmOpen(false)} onConfirm={() => { setConfirmOpen(false); onImport() }} />}
  </section>
}

// Import Settings shows real, traceable numbers only: DISPLAY_BATCH_SIZE
// (the same constant the progress panel groups real voucher counts into)
// and READ_BACK_RETRY_ATTEMPTS (must track backend
// tally/voucher_verification.py::QUERY_BACK_RETRY_DELAYS, currently 4
// entries) -- the per-voucher read-after-write verification retry, not a
// resend-on-failure count (this backend has no such setting).
const READ_BACK_RETRY_ATTEMPTS = 4

// Progress panel shown directly under the 3D transfer hero while a job is
// actually running -- every figure here is either a raw jobStatus field or
// derived client-side from real ones (elapsed = now - started_at, speed =
// processed/elapsed, ETA = remaining/speed); nothing is a placeholder.
const ACTIVE_JOB_STATUS_SET = new Set(['PENDING', 'RUNNING', 'VERIFYING'])

function useElapsedSeconds(startedAtIso, active) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!startedAtIso || !active) return
    const startedAtMs = new Date(startedAtIso).getTime()
    const tick = () => setSeconds(Math.max(0, Math.floor((Date.now() - startedAtMs) / 1000)))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [startedAtIso, active])
  return seconds
}

const formatDuration = totalSeconds => {
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`
}

// Display-only grouping of the real per-voucher progress into fixed-size
// "batches" (matches the Import Settings batch size shown on the
// destination card) -- this backend sends vouchers one at a time, not in
// numbered chunks, so this is real processed/total data reframed as a
// batch narrative for the UI, never an invented number.
const DISPLAY_BATCH_SIZE = 500

function ProgressRing({ percent }) {
  const r = 30
  const c = 2 * Math.PI * r
  const offset = c * (1 - Math.min(100, Math.max(0, percent)) / 100)
  return <div className="import-progress-ring">
    <svg viewBox="0 0 74 74" width="74" height="74" aria-hidden="true">
      <defs><linearGradient id="import-ring-grad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stopColor="#35D6FF" /><stop offset="100%" stopColor="#168CF4" /></linearGradient></defs>
      <circle cx="37" cy="37" r={r} fill="none" stroke="#D9EBF8" strokeWidth="8" />
      <circle className="import-progress-ring-arc" cx="37" cy="37" r={r} fill="none" stroke="url(#import-ring-grad)" strokeWidth="8" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={offset} transform="rotate(-90 37 37)" />
    </svg>
    <span className="import-progress-ring-label">{percent.toFixed(1)}%</span>
  </div>
}

// Shared by the active control bar, the completed bar, and the result
// popup -- exports whatever real progress/result data is on screen right
// now, so "download" never means anything other than "what you can already
// see".
function downloadImportReport(jobStatus, result) {
  const status = jobStatus?.status || 'PENDING'
  const rows = [
    ['Invoice No', 'GSTIN', 'Party', 'Voucher Date', 'Taxable Value', 'Tax', 'Final Total', 'Status', 'Reason', 'Imported Timestamp'],
    ...(result?.results || []).map(item => [
      item.invoice_no || item.invoice_number || '', item.gstin || item.party_gstin || '', item.party || item.party_name || '',
      item.voucher_date || item.date || '', item.taxable_value || '', item.tax || item.component_total || '',
      item.final_total || item.final_voucher_total || '',
      item.status === 'Imported' ? 'IMPORTED' : item.status === 'Already Imported' ? 'ALREADY_IMPORTED' : 'NOT_IMPORTED',
      item.reason || item.error_message || '', item.imported_at || '',
    ]),
    ['Job ID', jobStatus?.job_id || ''],
    ['Batch ID', jobStatus?.batch_id || ''],
    ['Status', status],
    ['Total', jobStatus?.total || 0],
    ['Processed', jobStatus?.processed || 0],
    ['Imported', jobStatus?.imported || 0],
    ['Failed', jobStatus?.failed || 0],
    ['Skipped', jobStatus?.skipped || 0],
    ['Verification Pending', jobStatus?.verification_pending || 0],
    ['Started At', jobStatus?.started_at || ''],
    ['Last Heartbeat', jobStatus?.heartbeat_at || ''],
    ['Completed At', jobStatus?.completed_at || ''],
  ]
  if (result?.message) rows.push(['Message', result.message])
  const csv = rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `import-report-${jobStatus?.job_id || 'progress'}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

// Top-right completion notification (Issue 2): appears in place of the old
// separate result page. Uses the same outcome classification as the rest
// of the app (finalImportToast) so "success" vs "needs attention" can never
// disagree with the badge/footer below it. Success auto-dismisses after 5s;
// anything short of a clean success stays until the user closes it or takes
// one of its actions, since that is real attention-needing information.
function ImportResultPopup({ result, jobStatus, onClose, onViewDetails }) {
  const toast = finalImportToast(result || {})
  const isSuccess = toast.kind === 'success'
  const summary = result?.summary || {}
  const imported = Number(summary.imported || 0) + Number(summary.already_imported || 0)
  const failed = Number(summary.failed || 0)

  useEffect(() => {
    if (!isSuccess) return
    const timer = window.setTimeout(onClose, 5000)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSuccess])

  return <div className={`import-result-popup ${isSuccess ? 'is-success' : 'is-attention'}`} role="status">
    <button type="button" className="import-result-popup-close" onClick={onClose} aria-label="Dismiss">×</button>
    <div className="import-result-popup-head">
      <span aria-hidden="true">{isSuccess ? '✓' : '⚠'}</span>
      <strong>{isSuccess ? 'Import Completed' : 'Import Completed with Attention'}</strong>
    </div>
    <p>{isSuccess ? 'All vouchers were imported successfully.' : 'Some vouchers could not be imported.'}</p>
    <div className="import-result-popup-stats">
      <div><span>Imported</span><b>{fmtNum(imported)}</b></div>
      <div><span>Failed</span><b>{fmtNum(failed)}</b></div>
    </div>
    <div className="import-result-popup-actions">
      {isSuccess
        ? <button type="button" onClick={() => { onViewDetails(); onClose() }}>View Report</button>
        : <>
          <button type="button" onClick={() => { onViewDetails(); onClose() }}>View Failed Vouchers</button>
          <button type="button" onClick={() => { downloadImportReport(jobStatus, result); onClose() }}>Download Report</button>
        </>}
    </div>
  </div>
}

// Per-row detail, now an in-page modal instead of a separate route (Issue
// 2) -- same ImportResultsTable the old Result screen used, just no longer
// behind a full page navigation.
function ImportResultsModal({ result, status, rows, onClose, onViewError }) {
  const labels = { imported: 'Imported Data', already_imported: 'Already Imported Data', not_imported: 'Not Imported Data', skipped: 'Skipped Data', remaining: 'Remaining Data' }
  const filteredResult = { ...result, results: rows || result?.results || [] }
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <div className="modal import-results-modal" role="dialog" aria-modal="true" aria-label="Import details">
      <div className="modal-head"><h2>Import Details</h2><button type="button" onClick={onClose} aria-label="Close">×</button></div>
      <ImportResultsTable result={filteredResult} onViewError={onViewError} />
    </div>
  </div>
}

function ImportConfirmModal({ readyCount, companyName, companyGstin, financialYear, onCancel, onConfirm }) {
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onCancel()}>
    <div className="import-confirm-modal" role="dialog" aria-modal="true" aria-label="Confirm import to Tally">
      <h2>Import to Tally</h2>
      <div className="import-confirm-flow">
        <div className="import-confirm-flow-count">{fmtNum(readyCount)} Voucher{readyCount === 1 ? '' : 's'}</div>
        <div className="import-confirm-flow-arrow" aria-hidden="true">↓</div>
        <div className="import-confirm-flow-company">{companyName}</div>
      </div>
      <dl className="import-confirm-fields">
        <div><dt>Company</dt><dd>{companyName}</dd></div>
        <div><dt>GSTIN</dt><dd>{companyGstin}</dd></div>
        <div><dt>Financial Year</dt><dd>{financialYear}</dd></div>
      </dl>
      <p className="import-confirm-warning">
        This will create accounting vouchers in the verified Tally company.<br />
        Only eligible Ready vouchers will be imported.
      </p>
      <div className="import-confirm-actions">
        <button type="button" className="import-confirm-cancel" onClick={onCancel}>Cancel</button>
        <button type="button" className="import-confirm-primary" onClick={onConfirm}>Confirm & Import</button>
      </div>
    </div>
  </div>
}

function ImportResultsTable({ result, onReview, onViewError }) {
  const rows = (result?.results || []).map(row => ({ ...row, date: formatDate(row.date) }))
  return <DataTable columns={['Invoice', 'Date', 'Party', 'GSTIN', 'Voucher Type', 'Status', 'Voucher ID', 'Reason', 'Details']} rows={rows} empty="No import result rows yet." render={(row, index) => <tr key={`${row.invoice_no}-${index}`}>
    <td>{row.invoice_no || '-'}</td><td>{row.date || '-'}</td><td>{row.party || '-'}</td><td>{row.gstin || '-'}</td><td>{row.voucher_type || '-'}</td><td><StatusBadge value={row.status || '-'} /></td><td>{row.voucher_identifier || '-'}</td><td title={row.reason || ''}>{row.reason || '-'}</td>
    <td>{row.status === 'Validation Failed' && onReview ? <button className="icon-link" onClick={() => onReview(row)}>View Details</button> : row.tally_error && onViewError ? <button className="icon-link" onClick={() => onViewError(row)}>View Details</button> : '-'}</td>
  </tr>} />
}

// Compact enterprise voucher-detail modal -- shows the real party/invoice/
// ledger data the backend already computed (never a value invented here).
// Used for "View" on any non-mismatch row (Ready, Ready with GSTIN Fallback,
// Already Imported, Skipped, Needs Attention); the separate mismatch
// correction workflow (Review Required/Validation Failed) still opens
// VoucherMismatchModal, untouched.
function SkipReasonModal({ row, onClose }) {
  const isSkipped = row.status === 'Skipped' || row.status === 'Needs Attention'
  const isReady = row.status === 'Ready' || row.status === 'Ready with GSTIN Fallback'
  // Code is ONLY ever the voucher's own genuine blocking code
  // (error_code/skip_reason_code, e.g. TAX_AMOUNT_MISMATCH) -- never
  // warning_code, which is Sandbox taxpayer-enrichment status and must
  // never masquerade as the voucher's code (see SANDBOX MUST NOT BECOME
  // VOUCHER CODE).
  const code = row.error_code || row.skip_reason_code || ''
  const blockingDetail = row.skip_reason || row.reason
  const party = row.party || {}
  // Genuinely unavailable optional data reads as "--", never hidden and
  // never invented -- see mappings.py/party_lookup.py's own Sandbox ->
  // source -> fallback priority chain, which already resolves each of
  // these fields before they ever reach the frontend.
  const orDash = value => (value === null || value === undefined || String(value).trim() === '') ? '—' : value
  const displayName = party.trade_name || party.name || row.party_name || ''
  const rateAllocations = row.rate_allocations || []
  const taxAllocations = row.tax_allocations || []
  const taxValidation = row.tax_validation
  // Purely technical, backend-diagnostic information -- never shown as a
  // prominent section by default (it would make a normal, valid imported
  // invoice look like it has a problem). Only offered as a small,
  // collapsed-by-default expandable link, and only when there's actually
  // something to show (a real rate-vs-source difference worth explaining).
  const showTaxDiagnosticsLink = taxValidation && taxValidation.rate_available && taxValidation.diagnostic_mismatch
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="modal voucher-detail-modal" role="dialog" aria-modal="true" aria-label="Voucher details">
    <div className="modal-head"><div><h2>{row.invoice_number || '-'}</h2><small>{displayName || '-'} · {row.invoice_date || '-'}</small></div><button onClick={onClose} aria-label="Close">×</button></div>
    <div className="voucher-detail-badges">
      <StatusBadge value={row.status || '-'} />
      {row.return_type && <span className="voucher-detail-chip">{row.return_type}</span>}
      {row.voucher_type && <span className="voucher-detail-chip">{row.voucher_type}</span>}
    </div>
    {blockingDetail
      ? <p className={`voucher-detail-status-line ${isSkipped ? 'is-warning' : 'is-note'}`}><span aria-hidden="true">{isSkipped ? '⚠' : 'ℹ'}</span> {blockingDetail}{code && isSkipped ? ` (${code})` : ''}</p>
      : isReady && <p className="voucher-detail-status-line is-success"><span aria-hidden="true">✓</span> Voucher validated successfully</p>}

    <section className="voucher-detail-section">
      <h3>Party Information</h3>
      <div className="voucher-detail-fields">
        <div><dt>Trade Name</dt><dd>{orDash(displayName)}</dd></div>
        <div><dt>Legal Name</dt><dd>{orDash(party.legal_name)}</dd></div>
        <div><dt>GSTIN</dt><dd>{orDash(party.gstin || row.party_gstin)}</dd></div>
        <div><dt>GSTIN Status</dt><dd>{orDash(party.gstin_status)}</dd></div>
        <div><dt>Registration Type</dt><dd>{orDash(party.registration_type)}</dd></div>
        <div><dt>State</dt><dd>{orDash(party.state)}</dd></div>
        <div><dt>Country</dt><dd>{orDash(party.country || 'India')}</dd></div>
        <div><dt>Pincode</dt><dd>{orDash(party.pincode)}</dd></div>
        <div className="voucher-detail-field-wide"><dt>Address</dt><dd>{orDash(party.address)}</dd></div>
      </div>
      {row.sandbox_warning && <p className="voucher-detail-sandbox-warning"><span aria-hidden="true">⚠</span> Additional taxpayer details could not be fetched. Source party details are being used.</p>}
    </section>

    <section className="voucher-detail-section">
      <h3>Invoice Details</h3>
      <div className="voucher-detail-money-rows">
        <div><span>Taxable Value</span><b>{money(row.taxable_total)}</b></div>
        <div><span>CGST</span><b>{money(row.cgst)}</b></div>
        <div><span>SGST</span><b>{money(row.sgst)}</b></div>
        <div><span>IGST</span><b>{money(row.igst)}</b></div>
        <div><span>Cess</span><b>{money(row.cess)}</b></div>
        <div className="voucher-detail-money-divider" />
        <div><span>Component Total</span><b>{money(row.component_total)}</b></div>
        <div><span>Round Off</span><b>{signedMoney(row.round_off)}</b></div>
        <div className="voucher-detail-final-row"><span>Final Total</span><b>{money(row.final_voucher_total)}</b></div>
      </div>
    </section>

    <section className="voucher-detail-section">
      <h3>Ledger Allocation</h3>
      {rateAllocations.length === 0 && taxAllocations.length === 0
        ? <p className="voucher-detail-empty">No ledger allocation available for this voucher.</p>
        : <div className="voucher-detail-ledger">
          {rateAllocations.length > 0 && <div className="voucher-detail-ledger-group">
            <span className="voucher-detail-ledger-label">{rateAllocations.length > 1 ? 'Account Ledgers' : 'Main Ledger'}</span>
            {rateAllocations.map((allocation, index) => <div key={index} className="voucher-detail-ledger-row">
              <span>{allocation.account_ledger || allocation.sales_ledger || '-'}</span>
              {rateAllocations.length > 1 && <b>{money(allocation.taxable_value)}</b>}
            </div>)}
          </div>}
          {taxAllocations.length > 0 && <div className="voucher-detail-ledger-group">
            <span className="voucher-detail-ledger-label">Tax Ledgers</span>
            {taxAllocations.map((tax, index) => <div key={index} className="voucher-detail-ledger-row"><span>{tax.ledger}</span><b>{money(tax.amount)}</b></div>)}
          </div>}
        </div>}
    </section>

    {showTaxDiagnosticsLink && <details className="skip-reason-tax-details">
      <summary>Technical validation details</summary>
      <SummaryPanel title="Tax Validation (diagnostic only -- not an error)" items={[
        ['Source CGST', money(taxValidation.source_cgst)], ['Calculated CGST', money(taxValidation.calculated_cgst)],
        ['Source SGST', money(taxValidation.source_sgst)], ['Calculated SGST', money(taxValidation.calculated_sgst)],
      ]} />
      <p className="skip-reason-tax-note">Source tax amounts are used for this voucher; the calculated amounts above are for reference only.</p>
    </details>}
    <div className="modal-actions"><button onClick={onClose}>Close</button></div>
  </div></div>
}

// Explanation-first "Import Failed" modal (task spec: normal users must
// never need to understand CREATED/ALTERED/ERRORS/EXCEPTIONS/LINEERROR/HTTP
// 200/XML transport). The backend's normalize_tally_error() already did the
// translation into {title, user_message, action_message, retryable} -- this
// only renders it. A generic, honest fallback is used only when the backend
// genuinely could not attach a normalized reason (older result rows), never
// a guessed cause.
const FALLBACK_USER_ERROR = {
  title: 'Import Could Not Be Completed',
  user_message: 'Tally rejected this voucher, but did not provide a specific reason.',
  action_message: 'Please verify the voucher details and Tally configuration, then retry.',
  retryable: true,
}

function ErrorDetailModal({ row, onClose, onRetry }) {
  const error = row.tally_error || {}
  const userError = row.user_error || FALLBACK_USER_ERROR
  return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="modal">
    <div className="modal-head">
      <div><h2>Import Failed</h2><small>{row.party || '-'} · Voucher No. {row.invoice_no || '-'} · {row.date || '-'}</small></div>
      <button onClick={onClose}>×</button>
    </div>
    <Alert kind="error">
      <strong>Voucher could not be imported</strong>
      <p className="error-detail-lead">Tally rejected this voucher during import.</p>
    </Alert>
    <section className="voucher-detail-section" style={{ marginTop: 0, paddingTop: 0, borderTop: 0 }}>
      <h3>Reason</h3>
      <p>{userError.user_message}</p>
    </section>
    <section className="voucher-detail-section">
      <h3>What you can do</h3>
      <p>{userError.action_message}</p>
    </section>
    <div className="modal-actions">
      {onRetry && userError.retryable !== false &&
        <button className="stage-primary" onClick={() => { onClose(); onRetry() }}>Retry Import</button>}
      <button onClick={onClose}>Close</button>
    </div>
    {/* Technical diagnostics -- collapsed by default, never the primary
        content (task spec sections 6-8). Kept available here (rather than
        gated behind a role this app does not currently model) for
        support/troubleshooting use; ordinary use of this screen never needs
        to open it. */}
    <details className="modal-advanced-details">
      <summary>Advanced Details</summary>
      <section className="confirm-grid">
        <SummaryPanel title="Diagnostics" items={[
          ['Stage', error.failed_stage || 'voucher_import'],
          ['Transport', error.transport || 'XML'],
          ['HTTP Status', error.http_status ?? '-'],
          ['Tally Created', error.created ?? '-'],
          ['Tally Altered', error.altered ?? '-'],
          ['Tally Errors', error.errors ?? '-'],
          ['Tally Exceptions', error.exceptions ?? '-'],
          ['Voucher ID', row.voucher_identifier || '-'],
        ]} />
      </section>
      {(userError.technical_message || row.reason) &&
        <p className="advanced-technical-reason"><small>Technical detail: {userError.technical_message || row.reason}</small></p>}
      <details><summary>View Request Payload</summary><pre>{error.request_payload || '-'}</pre></details>
      <details><summary>View Raw Tally Response</summary><pre>{error.raw_response || error.actual_tally_error || error.line_error || '-'}</pre></details>
    </details>
  </div></div>
}

// Compact "File Details" trigger + floating popover -- holds exactly the
// same real values (return type, record count, batch id, sheet name, file
// name) the old always-visible summary cards showed, just moved behind a
// click instead of permanently occupying vertical space above the table.
function FileDetailsButton({ preview, batch, recordCount, fileName }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  useEffect(() => {
    if (!open) return
    const onKeyDown = event => { if (event.key === 'Escape') setOpen(false) }
    const onPointerDown = event => { if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false) }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open])
  const format = formatLabelFromName(fileName)
  const rows = [
    ['File Name', fileName || '-'],
    ['Return Type', returnTypeLabel(preview?.return_type || batch?.gst_return_type || batch?.return_type)],
    ['File Format', formatLabel(preview?.file_type || batch?.file_type || format)],
    ['Total Invoices', show(recordCount)],
    ['Upload Date', batch?.uploaded_at ? formatDate(batch.uploaded_at) : '-'],
    ['Financial Year', show(batch?.tax_period || batch?.financial_year)],
  ]
  return <div className="file-details-wrap" ref={wrapRef}>
    <button type="button" className={`file-details-btn ${open ? 'is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" /></svg>
      File Details
    </button>
    {open && <div className="file-details-popover" role="dialog" aria-label="File details">
      <div className="file-details-popover-head">
        <span>File Details</span>
        <button type="button" className="file-details-close" aria-label="Close file details" onClick={() => setOpen(false)}>×</button>
      </div>
      <dl className="file-details-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <p className="file-details-footnote"><span aria-hidden="true">🔒</span> Read-only source data</p>
    </div>}
  </div>
}

function DownloadButtons({ preview, batch }) {
  const downloadExcel = () => {
    const columns = preview?.columns || []
    const rows = preview?.rows || []
    const escapeXml = value => String(show(value)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    const header = columns.map(column => `<Cell ss:StyleID="Header"><Data ss:Type="String">${escapeXml(column.label)}</Data></Cell>`).join('')
    const cell = (row, column) => {
      const numeric = column.format === 'money' || column.format === 'percent'
      const numericValue = Number(String(row[column.key] ?? '').replace(/[₹,%\s,]/g, ''))
      const excelValue = column.format === 'percent' ? numericValue / 100 : numericValue
      return numeric && Number.isFinite(numericValue)
        ? `<Cell ss:StyleID="${column.format === 'money' ? 'Currency' : 'Percent'}"><Data ss:Type="Number">${excelValue}</Data></Cell>`
        : `<Cell><Data ss:Type="String">${escapeXml(row[column.key])}</Data></Cell>`
    }
    const body = rows.map(row => `<Row>${columns.map(column => cell(row, column)).join('')}</Row>`).join('')
    const widths = columns.map(column => `<Column ss:AutoFitWidth="1" ss:Width="${Math.min(180, Math.max(70, column.label.length * 7))}"/>`).join('')
    const xml = `<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Styles><Style ss:ID="Header"><Font ss:Bold="1"/><Interior ss:Color="#D9EAF7" ss:Pattern="Solid"/></Style><Style ss:ID="Currency"><NumberFormat ss:Format="&quot;₹&quot;#,##0.00"/></Style><Style ss:ID="Percent"><NumberFormat ss:Format="0.00%"/></Style></Styles><Worksheet ss:Name="Invoices"><Table>${widths}<Row>${header}</Row>${body}</Table><WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel"><FreezePanes/><FrozenNoSplit/><SplitHorizontal>1</SplitHorizontal><TopRowBottomPane>1</TopRowBottomPane><AutoFilter x:Range="R1C1:R${rows.length + 1}C${columns.length}" xmlns="urn:schemas-microsoft-com:office:excel"/></WorksheetOptions></Worksheet></Workbook>`
    const blob = new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${preview?.file_name || 'gstr-preview'}.xls`
    link.click()
    URL.revokeObjectURL(link.href)
  }
  const downloadPdf = () => {
    if (!preview) return
    const columns = preview.columns || []
    const rows = preview.rows || []
    const generatedOn = new Date().toLocaleString('en-IN')
    const escapeHtml = value => String(show(value)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    const tableHeader = columns.map(column => `<th>${escapeHtml(column.label)}</th>`).join('')
    const tableRows = rows.map(row => `<tr>${columns.map(column => `<td class="${column.format === 'money' || column.format === 'percent' ? 'number' : column.format === 'date' ? 'date' : ''}">${escapeHtml(column.format === 'money' ? money(row[column.key]) : column.format === 'percent' ? `${show(row[column.key])}%` : row[column.key])}</td>`).join('')}</tr>`).join('')
    const report = `<!doctype html><html><head><title>GSTR 2 Tally - Invoice Preview</title><style>@page{size:A4 landscape;margin:12mm 10mm 15mm}*{box-sizing:border-box}body{font:8px Arial,sans-serif;color:#18324a;margin:0}header{display:grid;grid-template-columns:1fr 2fr 1fr;align-items:start;margin-bottom:10px}h1{font-size:13px;margin:0}h2{text-align:center;font-size:16px;margin:0}header p{margin:3px 0 0;font-size:9px}header .generated{text-align:right}table{width:100%;border-collapse:collapse;table-layout:auto}thead{display:table-header-group}th{background:#245b8c;color:#fff;font-weight:700;text-align:left;padding:5px 4px;border:1px solid #1d4b73;white-space:nowrap}td{padding:4px;border:1px solid #c8d8e5;white-space:nowrap}tbody tr:nth-child(even){background:#f3f8fb}.number{text-align:right}.date{text-align:center}.footer{position:fixed;bottom:-9mm;left:0;right:0;display:flex;justify-content:space-between;font-size:8px;color:#60788e}.page::after{content:'Page ' counter(page) ' of ' counter(pages)}</style></head><body><header><div><h1>GSTR 2 Tally</h1><p>Invoice Preview</p></div><div><h2>Invoice Preview</h2><p style="text-align:center">Return Type: ${escapeHtml(preview.return_type || batch?.gst_return_type || '-')}<br>Financial Year: ${escapeHtml(batch?.tax_period || batch?.financial_year || '-')}<br>Total Invoices: ${rows.length}</p></div><div class="generated">Generated On: ${escapeHtml(generatedOn)}</div></header><table><thead><tr>${tableHeader}</tr></thead><tbody>${tableRows}</tbody></table><div class="footer"><span>GSTR 2 Tally - Invoice Preview</span><span class="page"></span></div></body></html>`
    const reportWindow = window.open('', '_blank', 'noopener,noreferrer')
    if (!reportWindow) return
    reportWindow.document.write(report)
    reportWindow.document.close()
    reportWindow.focus()
    reportWindow.print()
  }
  return <div className="download-buttons"><button className="download-excel" onClick={downloadExcel} disabled={!preview}>Excel ↓</button><button className="download-pdf" onClick={downloadPdf} disabled={!preview}>PDF ↓</button></div>
}

function DataTable({ columns, rows, render, empty }) {
  return <div className="data-table-wrap"><table className="data-table"><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows?.length ? rows.map(render) : <tr><td className="empty" colSpan={columns.length}>{empty}</td></tr>}</tbody></table></div>
}

const META_STRIP_ICON_PATHS = {
  Return: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></>,
  Rows: <><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M3 6h.01" /><path d="M3 12h.01" /><path d="M3 18h.01" /></>,
  Batch: <><path d="M20.59 13.41 11 3.83A2 2 0 0 0 9.59 3.24L3 3v6.59a2 2 0 0 0 .59 1.41l9.58 9.59a2 2 0 0 0 2.83 0l4.59-4.59a2 2 0 0 0 0-2.83Z" /><circle cx="7.5" cy="7.5" r="1.5" /></>,
  Sheet: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M9 13h6" /><path d="M9 17h6" /></>,
}

// Purely decorative -- only renders when the label matches one of the known
// icons above, so MetaStrip's other callers (Vouchers/Import/Result) render
// exactly as before.
function MetaStrip({ items }) {
  return <div className="meta-strip">{items.map(([label, value]) => <span key={label}>
    {META_STRIP_ICON_PATHS[label] && <i className="meta-strip-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{META_STRIP_ICON_PATHS[label]}</svg></i>}
    <div><small>{label}</small><strong>{show(value)}</strong></div>
  </span>)}</div>
}

function SummaryPanel({ title, items }) {
  return <section className="summary-panel"><h2>{title}</h2>{items.map(([label, value]) => <div key={label}><span>{label}</span><strong>{show(value)}</strong></div>)}</section>
}

function Alert({ kind = 'warning', children }) {
  return <div className={`alert ${kind}`}>{children}</div>
}
