export const statusClass = value => String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '-')

export default function StatusBadge({ value }) {
  const show = value === null || value === undefined || value === '' ? '-' : String(value)
  return <span className={`status-badge ${statusClass(value)}`}>{show}</span>
}
