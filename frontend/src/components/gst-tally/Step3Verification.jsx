import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import LoadingButton from '../common/LoadingButton'
import { step3Ready, step3Rows } from '../../utils/step3Verification'

export default function Step3Verification({ companyInfo, companyResult, licenseResult, confirmLoading, confirmDone, onSelectGstin, onConfirmMasters, onRetryLicense }) {
  const [visible, setVisible] = useState(true)
  useEffect(() => setVisible(true), [licenseResult, companyResult])
  const needsPick = companyInfo?.error === 'MULTIPLE_COMPANY_GSTINS' && !companyResult
  if (!licenseResult && !needsPick) return null
  const complete = step3Ready(licenseResult) && companyResult?.company_details_saved === true && companyResult?.company_verified === true
  const rows = licenseResult ? step3Rows(licenseResult) : []
  const errors = [...(licenseResult?.errors || [])]
  if (companyResult && !companyResult.company_details_saved) {
    errors.push({ code: 'COMPANY_DETAILS_NOT_SAVED', message: companyResult.message || 'Verify and save company details before continuing.' })
  }
  const title = needsPick ? 'Select Company GSTIN' : !companyResult ? 'Verifying company details...' : complete ? 'Verification Complete' : 'Verification Failed'
  const showConnectionHelp = errors.some(error => ['TALLY_NOT_CONNECTED', 'TALLY_LICENSE_DATA_UNAVAILABLE'].includes(error.code))
  return <>
    {visible && createPortal(<div className="step3-verification-overlay">
      <section className="step3-verification" role="dialog" aria-modal="true" aria-label={title}>
        <header className="step3-header">
          <div><h2>{title}</h2><p>{complete ? 'All mandatory verification checks have passed.' : needsPick ? 'Select the source company GSTIN to continue.' : !companyResult ? 'Please wait while company details are verified.' : 'Some verification checks need attention before you can continue.'}</p></div>
          <button className="step3-close" aria-label="Close verification details" onClick={() => setVisible(false)}><VerificationIcon /></button>
        </header>
        {needsPick && <select className="step3-gstin-select" defaultValue="" onChange={event => event.target.value && onSelectGstin(event.target.value)}>
          <option value="">Select source GSTIN</option>
          {(companyInfo.company_gstin_candidates || []).map(value => <option key={value}>{value}</option>)}
        </select>}
        {licenseResult && <>
          <div className="step3-table-wrap"><table className="step3-table">
            <thead><tr><th>Verification</th><th>Expected</th><th>Detected</th><th>Status</th></tr></thead>
            <tbody>{rows.map(([label, expected, detected, passed]) => <tr key={label}>
              <th scope="row">{label}</th><td>{expected}</td><td>{detected}</td><td><span className={`step3-status ${passed ? 'step3-status-pass' : 'step3-status-fail'}`}><VerificationIcon passed={passed} />{passed ? 'Correct' : 'Action Required'}</span></td>
            </tr>)}</tbody>
          </table></div>
          {errors.length > 0 && <section className="step3-issues">
            <h3>Issues Found <span>({errors.length})</span></h3>
            <div className="step3-issue-list" tabIndex={0} role="region" aria-label="Verification issues">
              {errors.map(error => <div className="step3-issue" key={error.code}>
                <span className="step3-issue-icon" aria-hidden="true">!</span>
                <div className="step3-issue-content"><div className="step3-issue-heading"><strong>{errorTitles[error.code] || 'Verification requires attention'}</strong><span className="step3-error-code">{error.code}</span></div><p>{error.message}</p></div>
              </div>)}
            </div>
          </section>}
          {showConnectionHelp && <aside className="step3-help"><strong>What to do</strong><span>1. Start TallyPrime</span><span>2. Enable HTTP/ODBC</span><span>3. Open correct company</span><span>4. Retry</span></aside>}
        </>}
        <footer className="step3-footer">
          <button className="step3-secondary" onClick={() => setVisible(false)}>Close</button>
          {!needsPick && !complete && <LoadingButton className="step3-primary" onClick={onRetryLicense}>Retry Verification</LoadingButton>}
          {complete && <LoadingButton className="step3-primary" loading={confirmLoading} disabled={!complete || confirmDone} onClick={onConfirmMasters}>{confirmDone ? 'Confirmed' : 'Confirm & Continue'}</LoadingButton>}
        </footer>
      </section>
    </div>, document.body)}
    {!visible && <button className="step3-primary step3-reopen" onClick={() => setVisible(true)}>{title} - View Details</button>}
  </>
}

function VerificationIcon({ passed = false }) {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d={passed ? 'M5 12l4 4L19 6' : 'M6 6l12 12M18 6L6 18'} />
  </svg>
}

const errorTitles = {
  COMPANY_GSTIN_MISMATCH: 'Incorrect Company Opened',
  TALLY_SERIAL_MISMATCH: 'Incorrect Tally License In Use',
  PRODUCT_LICENSE_NOT_CONFIGURED: 'Tally License Not Configured',
  PRODUCT_LIMIT_REACHED: 'Product Limit Reached',
  DEVICE_LIMIT_REACHED: 'Device Limit Reached',
  DEVICE_NOT_AUTHORIZED: 'Device Not Authorized',
  TALLY_NOT_CONNECTED: 'Tally Not Connected',
  TALLY_LICENSE_DATA_UNAVAILABLE: 'Tally License Could Not Be Read',
}
