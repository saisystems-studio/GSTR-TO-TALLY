import { useEffect, useState } from 'react'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { approveDeviceRequest, listDeviceRequests, rejectDeviceRequest, replaceDeviceRequest } from '../services/superadminApi.js'
import { ErrorCard, LoadingCard, MiniTable, PageHeader, Status, fmtDateTime } from './pageUtils.jsx'

export default function DeviceRequests() {
  const [state, setState] = useState({ loading: true, rows: [], error: '' })
  const [modal, setModal] = useState(null)

  const load = () => {
    setState(s => ({ ...s, loading: true }))
    listDeviceRequests()
      .then(data => setState({ loading: false, rows: data.results || data || [], error: '' }))
      .catch(error => setState({ loading: false, rows: [], error: error.message }))
  }

  useEffect(load, [])

  if (state.loading) return <LoadingCard label="Loading device requests..." />
  if (state.error) return <ErrorCard message={state.error} />

  return (
    <>
      <PageHeader title="Device Requests" subtitle="Only same Tally serial requests are treated as device changes." />
      <section className="sa-card">
        <MiniTable rows={state.rows} columns={[
          { key: 'customer', label: 'Customer' },
          { key: 'activation_key', label: 'License' },
          { key: 'registered_tally_serial', label: 'Tally Serial' },
          { key: 'old_device_name', label: 'Current Device' },
          { key: 'requested_device_name', label: 'Requested Device' },
          { key: 'requested_at', label: 'Requested Date', render: row => fmtDateTime(row.requested_at) },
          { key: 'status', label: 'Status', render: row => <Status value={row.status} /> },
          { key: 'actions', label: '', render: row => row.status === 'PENDING' ? (
            <div className="sa-table-actions">
              <button className="sa-action-link" type="button" onClick={() => setModal({ row, action: 'approve' })}>Approve</button>
              <button className="sa-action-link" type="button" onClick={() => setModal({ row, action: 'replace' })}>Replace Existing Device</button>
              <button className="sa-action-link is-danger" type="button" onClick={() => setModal({ row, action: 'reject' })}>Reject</button>
            </div>
          ) : null },
        ]} />
      </section>

      {modal && (
        <ConfirmModal
          title={modal.action === 'approve' ? 'Approve New Device' : modal.action === 'replace' ? 'Replace Existing Device' : 'Reject Device Request'}
          danger={modal.action === 'reject'}
          fields={[{ key: 'reason', label: 'Reason', type: 'textarea' }]}
          confirmLabel="Confirm"
          onCancel={() => setModal(null)}
          onConfirm={async values => {
            if (modal.action === 'approve') await approveDeviceRequest(modal.row.id, values.reason)
            if (modal.action === 'replace') await replaceDeviceRequest(modal.row.id, values.reason)
            if (modal.action === 'reject') await rejectDeviceRequest(modal.row.id, values.reason)
            setModal(null)
            load()
          }}
        />
      )}
    </>
  )
}
