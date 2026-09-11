import StatusPill from '../components/StatusPill.jsx'

export const fmtNumber = value => Number(value || 0).toLocaleString('en-IN')
export const fmtMoney = value => `Rs ${Number(value || 0).toLocaleString('en-IN')}`

export function fmtDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function fmtDateTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function fieldValue(value) {
  if (value === null || value === undefined || value === '') return '-'
  return value
}

export function PageHeader({ eyebrow, title, subtitle, actions }) {
  return (
    <div className="sa-page-header">
      <div>
        {eyebrow && <div className="sa-eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="sa-header-actions">{actions}</div>}
    </div>
  )
}

export function MetricCard({ label, value, tone, caption }) {
  return (
    <div className={`sa-metric ${tone ? `is-${tone}` : ''}`}>
      <div className="sa-metric-label">{label}</div>
      <div className={`sa-metric-value ${tone ? `is-${tone}` : ''}`}>{value}</div>
      {caption && <div className="sa-metric-caption">{caption}</div>}
    </div>
  )
}

export function InfoGrid({ items }) {
  return (
    <div className="sa-kv-grid">
      {items.map(item => (
        <div className="sa-kv-item" key={item.label}>
          <div className="sa-kv-label">{item.label}</div>
          <div className="sa-kv-value">{fieldValue(item.value)}</div>
        </div>
      ))}
    </div>
  )
}

export function MiniTable({ columns, rows, empty = 'No records yet.' }) {
  return (
    <div className="sa-table-wrap is-compact">
      <table className="sa-table">
        <thead>
          <tr>{columns.map(col => <th key={col.key}>{col.label}</th>)}</tr>
        </thead>
        <tbody>
          {(rows || []).map((row, index) => (
            <tr key={row.id ?? index}>
              {columns.map(col => <td key={col.key}>{col.render ? col.render(row) : fieldValue(row[col.key])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {(!rows || rows.length === 0) && <div className="sa-table-empty">{empty}</div>}
    </div>
  )
}

export function Status({ value }) {
  return <StatusPill value={value || 'UNKNOWN'} />
}

export function LoadingCard({ label = 'Loading records...' }) {
  return <div className="sa-card sa-state-card">{label}</div>
}

export function ErrorCard({ message }) {
  return <div className="sa-card sa-state-card is-error">{message || 'Unable to load this page.'}</div>
}
