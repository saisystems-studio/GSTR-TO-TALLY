import DataTable from '../components/DataTable.jsx'
import { listPayments } from '../services/superadminApi.js'
import { PageHeader, Status, fmtDate, fmtMoney } from './pageUtils.jsx'

export default function Payments({ onOpen }) {
  return (
    <>
      <PageHeader eyebrow="Billing" title="Payments" subtitle="Simple payment and renewal history." />
      <section className="sa-card">
        <DataTable
          fetcher={listPayments}
          filters={[{ key: 'status', label: 'Payment', options: ['PAID', 'PENDING', 'FAILED', 'REFUNDED', 'PARTIALLY_REFUNDED'].map(value => ({ value, label: value.replaceAll('_', ' ') })) }]}
          columns={[
            { key: 'company', label: 'Customer' },
            { key: 'plan_name', label: 'Plan' },
            { key: 'final_amount', label: 'Amount', render: row => fmtMoney(row.final_amount) },
            { key: 'payment_date', label: 'Payment Date', render: row => fmtDate(row.payment_date) },
            { key: 'period', label: 'Period' },
            { key: 'expiry_date', label: 'Expiry Date', render: row => row.expiry_date ? fmtDate(row.expiry_date) : 'Not Set' },
            { key: 'payment_status', label: 'Payment Status', render: row => <Status value={row.payment_status} /> },
            { key: 'invoice_number', label: 'Invoice', render: row => <button className="sa-row-title" type="button" onClick={() => onOpen(row.id)}>{row.invoice_number}</button> },
            { key: 'action', label: 'Actions', render: row => (
              <div className="sa-table-actions">
                <button className="sa-action-link" type="button" onClick={() => onOpen(row.id)}>View</button>
                <button className="sa-action-link" type="button" onClick={() => onOpen(row.id)}>Invoice</button>
              </div>
            ) },
          ]}
        />
      </section>
    </>
  )
}
