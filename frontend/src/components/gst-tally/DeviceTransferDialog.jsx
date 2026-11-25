import { useState } from 'react'
import { createPortal } from 'react-dom'
import { cancelDeviceTransfer, confirmDeviceTransfer } from '../../services/gstTallyApi'

export default function DeviceTransferDialog({ offer, onDone, onCancel }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const payload = { tally_serial: offer.tally_serial, device_name: offer.current_device_name }

  async function confirm(event) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const result = await confirmDeviceTransfer({ ...payload, password })
      setSuccess(result.message || 'Device Transfer Successful. This laptop is now activated.')
      setPassword('')
      setTimeout(onDone, 1200)
    } catch (err) { setError(err.message || 'Device transfer failed.') } finally { setBusy(false) }
  }
  async function cancel() {
    try { await cancelDeviceTransfer(payload) } catch { /* cancellation must never block the user */ }
    onCancel()
  }

  return createPortal(<div className="step3-verification-overlay">
    <form className="step3-verification device-transfer" role="dialog" aria-modal="true" aria-label="License Already Active on Another Device" onSubmit={confirm}>
      <header className="step3-header"><div><h2>License Already Active on Another Device</h2></div></header>
      {success ? <p role="status">{success}</p> : <>
        <p>Your GSTR2Tally license is currently active on another laptop.</p>
        <p>Would you like to transfer this license to your current laptop?</p>
        <p>After successful transfer, the previous laptop will lose access to GSTR2Tally.</p>
        <dl className="device-transfer-details">
          <dt>Customer</dt><dd>{offer.customer_name}</dd>
          <dt>Tally Serial Number</dt><dd>{offer.tally_serial}</dd>
          <dt>Previous Device</dt><dd>{offer.previous_device_name}</dd>
          <dt>Current Device</dt><dd>{offer.current_device_name}</dd>
          <dt>License Status</dt><dd>{offer.license_status}</dd>
        </dl>
        <label>Confirm your password to continue
          <input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required />
        </label>
        {error && <p role="alert" className="step3-error">{error}</p>}
        <div className="device-transfer-actions">
          <button type="submit" disabled={busy || !password}>{busy ? 'Transferring...' : 'OK – Transfer to This Laptop'}</button>
          <button type="button" disabled={busy} onClick={cancel}>Cancel</button>
        </div>
      </>}
    </form>
  </div>, document.body)
}
