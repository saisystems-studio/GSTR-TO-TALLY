import { useEffect, useState } from 'react'
import Toast from '../components/common/Toast.jsx'
import LoadingButton from '../components/common/LoadingButton.jsx'
import { clearSession, loginAccount, registerAccount, requestPasswordResetCode, resetPasswordWithCode } from '../services/authApi.js'
import { normalizeContactNumber, normalizeLoginIdentifier, validateLogin, validatePasswordReset, validateRegistration } from '../utils/authForms.js'
import '../styles/gst-tally.css'

const initialRegister = {
  full_name: '',
  email: '',
  contact_number: '',
  password: '',
  confirm_password: '',
}

// The Username field was removed from the UI (spec: register only asks for
// name/email/phone/password) but the backend still requires a unique
// username -- derive one invisibly from the email so the user never sees or
// types it. The short random suffix keeps collisions with an existing
// account effectively impossible without needing a visible retry flow.
function deriveUsername(email) {
  const local = String(email || '').split('@')[0].replace(/[^a-zA-Z0-9_.]/g, '').slice(0, 20) || 'user'
  return `${local}_${Math.random().toString(36).slice(2, 6)}`
}

const HEADINGS = {
  forgot: { title: 'Reset Your Password', subtitle: 'Enter your registered email to receive a verification code.' },
}

export default function Login({ onLogin, notice }) {
  // Only 'login' and 'register' render directly from the top level -- there
  // is no tab bar. 'forgot' is reached only via "Forgot Password?". There is
  // no product-activation step in this flow at all: registration goes
  // straight to Login, and Login only ever needs email/phone + password --
  // Tally/product activation happens later, from within the GST import
  // workflow (see services/product_license.py), never here.
  const [mode, setMode] = useState('login')
  const [register, setRegister] = useState(initialRegister)
  const [login, setLogin] = useState({ identifier: '', password: '', remember: true })
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [errors, setErrors] = useState({})
  const [toast, setToast] = useState(null)
  const [busy, setBusy] = useState(false)
  const [forgotStep, setForgotStep] = useState('email')
  const [forgotEmail, setForgotEmail] = useState('')
  const [resetForm, setResetForm] = useState({ code: '', new_password: '', confirm_password: '' })
  const [showResetPassword, setShowResetPassword] = useState(false)

  const notify = (message, kind = 'success') => setToast({ message, kind })
  useEffect(() => { if (notice) notify(notice, 'error') }, [notice])

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
        username: deriveUsername(register.email),
        email: register.email.trim(),
        phone_number: register.contact_number,
        full_name: register.full_name.trim(),
        password: register.password,
      })
      // Registration never signs the user in by itself -- account creation
      // and authentication are separate; an explicit login always follows.
      clearSession()
      setRegister(initialRegister)
      notify('Account created successfully. Please login to continue.')
      switchMode('login')
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
  const openForgotPassword = () => {
    setForgotStep('email')
    setForgotEmail(login.identifier.includes('@') ? login.identifier : '')
    setResetForm({ code: '', new_password: '', confirm_password: '' })
    switchMode('forgot')
  }
  const updateResetForm = event => {
    const { name, value } = event.target
    setResetForm(current => ({ ...current, [name]: value }))
  }
  const submitForgotEmail = async event => {
    event.preventDefault()
    if (!forgotEmail.trim() || !forgotEmail.includes('@')) {
      setErrors({ forgot_email: 'Enter a valid email address.' })
      return
    }
    setErrors({})
    setBusy(true)
    try {
      await requestPasswordResetCode(forgotEmail.trim().toLowerCase())
      notify('If this email is registered, a 6-digit verification code has been sent to it.')
      setForgotStep('code')
    } catch (error) {
      notify(error.message || 'Unable to send verification code.', 'error')
    } finally {
      setBusy(false)
    }
  }
  const submitResetPassword = async event => {
    event.preventDefault()
    const nextErrors = validatePasswordReset(resetForm)
    if (!String(resetForm.code || '').trim()) nextErrors.code = 'Enter the 6-digit code sent to your email.'
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setBusy(true)
    try {
      await resetPasswordWithCode(forgotEmail.trim().toLowerCase(), resetForm.code.trim(), resetForm.new_password)
      notify('Password updated. You can now log in with your new password.')
      setLogin({ identifier: forgotEmail, password: '', remember: true })
      switchMode('login')
    } catch (error) {
      notify(error.message || 'Unable to reset password.', 'error')
    } finally {
      setBusy(false)
    }
  }
  return <main className="auth-page">
    <header className="auth-topbar">
      <div className="auth-topbar-brand"><span className="brand-mark">GT</span><span>GSTR 2 Tally</span></div>
      <div className="auth-topbar-user"><span>Hi, User</span><ChevronDownIcon /></div>
    </header>
    <div className="auth-body">
      <section className="auth-left">
        {mode === 'register' ? <RegisterLeftPanel /> : <LoginLeftPanel />}
      </section>
      <section className="auth-right">
        <div className="auth-card">
          {mode === 'login' && <form className="auth-form auth-form-panel" key="login" onSubmit={submitLogin} noValidate>
            <div className="auth-card-title-row">
              <span className="auth-card-icon auth-card-icon-filled" aria-hidden="true"><UserIcon filled /></span>
              <div>
                <h2 className="auth-card-title">Welcome Back!</h2>
                <p className="auth-card-subtitle auth-card-subtitle-tight">Login to continue your GST import process</p>
              </div>
            </div>
            <Field label="Email / Phone Number" error={errors.identifier} icon={<MailIcon />}>
              <input name="identifier" placeholder="Enter email address or phone number" value={login.identifier} onChange={updateLogin} autoComplete="username" />
            </Field>
            <PasswordField label="Password" name="password" value={login.password} visible={showPassword} onVisible={() => setShowPassword(value => !value)} onChange={updateLogin} error={errors.password} autoComplete="current-password" icon={<LockIcon />} />
            <div className="auth-row">
              <label className="check-row"><input name="remember" type="checkbox" checked={login.remember} onChange={updateLogin} /> Remember Me</label>
              <button type="button" className="text-button" onClick={openForgotPassword}>Forgot Password?</button>
            </div>
            <LoadingButton className="wide-button auth-gradient-button" loading={busy} type="submit">
              {busy ? <span>Signing in…</span> : <><span>Login</span><ArrowRightIcon /></>}
            </LoadingButton>
            <div className="auth-divider" role="presentation"><span>OR</span></div>
            <p className="auth-switch">Don't have an account? <button type="button" onClick={() => switchMode('register')}>Register here</button></p>
          </form>}

          {mode === 'register' && <form className="auth-form auth-form-panel" key="register" onSubmit={submitRegister} noValidate>
            <div className="auth-card-title-row">
              <span className="auth-card-icon" aria-hidden="true"><UserIcon /></span>
              <div>
                <h2 className="auth-card-title">Create Your Account</h2>
                <p className="auth-card-subtitle auth-card-subtitle-tight">Fill in your details to get started.</p>
              </div>
            </div>
            <Field label="Full Name" error={errors.full_name} icon={<UserIcon />}><input name="full_name" placeholder="Enter full name" value={register.full_name} onChange={updateRegister} autoComplete="name" /></Field>
            <Field label="Email" error={errors.email} icon={<MailIcon />}><input name="email" type="email" placeholder="Enter email address" value={register.email} onChange={updateRegister} autoComplete="email" /></Field>
            <Field label="Contact Number" error={errors.contact_number || errors.phone_number} icon={<PhoneIcon />}><input name="contact_number" inputMode="numeric" placeholder="Enter 10 digit mobile number" maxLength={10} value={register.contact_number} onChange={updateRegister} autoComplete="tel" /></Field>
            <PasswordField label="Password" name="password" value={register.password} visible={showPassword} onVisible={() => setShowPassword(value => !value)} onChange={updateRegister} error={errors.password} autoComplete="new-password" icon={<LockIcon />} />
            <PasswordField label="Confirm Password" name="confirm_password" value={register.confirm_password} visible={showConfirm} onVisible={() => setShowConfirm(value => !value)} onChange={updateRegister} error={errors.confirm_password} autoComplete="new-password" icon={<LockIcon />} />
            <LoadingButton className="wide-button auth-gradient-button" loading={busy} type="submit">
              {busy ? <span>Creating account…</span> : <><span>Create Account</span><ArrowRightIcon /></>}
            </LoadingButton>
            <p className="auth-switch">Already have an account? <button type="button" onClick={() => switchMode('login')}>Login here</button></p>
          </form>}

          {mode === 'forgot' && <>
            <h2 className="auth-card-title">{HEADINGS.forgot.title}</h2>
            <p className="auth-card-subtitle">{forgotStep === 'email' ? HEADINGS.forgot.subtitle : 'Enter the code we emailed you and choose a new password.'}</p>
            {forgotStep === 'email' ? <form className="auth-form auth-form-panel" key="forgot-email" onSubmit={submitForgotEmail} noValidate>
              <Field label="Registered Email" error={errors.forgot_email}><input type="email" value={forgotEmail} onChange={event => setForgotEmail(event.target.value)} autoComplete="email" /></Field>
              <LoadingButton className="wide-button" loading={busy} type="submit">Send Verification Code</LoadingButton>
              <p className="auth-switch"><button type="button" onClick={() => switchMode('login')}>Back to Login</button></p>
            </form> : <form className="auth-form auth-form-panel" key="forgot-code" onSubmit={submitResetPassword} noValidate>
              <Field label="6-Digit Code" error={errors.code}><input name="code" inputMode="numeric" maxLength={6} value={resetForm.code} onChange={updateResetForm} autoComplete="one-time-code" /></Field>
              <PasswordField label="New Password" name="new_password" value={resetForm.new_password} visible={showResetPassword} onVisible={() => setShowResetPassword(value => !value)} onChange={updateResetForm} error={errors.new_password} autoComplete="new-password" />
              <PasswordField label="Confirm New Password" name="confirm_password" value={resetForm.confirm_password} visible={showResetPassword} onVisible={() => setShowResetPassword(value => !value)} onChange={updateResetForm} error={errors.confirm_password} autoComplete="new-password" />
              <LoadingButton className="wide-button" loading={busy} type="submit">Reset Password</LoadingButton>
              <p className="auth-switch">
                <button type="button" onClick={() => setForgotStep('email')}>Resend Code</button> · <button type="button" onClick={() => switchMode('login')}>Back to Login</button>
              </p>
            </form>}
          </>}

        </div>
      </section>
    </div>
    <Toast toast={toast} onDismiss={() => setToast(null)} />
  </main>
}

function FeatureItem({ icon, title, desc }) {
  return <div className="auth-feature-item">
    <span className="auth-feature-icon" aria-hidden="true">{icon}</span>
    <div className="auth-feature-text"><strong>{title}</strong><span>{desc}</span></div>
  </div>
}

const FEATURES = [
  { icon: <ShieldIcon />, title: 'Secure & Reliable', desc: 'Your data is always safe' },
  { icon: <BoltIcon />, title: 'Fast & Accurate', desc: 'Save time, reduce errors' },
  { icon: <ChartIcon />, title: 'Tally Integrated', desc: 'Seamless GST to Tally' },
]

function AuthHero({ copy, illustration, tagline, scriptTagline }) {
  return <div className="auth-login-hero">
    <div className="auth-login-hero-row">
      <div className="auth-login-hero-text">
        <span className="auth-accent-tick" aria-hidden="true" />
        <h1 className="auth-hero-title-left">Welcome to<span>GSTR 2 Tally</span></h1>
        <p className="auth-hero-copy-left">{copy}</p>
        <div className="auth-feature-list">
          {FEATURES.map(feature => <FeatureItem key={feature.title} {...feature} />)}
        </div>
      </div>
      <div className="auth-login-hero-illustration">
        {illustration}
        {scriptTagline && <p className="auth-script-tagline">{scriptTagline}</p>}
      </div>
    </div>
    <p className="auth-tagline"><span className="auth-accent-tick" aria-hidden="true" />{tagline}</p>
  </div>
}

function LoginLeftPanel() {
  return <AuthHero
    copy={<>Simplify your GST import journey<br />with Tally.</>}
    illustration={<SecureIdentityIllustration />}
    tagline="Your GST Data. In Sync with Tally."
    scriptTagline={<>Safe Data<br />Smarter Business</>}
  />
}

function RegisterLeftPanel() {
  return <AuthHero
    copy={<>Create your account and start your<br />GST import journey with Tally.</>}
    illustration={<TallyWorkspaceIllustration />}
    tagline="Powering Your Business with Tally."
  />
}

function UserIcon({ filled }) {
  if (filled) {
    return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="8.5" r="3.6" fill="currentColor" /><path d="M4.5 19.5c1.4-3.4 4.4-5.2 7.5-5.2s6.1 1.8 7.5 5.2" fill="currentColor" /></svg>
  }
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="8.5" r="3.6" stroke="currentColor" strokeWidth="1.6" /><path d="M4.5 19.5c1.4-3.4 4.4-5.2 7.5-5.2s6.1 1.8 7.5 5.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
}

function MailIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2.5" y="4.5" width="15" height="11" rx="2" stroke="currentColor" strokeWidth="1.4" /><path d="M3 5.5l7 5.5 7-5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function LockIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="4" y="9" width="12" height="8.5" rx="2" stroke="currentColor" strokeWidth="1.4" /><path d="M6.5 9V6.5a3.5 3.5 0 0 1 7 0V9" stroke="currentColor" strokeWidth="1.4" /></svg>
}

function PhoneIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6 2.5h2l1 3-1.5 1.5a9 9 0 0 0 4.5 4.5L13.5 10l3 1v2c0 1.1-.9 2-2 2A11.5 11.5 0 0 1 4 4.5c0-1.1.9-2 2-2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
}

function ArrowRightIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 10h12M11 5l5 5-5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function ChevronDownIcon() {
  return <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function ShieldIcon() {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6l7-3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>
}

function BoltIcon() {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>
}

function ChartIcon() {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 20V10M11 20V4M18 20v-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
}

// The required "secure identity" composition: a soft blurred cloud/blob
// behind, a dashed orbit ring, sparkles, a vertical identity/document card
// with a profile avatar, content lines and a "verified" checkmark badge, a
// gradient shield with a padlock overlapping its bottom-right corner,
// decorative leaves, and a grounding shadow underneath the whole group.
function SecureIdentityIllustration() {
  return <svg className="auth-illustration" viewBox="0 0 360 340" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="shieldGrad" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#5FC1E6" />
        <stop offset="1" stopColor="#2596BE" />
      </linearGradient>
      <filter id="softBlur" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="16" />
      </filter>
    </defs>

    <g filter="url(#softBlur)" opacity=".6">
      <ellipse cx="195" cy="140" rx="140" ry="108" fill="#DCEFFB" />
    </g>
    <ellipse cx="195" cy="270" rx="96" ry="12" fill="#0B2D4D" opacity=".08" />
    <circle cx="195" cy="150" r="118" fill="none" stroke="#BFE0F3" strokeWidth="1.6" strokeDasharray="5 7" />

    <g fill="#5FC1E6">
      <path d="M298 78l2.6 6.4 6.4 2.6-6.4 2.6-2.6 6.4-2.6-6.4-6.4-2.6 6.4-2.6Z" />
      <path d="M320 118l1.7 4 4 1.7-4 1.7-1.7 4-1.7-4-4-1.7 4-1.7Z" />
      <path d="M84 62l1.7 4 4 1.7-4 1.7-1.7 4-1.7-4-4-1.7 4-1.7Z" />
    </g>

    <g opacity=".92">
      <path d="M64 292c-4-36 6-62 23-62 4 13 2 26-4 36" fill="none" stroke="#2596BE" strokeWidth="3" strokeLinecap="round" />
      <path d="M45 303c10-4 23-2 31 6-8 9-23 8-31-6Z" fill="#2596BE" opacity=".85" />
      <path d="M51 279c10-6 23-6 31 0-6 10-21 12-31 0Z" fill="#4FB6E0" opacity=".85" />
      <path d="M57 253c9-5 20-4 27 2-5 9-19 10-27-2Z" fill="#2596BE" opacity=".6" />
    </g>

    <rect x="108" y="66" width="144" height="198" rx="18" fill="#fff" stroke="#D7EAF7" strokeWidth="2" />
    <rect x="156" y="52" width="54" height="22" rx="7" fill="#2596BE" />
    <circle cx="184" cy="112" r="20" fill="#2596BE" />
    <circle cx="184" cy="105" r="7.2" fill="#fff" />
    <path d="M171.5 124c2.8-7 6.6-9.8 12.5-9.8s9.7 2.8 12.5 9.8" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
    <rect x="130" y="150" width="100" height="7.5" rx="3.75" fill="#DCEFFB" />
    <rect x="130" y="170" width="78" height="7.5" rx="3.75" fill="#DCEFFB" />
    <rect x="130" y="190" width="90" height="7.5" rx="3.75" fill="#DCEFFB" />
    <rect x="130" y="210" width="66" height="7.5" rx="3.75" fill="#DCEFFB" />
    <rect x="130" y="230" width="84" height="7.5" rx="3.75" fill="#DCEFFB" />

    <g transform="translate(212 66)">
      <rect width="38" height="38" rx="10" fill="#fff" stroke="#D7EAF7" strokeWidth="2" />
      <circle cx="19" cy="19" r="13" fill="#2596BE" />
      <path d="M13 19l4 4 8-8" stroke="#fff" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </g>

    <g transform="translate(206 186)">
      <ellipse cx="52" cy="116" rx="46" ry="9" fill="#0B2D4D" opacity=".1" />
      <path d="M52 0 102 18v36c0 34-20 54-50 66-30-12-50-32-50-66V18Z" fill="url(#shieldGrad)" />
      <path d="M52 9 92 23v31c0 27-16 43-40 52-24-9-40-25-40-52V23Z" fill="#fff" opacity=".14" />
      <rect x="28" y="58" width="48" height="36" rx="8" fill="#fff" />
      <path d="M36 58v-11a16 16 0 0 1 32 0v11" stroke="#fff" strokeWidth="5" fill="none" />
      <circle cx="52" cy="73" r="5" fill="#0B2D4D" />
      <rect x="49.5" y="74" width="5" height="11" rx="2.5" fill="#0B2D4D" />
    </g>
  </svg>
}

// Register: a laptop running a GST dashboard, a floating "Tally" checklist
// card overlapping it, a cloud-upload badge above, and a gear -- reusing the
// same blur/gradient/leaves/dots vocabulary as the Login illustration so the
// two pages read as one consistent illustration style.
function TallyWorkspaceIllustration() {
  return <svg className="auth-illustration" viewBox="0 0 340 300" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="cloudGrad" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#4FB6E0" />
        <stop offset="1" stopColor="#2596BE" />
      </linearGradient>
      <filter id="softBlur2" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="14" />
      </filter>
    </defs>

    <g filter="url(#softBlur2)" opacity=".55">
      <ellipse cx="185" cy="150" rx="128" ry="98" fill="#DCEFFB" />
    </g>
    <circle cx="270" cy="70" r="5" fill="#BFE0F3" />
    <circle cx="295" cy="105" r="3" fill="#BFE0F3" />
    <circle cx="60" cy="90" r="4" fill="#BFE0F3" />

    <g opacity=".9">
      <path d="M52 268c-4-32 5-54 20-54 4 11 2 22-3 32" fill="none" stroke="#2596BE" strokeWidth="3" strokeLinecap="round" />
      <path d="M35 278c9-4 20-2 27 5-7 8-20 7-27-5Z" fill="#2596BE" opacity=".85" />
      <path d="M40 258c9-5 20-5 27 0-5 9-18 11-27 0Z" fill="#4FB6E0" opacity=".85" />
    </g>

    <ellipse cx="180" cy="252" rx="98" ry="9" fill="#0B2D4D" opacity=".08" />

    <rect x="88" y="128" width="176" height="112" rx="9" fill="#0B2D4D" />
    <rect x="96" y="136" width="160" height="88" rx="4" fill="#fff" />
    <rect x="150" y="240" width="16" height="10" fill="#0B2D4D" />
    <rect x="126" y="250" width="64" height="6" rx="3" fill="#0B2D4D" opacity=".7" />
    <rect x="104" y="144" width="34" height="16" rx="4" fill="#2596BE" />
    <rect x="106" y="168" width="10" height="34" fill="#DCEFFB" />
    <rect x="120" y="180" width="10" height="22" fill="#4FB6E0" />
    <rect x="134" y="160" width="10" height="42" fill="#2596BE" />
    <rect x="148" y="172" width="10" height="30" fill="#DCEFFB" />

    <g transform="translate(178 148)">
      <rect width="76" height="88" rx="8" fill="#fff" stroke="#D7EAF7" strokeWidth="2" />
      <text x="38" y="24" textAnchor="middle" fontSize="15" fontWeight="700" fill="#2596BE" fontFamily="inherit">Tally</text>
      <rect x="10" y="36" width="14" height="14" rx="4" fill="#2596BE" />
      <path d="M13.5 43l2.5 2.5 5-5.5" stroke="#fff" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="30" y="40" width="34" height="6" rx="3" fill="#DCEFFB" />
      <rect x="10" y="56" width="14" height="14" rx="4" fill="#2596BE" />
      <path d="M13.5 63l2.5 2.5 5-5.5" stroke="#fff" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="30" y="60" width="34" height="6" rx="3" fill="#DCEFFB" />
      <rect x="10" y="76" width="14" height="14" rx="4" fill="#2596BE" />
      <path d="M13.5 83l2.5 2.5 5-5.5" stroke="#fff" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="30" y="80" width="34" height="6" rx="3" fill="#DCEFFB" />
    </g>

    <g transform="translate(152 70)">
      <ellipse cx="30" cy="24" rx="30" ry="20" fill="url(#cloudGrad)" />
      <ellipse cx="12" cy="28" rx="16" ry="13" fill="url(#cloudGrad)" />
      <ellipse cx="50" cy="28" rx="16" ry="13" fill="url(#cloudGrad)" />
      <path d="M30 16v20M22 26l8-8 8 8" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </g>

    <g transform="translate(232 208)">
      <circle cx="24" cy="24" r="24" fill="#2596BE" />
      <circle cx="24" cy="24" r="9" fill="#fff" />
      {Array.from({ length: 8 }).map((_, index) => {
        const angle = (index * Math.PI) / 4
        const x1 = 24 + Math.cos(angle) * 16, y1 = 24 + Math.sin(angle) * 16
        const x2 = 24 + Math.cos(angle) * 22, y2 = 24 + Math.sin(angle) * 22
        return <line key={index} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#fff" strokeWidth="4" strokeLinecap="round" />
      })}
    </g>
  </svg>
}

function Field({ label, error, children, icon }) {
  return <label className="form-field">
    <span>{label}</span>
    {icon ? <div className="input-icon-field"><span className="input-icon" aria-hidden="true">{icon}</span>{children}</div> : children}
    {error && <small role="alert">{error}</small>}
  </label>
}

const EyeIcon = ({ open }) => open
  ? <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6Z" stroke="currentColor" strokeWidth="1.4" /><circle cx="10" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.4" /></svg>
  : <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6Z" stroke="currentColor" strokeWidth="1.4" /><circle cx="10" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.4" /><path d="M3 3l14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>

function PasswordField({ label, name, value, visible, onVisible, onChange, error, autoComplete, icon }) {
  return <Field label={label} error={error}>
    <div className="password-wrap">
      {icon && <span className="input-icon" aria-hidden="true">{icon}</span>}
      <input name={name} type={visible ? 'text' : 'password'} value={value} onChange={onChange} autoComplete={autoComplete} />
      <button type="button" aria-label={visible ? 'Hide password' : 'Show password'} onClick={onVisible}><EyeIcon open={visible} /></button>
    </div>
  </Field>
}
