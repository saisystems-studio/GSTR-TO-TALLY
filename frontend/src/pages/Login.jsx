import { useState } from 'react'
import Toast from '../components/common/Toast.jsx'
import LoadingButton from '../components/common/LoadingButton.jsx'
import { clearSession, loginAccount, registerAccount } from '../services/authApi.js'
import { normalizeContactNumber, normalizeLoginIdentifier, validateLogin, validateRegistration } from '../utils/authForms.js'
import '../styles/gst-tally.css'

const initialRegister = {
  full_name: '',
  email: '',
  contact_number: '',
  username: '',
  password: '',
  confirm_password: '',
}

export default function Login({ onLogin }) {
  const [mode, setMode] = useState('login')
  const [register, setRegister] = useState(initialRegister)
  const [login, setLogin] = useState({ identifier: '', password: '', remember: true })
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [errors, setErrors] = useState({})
  const [toast, setToast] = useState(null)
  const [busy, setBusy] = useState(false)

  const notify = (message, kind = 'success') => setToast({ message, kind })
  const switchMode = next => {
    setMode(next)
    setErrors({})
  }
  const updateRegister = event => {
    const { name, value } = event.target
    setRegister(current => ({ ...current, [name]: name === 'contact_number' ? normalizeContactNumber(value) : value }))
  }
  const updateLogin = event => {
    const { name, value, checked, type } = event.target
    setLogin(current => ({ ...current, [name]: type === 'checkbox' ? checked : value }))
  }
  const submitRegister = async event => {
    event.preventDefault()
    const nextErrors = validateRegistration(register)
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setBusy(true)
    try {
      await registerAccount({
        username: register.username.trim(),
        email: register.email.trim(),
        phone_number: register.contact_number,
        full_name: register.full_name.trim(),
        password: register.password,
      })
      clearSession()
      setRegister(initialRegister)
      setMode('login')
      notify('Registration Successful ✓\nPlease login to continue.')
    } catch (error) {
      setErrors(error.errors || {})
      notify(error.message || 'Registration failed.', 'error')
    } finally {
      setBusy(false)
    }
  }
  const submitLogin = async event => {
    event.preventDefault()
    const nextErrors = validateLogin(login)
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setBusy(true)
    try {
      const session = await loginAccount(normalizeLoginIdentifier(login.identifier), login.password)
      onLogin(session?.user || null)
    } catch (error) {
      notify(error.message || 'Login failed.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return <main className="auth-page">
    <section className="auth-hero">
      <h1>Welcome to<span>GSTR 2 Tally</span></h1>
      <p>Create your account and start your GST import journey with Tally.</p>
      <div className="auth-benefits">
        <span className="auth-benefit"><i>✓</i> Secure &amp; Reliable</span>
        <span className="auth-benefit"><i>✓</i> Fast &amp; Accurate</span>
        <span className="auth-benefit"><i>✓</i> Tally Integrated</span>
      </div>
      <AccountingIllustration />
    </section>
    <div className="auth-panel-wrap">
      <section className="auth-panel">
        <div className="auth-brand">
          <span className="brand-mark">GT</span>
          <div>
            <h1>{mode === 'login' ? 'Welcome Back!' : 'Create Your Account'}</h1>
            <p>{mode === 'login' ? 'Login to continue your GSTR to Tally import process' : 'Fill in your details to get started.'}</p>
          </div>
        </div>
        <div className="auth-mode-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => switchMode('login')}>Login</button>
          <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => switchMode('register')}>Register</button>
        </div>
        {mode === 'register' ? <form className="auth-form auth-form-panel" key="register" onSubmit={submitRegister} noValidate>
          <Field label="Full Name" error={errors.full_name}><input name="full_name" value={register.full_name} onChange={updateRegister} autoComplete="name" /></Field>
          <Field label="Email" error={errors.email}><input name="email" type="email" value={register.email} onChange={updateRegister} autoComplete="email" /></Field>
          <Field label="Contact Number" error={errors.contact_number || errors.phone_number}><input name="contact_number" inputMode="numeric" maxLength={10} value={register.contact_number} onChange={updateRegister} autoComplete="tel" /></Field>
          <Field label="Username" error={errors.username}><input name="username" value={register.username} onChange={updateRegister} autoComplete="username" /></Field>
          <PasswordField label="Password" name="password" value={register.password} visible={showPassword} onVisible={() => setShowPassword(value => !value)} onChange={updateRegister} error={errors.password} autoComplete="new-password" />
          <PasswordField label="Confirm Password" name="confirm_password" value={register.confirm_password} visible={showConfirm} onVisible={() => setShowConfirm(value => !value)} onChange={updateRegister} error={errors.confirm_password} autoComplete="new-password" />
          <LoadingButton className="wide-button" loading={busy} type="submit">Create Account</LoadingButton>
          <p className="auth-switch">Already have an account? <button type="button" onClick={() => switchMode('login')}>Login here</button></p>
        </form> : <form className="auth-form auth-form-panel" key="login" onSubmit={submitLogin} noValidate>
          <Field label="Email / Phone Number" error={errors.identifier}><input name="identifier" placeholder="Enter email or 10-digit mobile number" value={login.identifier} onChange={updateLogin} autoComplete="username" /></Field>
          <PasswordField label="Password" name="password" value={login.password} visible={showPassword} onVisible={() => setShowPassword(value => !value)} onChange={updateLogin} error={errors.password} autoComplete="current-password" />
          <div className="auth-row">
            <label className="check-row"><input name="remember" type="checkbox" checked={login.remember} onChange={updateLogin} /> Remember Me</label>
            <button type="button" className="text-button">Forgot Password?</button>
          </div>
          <LoadingButton className="wide-button" loading={busy} type="submit">{busy ? 'Signing in…' : 'Login'}</LoadingButton>
          <p className="auth-switch">Don't have an account? <button type="button" onClick={() => switchMode('register')}>Register here</button></p>
        </form>}
      </section>
    </div>
    <Toast toast={toast} onDismiss={() => setToast(null)} />
  </main>
}

function AccountingIllustration() {
  return <svg className="auth-illustration" viewBox="0 0 320 220" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect x="18" y="24" width="204" height="150" rx="10" fill="#fff" stroke="currentColor" strokeOpacity=".25" />
    <rect x="34" y="42" width="80" height="10" rx="3" fill="currentColor" fillOpacity=".55" />
    <rect x="34" y="64" width="172" height="1" fill="currentColor" fillOpacity=".18" />
    {[0, 1, 2, 3, 4].map(row => <g key={row}>
      <rect x="34" y={78 + row * 18} width="90" height="8" rx="2" fill="currentColor" fillOpacity=".18" />
      <rect x="150" y={78 + row * 18} width="56" height="8" rx="2" fill="currentColor" fillOpacity={row % 2 ? '.3' : '.5'} />
    </g>)}
    <rect x="150" y="130" width="14" height="14" rx="3" fill="#ffc928" />
    <path d="M154 137l3 3 6-6" stroke="#102a43" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="252" cy="128" r="58" fill="currentColor" fillOpacity=".08" />
    <rect x="222" y="100" width="60" height="56" rx="8" fill="#fff" stroke="currentColor" strokeOpacity=".3" />
    <path d="M234 128l12 12 22-24" stroke="#159447" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
}

function Field({ label, error, children }) {
  return <label className="form-field"><span>{label}</span>{children}{error && <small role="alert">{error}</small>}</label>
}

const EyeIcon = ({ open }) => open
  ? <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6Z" stroke="currentColor" strokeWidth="1.4" /><circle cx="10" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.4" /></svg>
  : <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6Z" stroke="currentColor" strokeWidth="1.4" /><circle cx="10" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.4" /><path d="M3 3l14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>

function PasswordField({ label, name, value, visible, onVisible, onChange, error, autoComplete }) {
  return <Field label={label} error={error}>
    <div className="password-wrap">
      <input name={name} type={visible ? 'text' : 'password'} value={value} onChange={onChange} autoComplete={autoComplete} />
      <button type="button" aria-label={visible ? 'Hide password' : 'Show password'} onClick={onVisible}><EyeIcon open={visible} /></button>
    </div>
  </Field>
}
