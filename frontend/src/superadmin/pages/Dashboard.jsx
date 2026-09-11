import { useEffect, useState } from 'react'
import { getDashboard } from '../services/superadminApi.js'
import { ErrorCard, LoadingCard, MetricCard, MiniTable, PageHeader, Status, fmtDate, fmtNumber } from './pageUtils.jsx'

function daysLabel(value) {
  if (value === null || value === undefined) return 'Not Set'
  if (Number(value) < 0) return 'Expired'
  return `${fmtNumber(value)} Days`
}

export default function Dashboard({ onNavigate }) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })

  useEffect(() => {
    let cancelled = false
    getDashboard()
      .then(data => { if (!cancelled) setState({ loading: false, data, error: '' }) })
      .catch(error => { if (!cancelled) setState({ loading: false, data: null, error: error.message }) })
    return () => { cancelled = true }
  }, [])

  if (state.loading) return <LoadingCard label="Loading business overview..." />
  if (state.error) return <ErrorCard message={state.error} />

  const data = state.data || {}
  const customers = data.customers || {}
  const licenses = data.product_licenses || {}
  const upcoming = data.recent?.expiring_soon || []

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Customer, license expiry, and device request overview."
        actions={<button className="sa-button sa-button-inline" type="button" onClick={() => onNavigate('licenses')}>View All Licenses</button>}
      />

      <div className="sa-metric-grid is-five">
        <MetricCard label="Total Customers" value={fmtNumber(customers.total_customers)} tone="primary" />
        <MetricCard label="Active Licenses" value={fmtNumber(licenses.active)} tone="success" />
        <MetricCard label="Expiring Soon" value={fmtNumber(licenses.expiring)} tone="warning" />
        <MetricCard label="Expired" value={fmtNumber(licenses.expired)} tone="error" />
        <MetricCard label="Device Requests" value={fmtNumber(licenses.device_change_requests)} tone="primary" />
      </div>

      <section className="sa-card">
        <div className="sa-section-head">
          <h2>Upcoming Expiries</h2>
          <button type="button" className="sa-action-link" onClick={() => onNavigate('licenses')}>View All Licenses</button>
        </div>
        <MiniTable rows={upcoming} columns={[
          { key: 'customer', label: 'Customer' },
          { key: 'company', label: 'Company' },
          { key: 'tally_serial', label: 'Tally Serial' },
          { key: 'expiry_date', label: 'Expiry Date', render: row => row.expiry_date ? fmtDate(row.expiry_date) : 'Not Set' },
          { key: 'days_remaining', label: 'Days Left', render: row => daysLabel(row.days_remaining) },
          { key: 'status', label: 'Status', render: row => <Status value={row.status} /> },
        ]} />
      </section>
    </>
  )
}
