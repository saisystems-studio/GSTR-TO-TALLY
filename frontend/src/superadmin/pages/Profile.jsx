import { useState } from 'react'
import { changePassword, updateProfile } from '../services/superadminAuthApi.js'
import { InfoGrid, PageHeader, fmtDateTime } from './pageUtils.jsx'

export default function Profile({ profile, onUpdated }) {
  const [draft, setDraft] = useState(profile || {})
  const [state, setState] = useState({ saving: false, error: '', success: '' })
  const [passwordForm, setPasswordForm] = useState({ current: '', next: '', confirm: '' })
  const [passwordState, setPasswordState] = useState({ saving: false, error: '', success: '' })

  const submit = async e => {
    e.preventDefault()
    setState({ saving: true, error: '', success: '' })
    try {
      const updated = await updateProfile({ display_name: draft.display_name || '', email: draft.email || '', phone: draft.phone || '' })
      onUpdated(updated)
      setDraft(updated)
      setState({ saving: false, error: '', success: 'Profile updated successfully.' })
    } catch (error) {
      setState({ saving: false, error: error.message, success: '' })
    }
  }

  const submitPassword = async e => {
    e.preventDefault()
    setPasswordState({ saving: true, error: '', success: '' })
    try {
      await changePassword(passwordForm.current, passwordForm.next, passwordForm.confirm)
      setPasswordForm({ current: '', next: '', confirm: '' })
      onUpdated({ ...profile, must_change_password: false, password_changed_at: new Date().toISOString() })
      setPasswordState({ saving: false, error: '', success: 'Password updated successfully.' })
    } catch (error) {
      setPasswordState({ saving: false, error: error.detail || error.message, success: '' })
    }
  }

  return (
    <>
      <PageHeader eyebrow="Admin identity" title="Profile" subtitle="Manage your visible Super Admin contact information." />
      <div className="sa-section-grid">
        <section className="sa-card">
          <h2>Profile</h2>
          {state.error && <div className="sa-error-banner">{state.error}</div>}
          {state.success && <div className="sa-success-banner">{state.success}</div>}
          <form onSubmit={submit}>
            {[
              ['display_name', 'Display Name', 'text'],
              ['email', 'Email', 'email'],
              ['phone', 'Phone', 'text'],
            ].map(([key, label, type]) => (
              <div className="sa-field" key={key}>
                <label>{label}</label>
                <input className="sa-input" type={type} value={draft[key] || ''} onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))} />
              </div>
            ))}
            <button className="sa-button sa-button-inline" type="submit" disabled={state.saving}>{state.saving ? 'Saving...' : 'Save Profile'}</button>
          </form>
        </section>
        <section className="sa-card">
          <h2>Account</h2>
          <InfoGrid items={[
            { label: 'Name', value: profile?.name || profile?.display_name },
            { label: 'Role', value: profile?.role },
            { label: 'Account Status', value: profile?.account_status },
            { label: 'Created', value: fmtDateTime(profile?.account_created) },
            { label: 'Last Login', value: fmtDateTime(profile?.last_login) },
            { label: 'Password Changed', value: fmtDateTime(profile?.password_changed_at) },
          ]} />
        </section>
        <section className="sa-card">
          <h2>Change Password</h2>
          {passwordState.error && <div className="sa-error-banner">{passwordState.error}</div>}
          {passwordState.success && <div className="sa-success-banner">{passwordState.success}</div>}
          <form onSubmit={submitPassword}>
            {[
              ['current', 'Current Password'],
              ['next', 'New Password'],
              ['confirm', 'Confirm New Password'],
            ].map(([key, label]) => (
              <div className="sa-field" key={key}>
                <label>{label}</label>
                <input className="sa-input" type="password" value={passwordForm[key]} onChange={e => setPasswordForm(f => ({ ...f, [key]: e.target.value }))} required />
              </div>
            ))}
            <button className="sa-button sa-button-inline" type="submit" disabled={passwordState.saving}>{passwordState.saving ? 'Updating...' : 'Update Password'}</button>
          </form>
        </section>
      </div>
    </>
  )
}
