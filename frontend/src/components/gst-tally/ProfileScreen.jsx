import { useEffect, useState } from 'react'
import { changeMyPassword, getMyProfile, updateMyProfile } from '../../services/gstTallyApi'
import { formatDate } from '../../utils/date'
import { profileDeviceDetails } from '../../utils/profileDeviceDetails'

const TERMS = [
  'Use this application only on the authorized registered device.',
  'Keep your account and device credentials secure.',
  'The same successfully imported source file cannot be imported again as a duplicate.',
  'Company and GSTIN information must match the verified business records.',
  'Device changes may require administrator approval.',
]

const STATUS_META = {
  ACTIVE: { label: 'ACTIVE', tone: 'active' },
  EXPIRING_SOON: { label: 'EXPIRING SOON', tone: 'expiring-soon' },
  EXPIRED: { label: 'EXPIRED', tone: 'expired' },
  SUSPENDED: { label: 'SUSPENDED', tone: 'suspended' },
  NEEDS_SETUP: { label: 'NEEDS SETUP', tone: 'needs-setup' },
}

function daysLabel(value) {
  if (value === null || value === undefined) return 'Not Set'
  if (Number(value) < 0) return 'Expired'
  return `${value} Day${value === 1 ? '' : 's'}`
}

function fmtOrNotSet(value) {
  return value ? formatDate(value) : 'Not Set'
}

function StatusPill({ status }) {
  const meta = STATUS_META[status] || STATUS_META.NEEDS_SETUP
  return <span className={`profile-status-pill tone-${meta.tone}`}><span className="profile-status-dot" aria-hidden="true" />{meta.label}</span>
}

function VerifiedPill({ verified }) {
  return <span className={`profile-status-pill ${verified ? 'tone-active' : 'tone-needs-setup'}`}>
    <span className="profile-status-dot" aria-hidden="true" />{verified ? 'Verified' : 'Not Verified'}
  </span>
}

function Field({ label, value, fallback = 'Not Available' }) {
  return <div className="profile-field"><dt>{label}</dt><dd>{value || value === 0 ? value : fallback}</dd></div>
}

const icons = {
  profile: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 4-6 8-6s8 2 8 6" /></svg>,
  building: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="3" width="16" height="18" rx="1" /><path d="M9 8h1M14 8h1M9 12h1M14 12h1M9 16h1M14 16h1" /></svg>,
  document: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" /><path d="M9 12h6M9 16h6" /></svg>,
  key: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="8" cy="15" r="4" /><path d="M11 12l8-8M16 4l3 3M19 7l2 2" /></svg>,
  cube: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" /><path d="M4 7.5l8 4.5 8-4.5M12 21v-9" /></svg>,
  laptop: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="11" rx="1" /><path d="M2 19h20" /></svg>,
  calendar: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18" /></svg>,
  headset: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 13v-1a8 8 0 0 1 16 0v1" /><rect x="3" y="13" width="4" height="6" rx="1.5" /><rect x="17" y="13" width="4" height="6" rx="1.5" /><path d="M19 19v1a2 2 0 0 1-2 2h-3" /></svg>,
  edit: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" /></svg>,
  chevron: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6" /></svg>,
  lock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="10" width="16" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></svg>,
}

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function EditableField({ label, name, value, onChange, type = 'text', inputMode, maxLength }) {
  return <label className="profile-edit-field">
    <span>{label}</span>
    <input name={name} type={type} inputMode={inputMode} maxLength={maxLength} value={value} onChange={onChange} />
  </label>
}

function ProfileDetailsCard({ user, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ name: user.name, email: user.email, phone: user.phone })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const [changingPassword, setChangingPassword] = useState(false)
  const [pwForm, setPwForm] = useState({ current_password: '', new_password: '', confirm_password: '' })
  const [pwSaving, setPwSaving] = useState(false)
  const [pwError, setPwError] = useState('')
  const [pwSuccess, setPwSuccess] = useState('')

  const startEdit = () => {
    setForm({ name: user.name, email: user.email, phone: user.phone })
    setError('')
    setEditing(true)
  }
  const updateField = event => {
    const { name, value } = event.target
    setForm(current => ({ ...current, [name]: name === 'phone' ? value.replace(/\D/g, '').slice(0, 10) : value }))
  }
  const saveEdit = async event => {
    event.preventDefault()
    if (!form.name.trim()) return setError('Name is required.')
    if (!emailPattern.test(form.email.trim())) return setError('Enter a valid email address.')
    if (!/^\d{10}$/.test(form.phone)) return setError('Phone number must contain exactly 10 digits.')
    setError('')
    setSaving(true)
    try {
      const updated = await updateMyProfile({ name: form.name.trim(), email: form.email.trim(), phone: form.phone })
      onSaved(updated.user)
      setEditing(false)
    } catch (err) {
      setError(err.message || 'Unable to save profile changes.')
    } finally {
      setSaving(false)
    }
  }

  const updatePwField = event => {
    const { name, value } = event.target
    setPwForm(current => ({ ...current, [name]: value }))
  }
  const startChangePassword = () => {
    setPwForm({ current_password: '', new_password: '', confirm_password: '' })
    setPwError('')
    setPwSuccess('')
    setChangingPassword(true)
  }
  const savePassword = async event => {
    event.preventDefault()
    if (user.has_password && !pwForm.current_password) return setPwError('Enter your current password.')
    if (pwForm.new_password.length < 8) return setPwError('New password must be at least 8 characters.')
    if (pwForm.new_password !== pwForm.confirm_password) return setPwError('Passwords do not match.')
    setPwError('')
    setPwSaving(true)
    try {
      await changeMyPassword(pwForm.current_password, pwForm.new_password)
      setPwSuccess('Password changed successfully.')
      setPwForm({ current_password: '', new_password: '', confirm_password: '' })
      onSaved({ ...user, has_password: true })
    } catch (err) {
      setPwError(err.message || 'Unable to change password.')
    } finally {
      setPwSaving(false)
    }
  }

  return <div className="profile-card">
    <div className="profile-card-head">
      <h2><span className="profile-card-icon">{icons.profile}</span>Profile Details</h2>
      {!editing && <button type="button" className="profile-edit-btn" onClick={startEdit}><span className="profile-card-icon profile-edit-icon">{icons.edit}</span>Edit</button>}
    </div>

    {editing ? <form className="profile-edit-form" onSubmit={saveEdit}>
      <EditableField label="Name" name="name" value={form.name} onChange={updateField} />
      <EditableField label="Email" name="email" type="email" value={form.email} onChange={updateField} />
      <EditableField label="Phone" name="phone" inputMode="numeric" maxLength={10} value={form.phone} onChange={updateField} />
      {error && <div className="profile-form-error">{error}</div>}
      <div className="profile-form-actions">
        <button type="button" className="profile-cancel-btn" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
        <button type="submit" className="profile-save-btn" disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
      </div>
    </form> : <dl className="profile-fields">
      <Field label="Name" value={user.name} />
      <Field label="Email" value={user.email} />
      <Field label="Phone" value={user.phone} />
      <Field label="Role" value={user.role} />
    </dl>}

    <div className="profile-password-section">
      {!changingPassword ? <button type="button" className="profile-link-btn" onClick={startChangePassword}>
        <span className="profile-card-icon profile-edit-icon">{icons.lock}</span>Change Password
      </button> : <form className="profile-edit-form" onSubmit={savePassword}>
        {user.has_password && <EditableField label="Current Password" name="current_password" type="password" value={pwForm.current_password} onChange={updatePwField} />}
        <EditableField label="New Password" name="new_password" type="password" value={pwForm.new_password} onChange={updatePwField} />
        <EditableField label="Confirm New Password" name="confirm_password" type="password" value={pwForm.confirm_password} onChange={updatePwField} />
        {pwError && <div className="profile-form-error">{pwError}</div>}
        {pwSuccess && <div className="profile-form-success">{pwSuccess}</div>}
        <div className="profile-form-actions">
          <button type="button" className="profile-cancel-btn" onClick={() => setChangingPassword(false)} disabled={pwSaving}>Close</button>
          <button type="submit" className="profile-save-btn" disabled={pwSaving}>{pwSaving ? 'Saving...' : 'Save Password'}</button>
        </div>
      </form>}
    </div>
  </div>
}

function LicenseDetailsCard({ license }) {
  const status = license?.status || 'NEEDS_SETUP'
  return <div className="profile-card">
    <div className="profile-card-head">
      <h2><span className="profile-card-icon">{icons.key}</span>License Details</h2>
      <StatusPill status={status} />
    </div>
    <dl className="profile-fields">
      <Field label="Activation Key" value={license?.activation_key} />
      <Field label="Registered Tally Serial" value={license?.registered_tally_serial} />
      <Field label="Plan" value={license?.plan} />
      <Field label="Activated At" value={license?.activated_at ? formatDate(license.activated_at) : null} />
      <Field label="Start Date" value={fmtOrNotSet(license?.start_date)} />
      <Field label="Expiry Date" value={fmtOrNotSet(license?.expiry_date)} />
      <Field label="Days Remaining" value={daysLabel(license?.days_remaining)} />
    </dl>
  </div>
}

function SummaryBlock({ icon, label, value, sub }) {
  return <div className="profile-summary-block">
    <span className="profile-summary-icon" aria-hidden="true">{icon}</span>
    <div className="profile-summary-text">
      <div className="profile-summary-label">{label}</div>
      <div className="profile-summary-value">{value || 'Not Available'}</div>
      {sub}
    </div>
  </div>
}

export default function ProfileScreen() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    getMyProfile()
      .then(profile => {
        if (!alive) return
        setData(profile)
      })
      .catch(err => { if (alive) setError(err.message || 'Unable to load your profile right now.') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  if (loading) return <section className="screen profile-screen"><div className="profile-loading">Loading profile...</div></section>
  if (error) return <section className="screen profile-screen"><div className="profile-empty-state">{error}</div></section>
  if (!data) return null

  const { license } = data
  const details = profileDeviceDetails(data)
  const terms = data.terms || { last_updated: 'Not Available', items: TERMS }
  const licenseStatus = license?.status || 'NEEDS_SETUP'
  const isExpired = licenseStatus === 'EXPIRED'
  const isExpiringSoon = licenseStatus === 'EXPIRING_SOON'

  return <section className="screen profile-screen">
    {isExpired && <div className="profile-banner profile-banner-error">
      <div><strong>License Expired</strong> — this license expired on {fmtOrNotSet(license?.expiry_date)}. Tally import is currently disabled.</div>
    </div>}
    {!isExpired && isExpiringSoon && <div className="profile-banner profile-banner-warning">
      <div><strong>License Expiring Soon</strong> — {daysLabel(license?.days_remaining)} remaining. Renew before it expires to avoid interruption.</div>
    </div>}

    <div className="profile-grid">
      <ProfileDetailsCard user={data.user} onSaved={user => setData(current => ({ ...current, user }))} />
      <LicenseDetailsCard license={license} />

      <div className="profile-card">
        <div className="profile-card-head"><h2><span className="profile-card-icon">{icons.laptop}</span>System Configuration</h2></div>
        <dl className="profile-fields">
          {details.system.map(item => <div className="profile-field" key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
        </dl>
      </div>

      <div className="profile-card">
        <div className="profile-card-head"><h2><span className="profile-card-icon">{icons.laptop}</span>Device Authentication</h2></div>
        <dl className="profile-fields">
          {details.device.map(item => <div className="profile-field" key={item.label}><dt>{item.label}</dt><dd className={item.tone === 'verified' ? 'profile-verified-value' : ''}>{['First Seen', 'Last Seen'].includes(item.label) && item.value !== 'Not Available' ? formatDate(item.value) : item.value}</dd></div>)}
        </dl>
      </div>

      <div className="profile-card">
        <div className="profile-card-head">
          <h2><span className="profile-card-icon">{icons.document}</span>Terms &amp; Conditions</h2>
        </div>
        <div className="profile-terms">
          <div className="profile-terms-updated">Last Updated: {terms.last_updated || 'Not Available'}</div>
          <ol>{(terms.items || TERMS).map((term, index) => <li key={index}>{term}</li>)}</ol>
        </div>
      </div>
    </div>
  </section>
}
