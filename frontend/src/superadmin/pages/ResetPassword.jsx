import { useMemo, useState } from 'react'
import { resetPassword } from '../services/superadminAuthApi.js'

export default function ResetPassword({ onDone }) {
  const token = useMemo(() => new URLSearchParams(window.location.search).get('token') || '', [])
  const [form, setForm] = useState({ next: '', confirm: '' })
  const [state, setState] = useState({ loading: false, error: '', success: '' })

  const submit = async e => {
    e.preventDefault()
    setState({ loading: true, error: '', success: '' })
    try {
      await resetPassword(token, form.next, form.confirm)
      setState({ loading: false, error: '', success: 'Password updated successfully.' })
      window.setTimeout(() => onDone?.(), 700)
    } catch (error) {
      setState({ loading: false, error: error.detail || error.message, success: '' })
    }
  }

  return (
    <div className="sa-auth-page">
      <form className="sa-auth-card" onSubmit={submit}>
        <div className="sa-auth-brand">
          <div className="sa-brand-name">GSTR 2 Tally</div>
          <h1>Reset Password</h1>
        </div>
        {!token && <div className="sa-error-banner">Reset token is missing from this link.</div>}
        {state.error && <div className="sa-error-banner">{state.error}</div>}
        {state.success && <div className="sa-success-banner">{state.success}</div>}
        <div className="sa-field">
          <label>New Password</label>
          <input className="sa-input" type="password" value={form.next} onChange={e => setForm(f => ({ ...f, next: e.target.value }))} required />
        </div>
        <div className="sa-field">
          <label>Confirm New Password</label>
          <input className="sa-input" type="password" value={form.confirm} onChange={e => setForm(f => ({ ...f, confirm: e.target.value }))} required />
        </div>
        <button className="sa-button" type="submit" disabled={!token || state.loading}>{state.loading ? 'Updating...' : 'Reset Password'}</button>
      </form>
    </div>
  )
}
