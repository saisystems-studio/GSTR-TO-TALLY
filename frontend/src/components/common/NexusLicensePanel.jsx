import { useEffect, useState } from 'react'
import { authenticatedFetch } from '../../services/authApi.js'

const date = value => (value ? new Date(value).toLocaleDateString('en-IN') : '—')
const PLAN = { TRIAL: 'Trial', ANNUAL: '1 Year', LIFETIME: 'Lifetime' }

// Read-only licence summary; every value comes from the Nexus-backed /api/nexus/license/ response.
export default function NexusLicensePanel({ user }) {
  const [lic, setLic] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    authenticatedFetch('/api/nexus/license/').then(async response => {
      const body = await response.json().catch(() => ({}))
      if (!alive) return
      if (response.ok) setLic(body); else setError(body.detail || 'License status unavailable.')
    }).catch(() => alive && setError('License status unavailable.'))
    return () => { alive = false }
  }, [])
  const plan = lic?.license_plan
  const rows = [
    ['User ID', lic?.tally_serial || user.tally_serial],
    ['License Plan', plan ? (PLAN[plan] || plan) : '—'],
    ['Activation Date', date(lic?.activated_at)],
    ['Expiry Date', plan === 'LIFETIME' ? 'Never' : date(lic?.expires_at)],
    ['License Status', lic ? String(lic.license_status || lic.state || '').toUpperCase() : '—'],
    ['Device Status', lic ? (lic.offline ? 'Offline (lease)' : lic.authorized ? 'Active on this device' : String(lic.state || '').replace(/_/g, ' ')) : '—'],
  ]
  return <div className="user-menu-license" aria-label="License information">
    {rows.map(([label, value]) => <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '3px 0', fontSize: 12 }}><span style={{ opacity: .7 }}>{label}</span><strong>{value}</strong></div>)}
    {error && <small role="alert">{error}</small>}
    <div className="user-menu-divider" />
  </div>
}
