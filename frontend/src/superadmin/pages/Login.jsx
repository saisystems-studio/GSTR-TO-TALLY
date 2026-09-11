import { useState } from 'react'
import { loginSuperAdmin } from '../services/superadminAuthApi.js'

export default function Login({ onLogin, onForgotPassword }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async e => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await loginSuperAdmin(username.trim(), password)
      onLogin({ ...data.user, must_change_password: data.must_change_password })
    } catch (err) {
      setError(err.detail || err.message || 'Invalid username or password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="sa-auth-page">
      <form className="sa-auth-card" onSubmit={submit}>
        <div className="sa-auth-brand">
          <div className="sa-brand-name">GSTR 2 Tally</div>
          <h1>Super Admin Portal</h1>
        </div>
        {error && <div className="sa-error-banner">{error}</div>}
        <div className="sa-field">
          <label htmlFor="sa-username">Username</label>
          <input id="sa-username" className="sa-input" value={username} onChange={e => setUsername(e.target.value)} autoFocus required />
        </div>
        <div className="sa-field">
          <label htmlFor="sa-password">Password</label>
          <div className="sa-input-wrap">
            <input id="sa-password" className="sa-input" type={showPassword ? 'text' : 'password'}
                   value={password} onChange={e => setPassword(e.target.value)} required />
            <button type="button" className="sa-password-toggle" onClick={() => setShowPassword(s => !s)}>
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>
        <div className="sa-auth-links">
          <button type="button" className="sa-link" onClick={onForgotPassword}>Forgot Password?</button>
        </div>
        <button type="submit" className="sa-button" disabled={loading}>{loading ? 'Signing in...' : 'Sign In'}</button>
      </form>
    </div>
  )
}
