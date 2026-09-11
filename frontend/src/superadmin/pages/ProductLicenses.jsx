import { useEffect, useState } from 'react'
import ConfirmModal from '../components/ConfirmModal.jsx'
import {
  changeProductLicenseTallySerial,
  extendProductLicense,
  getProductLicense,
  listProductLicenses,
  revokeLicensedDevice,
  revokeProductLicense,
  reactivateProductLicense,
  suspendProductLicense,
} from '../services/superadminApi.js'
import { ErrorCard, InfoGrid, LoadingCard, MiniTable, PageHeader, Status, fmtDate, fmtDateTime } from './pageUtils.jsx'

function daysLabel(value) {
  if (value === null || value === undefined) return 'Not Set'
  if (Number(value) < 0) return 'Expired'
  return String(value)
}

export default function ProductLicenses() {
  const [state, setState] = useState({ loading: true, rows: [], error: '' })
  const [selected, setSelected] = useState(null)
  const [modal, setModal] = useState(null)

  const load = () => {
    setState(s => ({ ...s, loading: true }))
    listProductLicenses()
      .then(data => setState({ loading: false, rows: data.results || data || [], error: '' }))
      .catch(error => setState({ loading: false, rows: [], error: error.message }))
  }

  const open = id => getProductLicense(id).then(setSelected)
  useEffect(load, [])

  if (state.loading) return <LoadingCard label="Loading licenses..." />
  if (state.error) return <ErrorCard message={state.error} />

  const license = selected?.license
  const devices = selected?.devices || []

  return (
    <>
      <PageHeader title="Licenses" subtitle="Tally serial is the primary license lock. Device ID is secondary security." />
      <section className="sa-card">
        <MiniTable rows={state.rows} columns={[
          { key: 'customer', label: 'Customer' },
          { key: 'company', label: 'Company' },
          { key: 'licensed_gstin', label: 'GSTIN' },
          { key: 'activation_key', label: 'Activation Key' },
          { key: 'licensed_tally_serial', label: 'Registered Tally Serial' },
          { key: 'plan', label: 'Plan' },
          { key: 'activated_at', label: 'Activated Date', render: row => row.activated_at ? fmtDate(row.activated_at) : 'Not Set' },
          { key: 'expiry_date', label: 'Expiry Date', render: row => row.expiry_date ? fmtDate(row.expiry_date) : 'Not Set' },
          { key: 'days_remaining', label: 'Days Remaining', render: row => daysLabel(row.days_remaining) },
          { key: 'device', label: 'Device' },
          { key: 'last_verified_at', label: 'Last Verified', render: row => fmtDateTime(row.last_verified_at) },
          { key: 'status', label: 'Status', render: row => <Status value={row.status} /> },
          { key: 'open', label: 'Actions', render: row => (
            <div className="sa-table-actions">
              <button className="sa-action-link" type="button" onClick={() => open(row.id)}>View</button>
              <button className="sa-action-link" type="button" onClick={() => { setSelected({ license: row, devices: row.devices || [] }); setModal('extend') }}>Extend</button>
              <button className="sa-action-link is-danger" type="button" onClick={() => { setSelected({ license: row, devices: row.devices || [] }); setModal(row.status === 'SUSPENDED' || row.status === 'REVOKED' ? 'reactivate' : 'suspend') }}>{row.status === 'SUSPENDED' || row.status === 'REVOKED' ? 'Reactivate' : 'Suspend'}</button>
            </div>
          ) },
        ]} />
      </section>

      {license && (
        <section className="sa-card">
          <div className="sa-section-head">
            <h2>{license.customer}</h2>
            <button className="sa-action-link" type="button" onClick={() => setSelected(null)}>Close</button>
          </div>
          <div className="sa-section-grid">
            <section>
              <h3>Customer Details</h3>
              <InfoGrid items={[
                { label: 'Company', value: license.company },
                { label: 'GSTIN', value: license.licensed_gstin },
                { label: 'Phone', value: license.phone },
                { label: 'Email', value: license.customer_email },
              ]} />
            </section>
            <section>
              <h3>License Details</h3>
              <InfoGrid items={[
                { label: 'Activation Key', value: license.activation_key },
                { label: 'Plan', value: license.plan },
                { label: 'Start Date', value: license.purchase_date ? fmtDate(license.purchase_date) : 'Not Set' },
                { label: 'Activation Date', value: license.activated_at ? fmtDate(license.activated_at) : 'Not Set' },
                { label: 'Expiry Date', value: license.expiry_date ? fmtDate(license.expiry_date) : 'Not Set' },
                { label: 'Days Remaining', value: daysLabel(license.days_remaining) },
                { label: 'Status', value: <Status value={license.status} /> },
              ]} />
            </section>
            <section>
              <h3>Tally Details</h3>
              <InfoGrid items={[
                { label: 'Registered Tally Serial', value: `${license.licensed_tally_serial} LOCKED` },
                { label: 'Edition', value: license.tally_edition },
                { label: 'TSS Status', value: license.tss_status },
                { label: 'License Administrator', value: license.license_administrator },
                { label: 'Allowed Devices', value: license.allowed_devices },
                { label: 'Last Verified', value: fmtDateTime(license.last_verified_at) },
              ]} />
            </section>
          </div>
          <div className="sa-actions-row">
            <button className="sa-button sa-button-inline" type="button" onClick={() => setModal('extend')}>Extend</button>
            <button className="sa-button sa-button-inline is-secondary" type="button" onClick={() => setModal('serial')}>Change Tally Serial</button>
            <button className="sa-button sa-button-inline is-secondary" type="button" onClick={() => setModal('suspend')}>Suspend</button>
            <button className="sa-button sa-button-inline is-secondary" type="button" onClick={() => setModal('reactivate')}>Reactivate</button>
            <button className="sa-button sa-button-inline is-danger" type="button" onClick={() => setModal('revoke')}>Revoke</button>
          </div>
          <h3>Devices</h3>
          <MiniTable rows={devices} columns={[
            { key: 'device_name', label: 'Device' },
            { key: 'device_fingerprint', label: 'Device ID' },
            { key: 'windows_version', label: 'Windows Version' },
            { key: 'app_version', label: 'App Version' },
            { key: 'first_seen', label: 'First Seen', render: row => fmtDateTime(row.first_seen) },
            { key: 'last_seen', label: 'Last Seen', render: row => fmtDateTime(row.last_seen) },
            { key: 'status', label: 'Status', render: row => <Status value={row.status} /> },
            { key: 'action', label: '', render: row => row.status === 'ACTIVE' ? <button className="sa-action-link" type="button" onClick={() => revokeLicensedDevice(license.id, row.id, 'Admin reset').then(open.bind(null, license.id))}>Reset Device</button> : null },
          ]} />
        </section>
      )}

      {modal && license && (
        <ConfirmModal
          title={modal === 'serial' ? 'Change Registered Tally Serial' : modal === 'extend' ? 'Extend License' : modal === 'revoke' ? 'Revoke License' : modal === 'reactivate' ? 'Reactivate License' : 'Suspend License'}
          description={modal === 'serial' ? `Current Serial: ${license.licensed_tally_serial}` : ''}
          danger={modal === 'revoke'}
          fields={[
            ...(modal === 'serial' ? [
              { key: 'new_serial', label: 'New Serial' },
            ] : []),
            ...(modal === 'extend' ? [{ key: 'months', label: 'Extend By Months', type: 'number', defaultValue: 12 }] : []),
            { key: 'reason', label: 'Reason', type: 'textarea' },
          ]}
          confirmLabel={modal === 'serial' ? 'Confirm Change' : 'Confirm'}
          onCancel={() => setModal(null)}
          onConfirm={async values => {
            if (modal === 'serial') await changeProductLicenseTallySerial(license.id, values.new_serial, values.reason)
            if (modal === 'extend') await extendProductLicense(license.id, { months: Number(values.months || 12), reason: values.reason })
            if (modal === 'suspend') await suspendProductLicense(license.id)
            if (modal === 'reactivate') await reactivateProductLicense(license.id)
            if (modal === 'revoke') await revokeProductLicense(license.id)
            setModal(null)
            await open(license.id)
            load()
          }}
        />
      )}
    </>
  )
}
