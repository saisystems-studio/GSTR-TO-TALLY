import { useState } from 'react'

/** Generic confirm-with-reason dialog (spec section 74) -- reused for
 * Suspend/Reactivate/Change-Limit/Change-Plan/Renew/Reset-Device/etc. */
export default function ConfirmModal({ title, description, fields = [], confirmLabel = 'Confirm', danger = false, onConfirm, onCancel }) {
  const [values, setValues] = useState(() => Object.fromEntries(fields.map(f => [f.key, f.defaultValue ?? ''])))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setSubmitting(true)
    setError('')
    try {
      await onConfirm(values)
    } catch (err) {
      setError(err.message || 'Action failed.')
      setSubmitting(false)
    }
  }

  return (
    <div className="sa-modal-backdrop" onClick={onCancel}>
      <div className="sa-modal" onClick={e => e.stopPropagation()}>
        <h3>{title}</h3>
        {description && <p style={{ marginTop: -8, marginBottom: 16, color: 'var(--sa-text-secondary)', fontSize: 13.5 }}>{description}</p>}
        {error && <div className="sa-error-banner">{error}</div>}
        {fields.map(field => (
          <div className="sa-field" key={field.key}>
            <label>{field.label}</label>
            {field.type === 'select' ? (
              <select className="sa-input" value={values[field.key]} onChange={e => setValues(v => ({ ...v, [field.key]: e.target.value }))}>
                {field.options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
              </select>
            ) : field.type === 'textarea' ? (
              <textarea className="sa-input" rows={3} value={values[field.key]} onChange={e => setValues(v => ({ ...v, [field.key]: e.target.value }))} />
            ) : (
              <input className="sa-input" type={field.type || 'text'} value={values[field.key]} onChange={e => setValues(v => ({ ...v, [field.key]: e.target.value }))} />
            )}
          </div>
        ))}
        <div className="sa-modal-actions">
          <button className="sa-button is-ghost" style={{ width: 'auto' }} onClick={onCancel} disabled={submitting}>Cancel</button>
          <button className={`sa-button ${danger ? 'is-danger' : ''}`} style={{ width: 'auto' }} onClick={submit} disabled={submitting}>
            {submitting ? 'Please wait...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
