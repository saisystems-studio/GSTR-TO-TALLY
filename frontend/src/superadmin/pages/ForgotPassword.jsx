import { useState } from 'react'
import { forgotPassword } from '../services/superadminAuthApi.js'

export default function ForgotPassword({ onBack }) {
  const [username, setUsername] = useState('')
  const [done, setDone] = useState(false)
  const [loading, setLoading] = useState(false)

  const submit = async e => {
    e.preventDefault()
    setLoading(true)
    try { await forgotPassword(username.trim()) } finally { setLoading(false); setDone(true) }
  }

  return (
    <div className="sa-auth-page">
      <div className="sa-auth-card">
        <div className="sa-auth-brand">
          <div className="sa-brand-name">GSTR 2 Tally</div>
          <h1>Forgot Password</h1>
        </div>
        {done ? (
          <>
            <div className="sa-success-banner">If a Super Admin account matches, reset instructions have been generated. Contact your system administrator for the reset link.</div>
            <button type="button" className="sa-button is-secondary" onClick={onBack}>Back to Sign In</button>
          </>
        ) : (
          <form onSubmit={submit}>
            <div className="sa-field">
              <label htmlFor="sa-forgot-username">Username</label>
              <input id="sa-forgot-username" className="sa-input" value={username} onChange={e => setUsername(e.target.value)} required autoFocus />
            </div>
            <button type="submit" className="sa-button" disabled={loading}>{loading ? 'Please wait...' : 'Send Reset Instructions'}</button>
            <button type="button" className="sa-link" style={{ marginTop: 14, display: 'block', textAlign: 'center' }} onClick={onBack}>Back to Sign In</button>
          </form>
        )}
      </div>
    </div>
  )
}
