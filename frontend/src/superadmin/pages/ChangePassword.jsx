import { useState } from 'react'
import { changePassword } from '../services/superadminAuthApi.js'

export default function ChangePassword({ forced = false, onDone }) {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [state, setState] = useState({ loading: false, error: '', success: '' })

  const submit = async e => {
    e.preventDefault()
    setState({ loading: true, error: '', success: '' })
    try {
      await changePassword(form.current, form.next, form.confirm)
      setState({ loading: false, error: '', success: 'Password updated successfully.' })
      window.setTimeout(() => onDone?.(), 500)
    } catch (error) {
      setState({ loading: false, error: error.detail || error.message, success: '' })
    }
  }

  return (
    <div className="sa-auth-page">
      <form className="sa-auth-card" onSubmit={submit}>
        <div className="sa-auth-brand">
          <div className="sa-brand-name">GSTR 2 Tally</div>
          <h1>Change Password</h1>
        </div>
        {forced && <div className="sa-notice-banner">Change the default password before opening the dashboard.</div>}
        {state.error && <div className="sa-error-banner">{state.error}</div>}
        {state.success && <div className="sa-success-banner">{state.success}</div>}
        {[
          ['current', 'Current Password'],
          ['next', 'New Password'],
          ['confirm', 'Confirm New Password'],
        ].map(([key, label]) => (
          <div className="sa-field" key={key}>
            <label>{label}</label>
            <input className="sa-input" type="password" value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} required />
          </div>
        ))}
        <button className="sa-button" type="submit" disabled={state.loading}>{state.loading ? 'Updating...' : 'Update Password'}</button>
      </form>
    </div>
  )
}
