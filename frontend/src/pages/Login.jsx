import { useEffect, useRef, useState } from 'react'
import Toast from '../components/common/Toast.jsx'
import LoadingButton from '../components/common/LoadingButton.jsx'
import { clearSession, loginAccount, registerAccount, requestPasswordResetCode, resetPasswordWithCode } from '../services/authApi.js'
import { normalizeContactNumber, normalizeLoginIdentifier, validateLogin, validatePasswordReset, validateRegistration } from '../utils/authForms.js'
import * as nexus from '../services/nexusApi.js'
import '../styles/gst-tally.css'

// The Connector answers on loopback only to the configured web origin (local dev origins or the HTTPS site).
const CONNECTOR_HEALTH_URL = 'http://127.0.0.1:47831/health'
// Public static installer (no login needed); same path the import page uses.
const CONNECTOR_DOWNLOAD_URL = '/gstr2tally/downloads/GSTR2TallyConnectorSetup.exe'
async function probeConnector() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 1500)
  try {
    const response = await fetch(CONNECTOR_HEALTH_URL, { signal: controller.signal, cache: 'no-store' })
    const data = response.ok ? await response.json() : null
    return data?.app === 'GSTR2TallyConnector' ? data : null
  } catch { return null } finally { clearTimeout(timer) }
}
// When the normal (CORS) probe fails, an opaque no-cors request tells "something is listening but this origin is not
// allowed" (it succeeds, unreadable) apart from "nothing is listening" (it fails: connection refused).
async function connectorListening() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 1500)
  try { await fetch(CONNECTOR_HEALTH_URL, { mode: 'no-cors', signal: controller.signal, cache: 'no-store' }); return true } catch { return false } finally { clearTimeout(timer) }
}
// Connector Offline / TallyPrime Offline / Serial Failed are told apart by the Connector itself plus the server's
// verified read. The serial is only ever taken from the server (which got it from the Connector's Tally read).
let detectInFlight = null
// Pairing is attempted ONCE per enrollment per page load (success or failure): a 400 is never retried, and React
// StrictMode's double effects / poll ticks cannot re-send it. A 429 starts a cooldown during which NO request is sent.
const pairAttempted = new Set()
let throttledUntil = 0
const throttledResult = () => {
  const seconds = Math.max(1, Math.ceil((throttledUntil - Date.now()) / 1000))
  return { detected: false, code: 'throttled', retry_after: seconds, message: `Backend API throttled (HTTP 429). The server asked us to wait; you can retry in ${seconds} seconds.` }
}
const noteThrottle = error => { if (error?.status === 429) { throttledUntil = Date.now() + (error.retryAfter || 60) * 1000; return true } return false }
// One detection at a time: a Retry click or poll tick that overlaps a running one shares its result (no request pile-up).
function detectVerifiedSerial() {
  if (Date.now() < throttledUntil) return Promise.resolve(throttledResult())   // cooling down: send nothing
  if (!detectInFlight) detectInFlight = runDetection().finally(() => { detectInFlight = null })
  return detectInFlight
}
async function runDetection() {
  const health = await probeConnector()
  const blocked = !health && await connectorListening()
  let result = await detectWithHealth(health, blocked)
  if (result.detected && !health && !blocked) {
    // The server still holds a recent report, but nothing is listening any more: that serial is stale, never show it as verified.
    result = { detected: false, code: 'connector_offline', message: 'Connector Offline: nothing is listening on this PC. Start the GSTR2Tally Connector and retry.' }
  }
  // A server answer other than 'connector_offline' means a Connector is known to it even if this browser's origin cannot call loopback.
  const knownToServer = Boolean(result.detected || (result.code && !['connector_offline', 'api_error'].includes(result.code)))
  return { ...result, connector_running: Boolean(health?.running) || blocked || knownToServer }
}
async function detectWithHealth(health, blocked = false) {
  if (health?.enrollment?.enrollment_id && health.enrollment.proof) {
    // Running but not yet linked to this browser: link it (same pairing call as the #connector= link) so the server
    // can attribute the Connector's verified Tally serial to this registration page.
    const id = health.enrollment.enrollment_id
    if (!pairAttempted.has(id)) {
      pairAttempted.add(id)
      try { await nexus.pairConnector(id, health.enrollment.proof) } catch (error) { if (noteThrottle(error)) return throttledResult() }
    }
  }
  let result
  try { result = await nexus.detectTallySerial() } catch (error) {
    if (noteThrottle(error)) return throttledResult()
    return { detected: false, code: 'api_error', message: `Cannot reach the GSTR2Tally server: ${error.message || 'network error'}. Retry.` }
  }
  if (result.detected) return result
  if (!health && blocked) {
    return { ...result, code: 'browser_blocked', message: `Browser Connection Blocked: the Connector is running on this PC but does not allow this website address (${window.location.origin}). Open GSTR2Tally from its authorized address and retry.` }
  }
  if (!health) {
    return { ...result, code: 'connector_offline', message: 'Connector Offline: nothing is listening on this PC. Start the GSTR2Tally Connector (or install it) and retry.' }
  }
  // Health succeeded: never report the Connector as not running. Use what it actually read from TallyPrime.
  const local = health.tally
  if (local && !local.reachable) return { detected: false, code: 'tally_not_running', message: 'TallyPrime Not Connected. Open TallyPrime with a company and retry.' }
  if (local && local.reachable && !local.serial) return { detected: false, code: 'tally_license_unavailable', message: 'TallyPrime is connected but does not expose a license serial number (Educational Mode or not activated). Activate it and retry.' }
  if (local?.serial) return { detected: false, code: 'awaiting_server', tally_serial: local.serial, message: 'Serial read from TallyPrime by the Connector. Waiting for the server to verify it…' }
  return { ...result, code: 'connector_waiting', message: 'The Connector is running. Waiting for it to read TallyPrime…' }
}

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

  // Nexus mode (central licensing). When disabled every handler below keeps
  // the original local behaviour untouched.
  const [nexusOn, setNexusOn] = useState(false)
  const [tally, setTally] = useState({ detected: false, message: 'Checking TallyPrime…' })
  const [installStarted, setInstallStarted] = useState(false)
  const [pwChange, setPwChange] = useState({ ticket: '', current_password: '', new_password: '' })
  const nexusLoaded = useRef(false)
  useEffect(() => {
    if (nexusLoaded.current) return
    nexusLoaded.current = true
    nexus.getNexusConfig().then(config => setNexusOn(Boolean(config.enabled)))
  }, [])
  // The connector opens the app with #connector=<enrollment>.<proof>; bind this browser to it so the
  // verified Tally serial can be shown at registration (before any account exists).
  useEffect(() => {
    if (!nexusOn) return
    const match = /connector=([0-9a-f-]{36})\.([A-Za-z0-9_-]+)/i.exec(window.location.hash || '')
    if (match && !pairAttempted.has(match[1])) { pairAttempted.add(match[1]); nexus.pairConnector(match[1], match[2]).then(() => detectSerial()).catch(error => setTally({ detected: false, message: error.message || 'Connector pairing failed. Restart the connector and retry.' })) }
  }, [nexusOn])
  const [cooldown, setCooldown] = useState(0)
  useEffect(() => {
    if (tally.code !== 'throttled') { setCooldown(0); return undefined }
    setCooldown(tally.retry_after || 60)
    const timer = window.setInterval(() => setCooldown(value => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [tally])
  const detectSerial = () => {
    if (Date.now() < throttledUntil) { setTally(throttledResult()); return }
 setTally({ detected: false, message: 'Checking TallyPrime…' }); detectVerifiedSerial().then(setTally).catch(error => setTally({ detected: false, message: error.message || 'Unable to reach the server.' })) }
  useEffect(() => { if (nexusOn && mode === 'register') detectSerial() }, [nexusOn, mode])
  // While the registration page is open and Tally/the connector is not detected yet, quietly re-check every
  // 5 seconds so the serial fills in by itself as soon as TallyPrime/the connector comes online.
  useEffect(() => {
    if (!(nexusOn && mode === 'register') || !tally.detected) return undefined
    let stopped = false, misses = 0
    const timer = window.setInterval(async () => {
      if (stopped || document.hidden) return
      const health = await probeConnector()
      if (stopped) return
      if (health || (await connectorListening())) { misses = 0; return }
      if (++misses >= 2) detectVerifiedSerial().then(result => { if (!stopped) setTally(result) }).catch(() => {})
    }, 10000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [nexusOn, mode, tally.detected])
  useEffect(() => {
    if (!(nexusOn && mode === 'register') || tally.detected) return undefined
    let stopped = false, timer = 0, attempt = 0
    // Auto-recovery without hammering: a cheap loopback health check each tick (5s for the first minute, then 30s, and
    // paused while the tab is hidden). The server is only asked when the Connector actually answered.
    const tick = async () => {
      if (stopped) return
      if (!document.hidden) {
        const health = await probeConnector()
        if (stopped) return
        if (health?.tally?.serial) detectVerifiedSerial().then(result => { if (!stopped) setTally(result) }).catch(() => {})
      }
      attempt += 1
      timer = window.setTimeout(tick, attempt < 12 ? 5000 : 30000)
    }
    timer = window.setTimeout(tick, 5000)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [nexusOn, mode, tally.detected])
  // Legacy activation links (?activate=1) are retired: they open Sign In with the User ID prefilled.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (q.get('user_id') && q.get('activate') === '1') { setLogin(l => ({ ...l, identifier: q.get('user_id') })); setMode('login') }
  }, [])

  const notify = (message, kind = 'success') => setToast({ message, kind })
  useEffect(() => { if (notice) notify(notice, 'error') }, [notice])

  const switchMode = next => {
    setMode(next === 'activate' ? 'login' : next)  // legacy activation navigation lands on Sign In
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
    const nextErrors = nexusOn ? validateRegistration({ ...register, password: 'x'.repeat(8), confirm_password: 'x'.repeat(8) }) : validateRegistration(register)
    if (nexusOn && !tally.detected) return   // the inline status under the serial field already explains why
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setBusy(true)
    try {
      if (nexusOn) {
        const result = await nexus.nexusRegister({ name: register.full_name.trim(), email: register.email.trim(), contact_number: register.contact_number })
        setLogin(current => ({ ...current, identifier: result.user_id || tally.tally_serial, password: '' }))
        setRegister(initialRegister)
        const mailFailed = Boolean(result.welcome_email) && result.welcome_email !== 'submitted'
        const sentTo = result.email_sent_to ? ` (${result.email_sent_to})` : ''
        notify(mailFailed ? `Account created, but the credentials email${sentTo} could not be sent. Use Forgot Password on the sign-in page, or contact support.` : (result.message || `Registered. Check your email${sentTo} for your temporary password.`), mailFailed ? 'error' : 'success')
        switchMode('login')
        return
      }
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
      if (['connector_offline', 'tally_not_running', 'tally_license_unavailable'].includes(error.code)) {
        setTally({ detected: false, code: error.code, message: error.message })   // shown inline under the serial field
        detectSerial()
      } else {
        setErrors(error.errors || {})
        notify(error.message || 'Registration failed.', 'error')
      }
    } finally {
      setBusy(false)
    }
  }
  const submitLogin = async event => {
    event.preventDefault()
    const nextErrors = nexusOn ? ({ ...(login.identifier.trim() ? {} : { identifier: 'Enter your User ID.' }), ...(login.password ? {} : { password: 'Password is required.' }) }) : validateLogin(login)
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setBusy(true)
    try {
      let session
      if (nexusOn) {
        try { session = await nexus.nexusLogin(login.identifier.trim(), login.password) } catch (error) {
          if (error.code !== 'device_conflict') throw error
          // OK revokes the previous device through Nexus; Cancel leaves it active.
          if (!window.confirm(error.message)) { notify('Sign-in cancelled. Your other device remains active.', 'info'); return }
          session = await nexus.nexusLogin(login.identifier.trim(), login.password, true)
        }
      } else session = await loginAccount(normalizeLoginIdentifier(login.identifier), login.password)
      onLogin(session?.user || null)
    } catch (error) {
      if (error.code === 'password_change_required') { setPwChange({ ticket: error.ticket, current_password: login.password, new_password: '' }); switchMode('change') }
      else if (error.code === 'activation_required' || error.code === 'device_mismatch') notify('This device could not be verified. Open TallyPrime with the Connector running, make sure it is the Tally Serial Number you are signing in with, then retry.', 'error')
      else notify(error.message || 'Login failed.', 'error')
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
  const [resendIn, setResendIn] = useState(0)
  useEffect(() => {
    if (resendIn <= 0) return undefined
    const timer = setTimeout(() => setResendIn(value => value - 1), 1000)
    return () => clearTimeout(timer)
  }, [resendIn])
  // Success is announced only after the server confirms the request was accepted; any upstream/mail failure is
  // shown as an error and the user stays where they are. The wording is the same whether or not the account exists.
  const sendResetCode = async ({ resend = false } = {}) => {
    if (!forgotEmail.trim() || (!nexusOn && !forgotEmail.includes('@'))) {
      setErrors({ forgot_email: nexusOn ? 'Enter your registered email address or Tally Serial Number.' : 'Enter a valid email address.' })
      return
    }
    setErrors({})
    setBusy(true)
    try {
      let result = {}
      if (nexusOn) result = await nexus.nexusForgot(forgotEmail.trim()); else await requestPasswordResetCode(forgotEmail.trim().toLowerCase())
      notify(resend ? 'If the account exists, a new code has been emailed. Previous codes no longer work.' : 'If the account exists, a verification code has been emailed to the registered address. It is valid for 10 minutes.')
      setResendIn(Number(result.retry_after) || 60)
      setForgotStep('code')
    } catch (error) {
      if (error.code === 'resend_too_soon') { setResendIn(Number(error.retry_after) || 60); setForgotStep('code') }
      notify(error.message || 'Unable to send verification code. Please try again.', 'error')
    } finally {
      setBusy(false)
    }
  }
  const submitForgotEmail = event => { event.preventDefault(); return sendResetCode() }
  const submitResetPassword = async event => {
    event.preventDefault()
    const nextErrors = validatePasswordReset(resetForm)
    if (!String(resetForm.code || '').trim()) nextErrors.code = 'Enter the 6-digit code sent to your email.'
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setBusy(true)
    try {
      if (nexusOn) await nexus.nexusReset(resetForm.code.trim(), resetForm.new_password, forgotEmail.trim()); else await resetPasswordWithCode(forgotEmail.trim().toLowerCase(), resetForm.code.trim(), resetForm.new_password)
      notify('Password updated. You can now log in with your new password.')
      setLogin({ identifier: forgotEmail, password: '', remember: true })
      switchMode('login')
    } catch (error) {
      notify(error.message || 'Unable to reset password.', 'error')
    } finally {
      setBusy(false)
    }
  }
  const run = fn => async event => {
    event.preventDefault(); setBusy(true)
    try { await fn() } catch (error) { notify(error.message || 'Request failed.', 'error') } finally { setBusy(false) }
  }
  const submitChangePw = run(async () => {
    await nexus.nexusChangePassword(pwChange.ticket, pwChange.current_password, pwChange.new_password)
    notify('Password changed. Please sign in with your new password.'); setLogin(l => ({ ...l, password: '' })); switchMode('login')
  })
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
            <Field label={nexusOn ? 'User ID (Tally Serial Number)' : 'Email / Phone Number'} error={errors.identifier} icon={<MailIcon />}>
              <input name="identifier" placeholder={nexusOn ? 'Enter User ID' : 'Enter email address or phone number'} value={login.identifier} onChange={updateLogin} autoComplete="username" />
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
            {nexusOn && <Field label="Tally Serial Number (User ID)" error={!tally.detected ? tally.message : ''}>
              <input value={tally.detected ? (tally.tally_serial || '') : ''} readOnly placeholder="Detected automatically from TallyPrime" />
              {tally.detected && <p className="serial-verified" role="status"><span aria-hidden="true">✓</span> Tally Serial Verified</p>}
            </Field>}
            {nexusOn && !tally.detected && <p className="auth-switch serial-actions">
              <button type="button" onClick={detectSerial} disabled={cooldown > 0}>{cooldown > 0 ? `Retry in ${cooldown}s` : 'Retry'}</button>
              {/* Download is offered only when the Connector itself is not answering; a running Connector is never re-downloaded. */}
              {!tally.connector_running && <a className="serial-download" href={CONNECTOR_DOWNLOAD_URL} download onClick={() => setInstallStarted(true)}>Download &amp; Install Connector</a>}
              {!tally.connector_running && installStarted && <span className="serial-install-hint">Run the installer, then keep this page open — it will detect TallyPrime automatically.</span>}
            </p>}
            {!nexusOn && <PasswordField label="Password" name="password" value={register.password} visible={showPassword} onVisible={() => setShowPassword(value => !value)} onChange={updateRegister} error={errors.password} autoComplete="new-password" icon={<LockIcon />} />}
            {!nexusOn && <PasswordField label="Confirm Password" name="confirm_password" value={register.confirm_password} visible={showConfirm} onVisible={() => setShowConfirm(value => !value)} onChange={updateRegister} error={errors.confirm_password} autoComplete="new-password" icon={<LockIcon />} />}
            <LoadingButton className="wide-button auth-gradient-button" loading={busy} disabled={nexusOn && !tally.detected} type="submit">
              {busy ? <span>Creating account…</span> : <><span>Create Account</span><ArrowRightIcon /></>}
            </LoadingButton>
            <p className="auth-switch">Already have an account? <button type="button" onClick={() => switchMode('login')}>Login here</button></p>
          </form>}

          {mode === 'change' && <form className="auth-form auth-form-panel" onSubmit={submitChangePw} noValidate>
            <h2 className="auth-card-title">Set a New Password</h2>
            <p className="auth-card-subtitle">Replace your temporary password to continue.</p>
            <Field label="New Password"><input type="password" value={pwChange.new_password} onChange={e => setPwChange({ ...pwChange, new_password: e.target.value })} autoComplete="new-password" /></Field>
            <LoadingButton className="wide-button" loading={busy} type="submit">Change Password</LoadingButton>
          </form>}

          {mode === 'forgot' && <>
            <h2 className="auth-card-title">{HEADINGS.forgot.title}</h2>
            <p className="auth-card-subtitle">{forgotStep === 'email' ? HEADINGS.forgot.subtitle : 'Enter the code we emailed you and choose a new password.'}</p>
            {forgotStep === 'email' ? <form className="auth-form auth-form-panel" key="forgot-email" onSubmit={submitForgotEmail} noValidate>
              <Field label={nexusOn ? 'Registered Email or Tally Serial Number' : 'Registered Email'} error={errors.forgot_email}><input type={nexusOn ? 'text' : 'email'} value={forgotEmail} onChange={event => setForgotEmail(event.target.value)} autoComplete={nexusOn ? 'off' : 'email'} /></Field>
              <LoadingButton className="wide-button" loading={busy} type="submit">Send Verification Code</LoadingButton>
              <p className="auth-switch"><button type="button" onClick={() => switchMode('login')}>Back to Login</button></p>
            </form> : <form className="auth-form auth-form-panel" key="forgot-code" onSubmit={submitResetPassword} noValidate>
              <Field label="6-Digit Code" error={errors.code}><input name="code" inputMode="numeric" maxLength={6} value={resetForm.code} onChange={updateResetForm} autoComplete="one-time-code" /></Field>
              <PasswordField label="New Password" name="new_password" value={resetForm.new_password} visible={showResetPassword} onVisible={() => setShowResetPassword(value => !value)} onChange={updateResetForm} error={errors.new_password} autoComplete="new-password" />
              <PasswordField label="Confirm New Password" name="confirm_password" value={resetForm.confirm_password} visible={showResetPassword} onVisible={() => setShowResetPassword(value => !value)} onChange={updateResetForm} error={errors.confirm_password} autoComplete="new-password" />
              <LoadingButton className="wide-button" loading={busy} type="submit">Reset Password</LoadingButton>
              <p className="auth-switch">
                <button type="button" disabled={busy || resendIn > 0} onClick={() => sendResetCode({ resend: true })}>{resendIn > 0 ? `Resend Code (${resendIn}s)` : 'Resend Code'}</button> · <button type="button" onClick={() => switchMode('login')}>Back to Login</button>
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
