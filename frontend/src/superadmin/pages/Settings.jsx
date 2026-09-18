import { useEffect, useState } from 'react'
import { activateSandboxConfiguration, getSandboxConfiguration, getSettings, testSandboxConfiguration, updateSettings } from '../services/superadminApi.js'
import { ErrorCard, LoadingCard, PageHeader } from './pageUtils.jsx'

const isMaskedCredential = value => {
  const text = String(value || '').trim()
  return !text || (/^(.)\1{3,}$/.test(text) && ['•', '*', '·', '●'].includes(text[0])) || (text.startsWith('â') && text.includes('€¢'))
}

const credentialPayload = config => ({ ...config,
  api_key: isMaskedCredential(config.api_key) ? '' : config.api_key,
  api_secret: isMaskedCredential(config.api_secret) ? '' : config.api_secret,
})

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [sandbox, setSandbox] = useState({ provider: 'sandbox', environment: 'test', api_version: '1.0.0', api_key: '', api_secret: '' })
  const [sandboxState, setSandboxState] = useState({ testing: false, saving: false, tested: false, error: '', success: '' })
  const [state, setState] = useState({ loading: true, saving: false, error: '', success: '' })

  useEffect(() => {
    Promise.all([getSettings(), getSandboxConfiguration()]).then(([data, config]) => { setSettings(data); setSandbox(s => ({ ...s, ...config })); setState({ loading: false, saving: false, error: '', success: '' }) })
      .catch(error => setState({ loading: false, saving: false, error: error.message, success: '' }))
  }, [])

  const submit = async e => {
    e.preventDefault()
    setState(s => ({ ...s, saving: true, success: '', error: '' }))
    try {
      const updated = await updateSettings(settings)
      setSettings(updated)
      setState({ loading: false, saving: false, error: '', success: 'Settings updated successfully.' })
    } catch (error) {
      setState({ loading: false, saving: false, error: error.message, success: '' })
    }
  }

  const testSandbox = async () => {
    setSandboxState({ testing: true, saving: false, tested: false, error: '', success: '' })
    try { await testSandboxConfiguration(credentialPayload(sandbox)); setSandboxState({ testing: false, saving: false, tested: true, error: '', success: 'Connection successful. Credentials are valid and ready to activate.' }) }
    catch (error) { setSandboxState({ testing: false, saving: false, tested: false, error: error.message, success: '' }) }
  }

  const activateSandbox = async () => {
    setSandboxState({ testing: false, saving: true, tested: true, error: '', success: '' })
    try { const updated = await activateSandboxConfiguration(credentialPayload(sandbox)); setSandbox(s => ({ ...s, ...updated, api_key: '', api_secret: '' })); setSandboxState({ testing: false, saving: false, tested: false, error: '', success: 'Sandbox configuration saved and activated.' }) }
    catch (error) { setSandboxState({ testing: false, saving: false, tested: false, error: error.message, success: '' }) }
  }

  if (state.loading) return <LoadingCard />
  if (!settings && state.error) return <ErrorCard message={state.error} />

  return (
    <>
      <PageHeader eyebrow="System controls" title="Configuration" subtitle="Manage application-level system settings and external API credentials." />
      <section className="sa-card">
        {state.error && <div className="sa-error-banner">{state.error}</div>}
        {state.success && <div className="sa-success-banner">{state.success}</div>}
        <form className="sa-form-grid" onSubmit={submit}>
          {[
            ['application_name', 'Application Name', 'text'],
            ['support_email', 'Support Email', 'email'],
            ['support_phone', 'Support Phone', 'text'],
            ['default_validity_days', 'Default Validity Days', 'number'],
            ['expiry_warning_days', 'Expiry Warning Days', 'number'],
            ['current_app_version', 'Current EXE/App Version', 'text'],
          ].map(([key, label, type]) => (
            <div className="sa-field" key={key}>
              <label>{label}</label>
              <input className="sa-input" type={type} value={settings?.[key] ?? ''} onChange={e => setSettings(s => ({ ...s, [key]: e.target.value }))} />
            </div>
          ))}
          <button className="sa-button sa-button-inline" type="submit" disabled={state.saving}>{state.saving ? 'Saving...' : 'Save Settings'}</button>
        </form>
      </section>
      <section className="sa-card sa-sandbox-config-card">
        <div className="sa-section-heading"><div><h2>GST Party Lookup API Configuration</h2><p>Manage GSTIN party lookup provider credentials.</p></div></div>
        {sandboxState.error && <div className="sa-error-banner">{sandboxState.error}</div>}
        {sandboxState.success && <div className="sa-success-banner">{sandboxState.success}</div>}
        <fieldset className="sa-form-grid" style={{ border: 0, padding: 0, margin: 0 }} disabled={sandboxState.testing || sandboxState.saving}>
          <div className="sa-field"><label>Provider</label><select className="sa-input" value="sandbox" disabled><option value="sandbox">Sandbox</option></select></div>
          <div className="sa-field"><label>Environment</label><select className="sa-input" value={sandbox.environment} onChange={e => { setSandbox(s => ({ ...s, environment: e.target.value })); setSandboxState(s => ({ ...s, tested: false })) }}><option value="test">Test / Sandbox</option><option value="production">Production</option></select></div>
          <div className="sa-field sa-field-wide"><label>API Key</label><input className="sa-input" type="password" autoComplete="new-password" value={sandbox.api_key || ''} placeholder={sandbox.api_key_masked || 'Enter API Key'} onChange={e => { setSandbox(s => ({ ...s, api_key: e.target.value })); setSandboxState(s => ({ ...s, tested: false })) }} /></div>
          <div className="sa-field sa-field-wide"><label>Secret Key</label><input className="sa-input" type="password" autoComplete="new-password" value={sandbox.api_secret || ''} placeholder="Enter a new API Secret" onChange={e => { setSandbox(s => ({ ...s, api_secret: e.target.value })); setSandboxState(s => ({ ...s, tested: false })) }} /></div>
          <div className="sa-field"><label>API Version</label><input className="sa-input" value={sandbox.api_version || '1.0.0'} onChange={e => { setSandbox(s => ({ ...s, api_version: e.target.value })); setSandboxState(s => ({ ...s, tested: false })) }} /></div>
        </fieldset>
        <div className="sa-actions-row"><button className="sa-button sa-button-secondary" type="button" onClick={testSandbox} disabled={sandboxState.testing || sandboxState.saving}>{sandboxState.testing ? 'Testing...' : 'Test Connection'}</button><button className="sa-button" type="button" onClick={activateSandbox} disabled={!sandboxState.tested || sandboxState.testing || sandboxState.saving}>{sandboxState.saving ? 'Saving...' : 'Save Configuration'}</button></div>
        <div className="sa-sandbox-status"><h3>Active Configuration Status</h3><div className="sa-kv-grid">
          {[
            ['Provider', 'Sandbox'], ['Configuration', sandbox.configured ? 'Configured' : 'Not configured'],
            ['Connection', sandbox.connection_status || 'Unverified'], ['Credentials', sandbox.credentials_status || 'Unverified'],
            ['Last Verified', sandbox.last_verified_at ? new Date(sandbox.last_verified_at).toLocaleString() : 'Not verified'],
            ['Party Lookup', sandbox.lookup_ready ? 'Ready' : 'Temporarily unavailable'],
            ['Session Required', sandbox.session_required ? 'Yes' : 'No'], ['Session Active', sandbox.session_active ? 'Yes' : 'No'],
            ['Session Expired', sandbox.session_expired ? 'Yes' : 'No'], ['OTP Required', sandbox.otp_required ? 'Yes' : 'No'],
          ].map(([label, value]) => <div className="sa-kv-item" key={label}><span className="sa-kv-label">{label}</span><span className="sa-kv-value">{value}</span></div>)}
        </div>{sandbox.last_error && <p className="sa-error-banner">{sandbox.last_error}</p>}</div>
      </section>
    </>
  )
}
