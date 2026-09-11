import DataTable from '../components/DataTable.jsx'
import { listCustomers } from '../services/superadminApi.js'
import { PageHeader, Status, fmtDate, fmtDateTime, fieldValue } from './pageUtils.jsx'

export default function Customers({ onOpen }) {
  return (
    <>
      <PageHeader eyebrow="Accounts" title="Customers" subtitle="Who purchased and uses GSTR 2 Tally." />
      <section className="sa-card">
        <DataTable
          fetcher={listCustomers}
          filters={[
            { key: 'status', label: 'Status', options: ['TRIAL', 'ACTIVE', 'EXPIRING_SOON', 'EXPIRED', 'SUSPENDED'].map(value => ({ value, label: value.replaceAll('_', ' ') })) },
          ]}
          columns={[
            { key: 'username', label: 'Customer', render: row => <button className="sa-row-title" type="button" onClick={() => onOpen(row.customer_id)}>{fieldValue(row.username)}</button> },
            { key: 'company', label: 'Company' },
            { key: 'gstin', label: 'GSTIN' },
            { key: 'phone', label: 'Phone' },
            { key: 'email', label: 'Email' },
            { key: 'tally_serial', label: 'Tally Serial' },
            { key: 'plan', label: 'Plan' },
            { key: 'expiry_date', label: 'Expiry Date', render: row => row.expiry_date ? fmtDate(row.expiry_date) : 'Not Set' },
            { key: 'license_status', label: 'License Status', render: row => <Status value={row.license_status} /> },
            { key: 'last_active', label: 'Last Seen', render: row => fmtDateTime(row.last_active) },
            { key: 'action', label: 'Action', render: row => <button className="sa-action-link" type="button" onClick={() => onOpen(row.customer_id)}>View Customer</button> },
          ]}
        />
      </section>
    </>
  )
}
