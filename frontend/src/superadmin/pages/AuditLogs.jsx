import DataTable from '../components/DataTable.jsx'
import { listAuditLogs } from '../services/superadminApi.js'
import { PageHeader, Status, fmtDateTime, fieldValue } from './pageUtils.jsx'

function auditValue(value) {
  if (!value) return '-'
  if (typeof value !== 'object') return fieldValue(value)
  const first = Object.entries(value).find(([, entry]) => entry !== null && entry !== undefined && entry !== '')
  return first ? String(first[1]) : '-'
}

export default function AuditLogs() {
  return (
    <>
      <PageHeader eyebrow="Read-only history" title="Audit Logs" subtitle="Important license, device, Tally serial, GSTIN, and payment changes." />
      <section className="sa-card">
        <DataTable
          fetcher={listAuditLogs}
          filters={[
            { key: 'action', label: 'Action', options: ['LICENSE_CREATED', 'LICENSE_ACTIVATED', 'LICENSE_EXTENDED', 'LICENSE_EXPIRED', 'LICENSE_SUSPENDED', 'LICENSE_REACTIVATED', 'LICENSE_REVOKED', 'TALLY_SERIAL_CHANGED', 'TALLY_SERIAL_MISMATCH', 'DEVICE_REGISTERED', 'DEVICE_CHANGE_REQUESTED', 'DEVICE_REPLACED', 'DEVICE_REVOKED', 'EXPIRY_DATE_CHANGED', 'GSTIN_MISMATCH'].map(value => ({ value, label: value.replaceAll('_', ' ') })) },
          ]}
          columns={[
            { key: 'created_at', label: 'Date & Time', render: row => fmtDateTime(row.created_at) },
            { key: 'customer', label: 'Customer' },
            { key: 'action', label: 'Action' },
            { key: 'old_value', label: 'Old Value', render: row => auditValue(row.old_value) },
            { key: 'new_value', label: 'New Value', render: row => auditValue(row.new_value) },
            { key: 'changed_by', label: 'Changed By', render: row => fieldValue(row.changed_by || row.admin_user) },
            { key: 'status', label: 'Status', render: row => <Status value={row.status || 'SUCCESS'} /> },
          ]}
        />
      </section>
    </>
  )
}
