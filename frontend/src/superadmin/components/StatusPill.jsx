export default function StatusPill({ status, value }) {
  const actualStatus = status || value
  if (!actualStatus) return <span>-</span>
  const key = String(actualStatus).toLowerCase()
  const label = String(actualStatus).replace(/_/g, ' ').replace(/\w\S*/g, w => w[0].toUpperCase() + w.slice(1).toLowerCase())
  return <span className={`sa-pill is-${key}`}>{label}</span>
}
