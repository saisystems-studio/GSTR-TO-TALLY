import { useEffect, useState } from 'react'
import { getCustomer } from '../services/superadminApi.js'
import { ErrorCard, LoadingCard, Status, fmtDate, fmtDateTime } from './pageUtils.jsx'

function missingCustomerValue(value) {
  if (value === null || value === undefined || value === '') return 'Not Available'
  return value
}

function missingLicenseValue(value) {
  if (value === null || value === undefined || value === '') return 'Not Set'
  return value
}

function deviceNameValue(value) {
  if (value === null || value === undefined || value === '') return 'Not Registered'
  return value
}

function deviceFieldValue(value) {
  if (value === null || value === undefined || value === '') return 'Not Available'
  return value
}

function daysLabel(value) {
  if (value === null || value === undefined) return 'Not Set'
  if (Number(value) < 0) return 'Expired'
  return `${value} Days`
}

function initialsFrom(name) {
  const words = String(name || 'Customer').trim().split(/\s+/).filter(Boolean)
  return words.slice(0, 2).map(word => word[0]).join('').toUpperCase() || 'CU'
}

function SectionTitle({ icon, children }) {
  return (
    <h2 className="sa-customer-section-title">
      <span className="sa-customer-section-icon">{icon}</span>
      {children}
    </h2>
  )
}

function DetailField({ label, value, highlight = false, children }) {
  return (
    <div className={`sa-customer-field ${highlight ? 'is-highlight' : ''}`}>
      <div className="sa-customer-field-label">{label}</div>
      <div className="sa-customer-field-value">{children || value}</div>
    </div>
  )
}

export default function CustomerDetail({ userId, onBack }) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })

  const load = () => {
    setState(s => ({ ...s, loading: true }))
    getCustomer(userId)
      .then(data => setState({ loading: false, data, error: '' }))
      .catch(error => setState({ loading: false, data: null, error: error.message }))
  }

  useEffect(load, [userId])

  if (state.loading) return <LoadingCard />
  if (state.error) return <ErrorCard message={state.error} />

  const data = state.data || {}
  const customer = data.customer || {}
  const profile = customer.profile || {}
  const license = data.license_summary || {}
  const device = data.device_summary || {}
  const customerName = missingCustomerValue(customer.name || customer.username)
  const companyName = missingCustomerValue(profile.business_name || license.company)
  const gstin = missingCustomerValue(license.licensed_gstin)
  const phone = missingCustomerValue(customer.phone)
  const email = missingCustomerValue(customer.email)
  const licenseStatus = license.status || 'NEEDS_SETUP'
  const isDeviceRegistered = Boolean(device.device_name || device.device_fingerprint)

  return (
    <div className="sa-customer-detail-page">
      <button type="button" className="sa-back-link" onClick={onBack}>&larr; Back to Customers</button>

      <section className="sa-card sa-customer-profile-card">
        <div className="sa-customer-profile-main">
          <div className="sa-customer-avatar">{initialsFrom(customerName)}</div>
          <div className="sa-customer-profile-copy">
            <h1>{customerName}</h1>
            <p>{email} <span aria-hidden="true">•</span> {phone}</p>
            <div className="sa-customer-profile-meta">
              <span>Company: {companyName}</span>
              <span>GSTIN: {gstin}</span>
            </div>
          </div>
        </div>
        <Status value={licenseStatus} />
      </section>

      <div className="sa-customer-detail-grid">
        <section className="sa-card sa-customer-info-card">
          <SectionTitle icon="CU">Customer Details</SectionTitle>
          <div className="sa-customer-field-stack">
            <DetailField label="Customer Name" value={customerName} />
            <DetailField label="Company Name" value={companyName} />
            <DetailField label="GSTIN" value={gstin} />
            <DetailField label="Phone" value={phone} />
            <DetailField label="Email" value={email} />
          </div>
        </section>

        <section className="sa-card sa-license-summary-card">
          <SectionTitle icon="LC">License Summary</SectionTitle>
          <div className="sa-license-grid">
            <DetailField label="Activation Key" value={missingLicenseValue(license.activation_key)} />
            <DetailField label="Plan" value={missingLicenseValue(license.plan)} />
            <DetailField label="Registered Tally Serial" value={missingLicenseValue(license.licensed_tally_serial)} />
            <DetailField label="Status">
              <Status value={licenseStatus} />
            </DetailField>
            <DetailField label="Purchase Date" value={license.purchase_date ? fmtDate(license.purchase_date) : 'Not Set'} />
            <DetailField label="Activation Date" value={license.activated_at ? fmtDate(license.activated_at) : 'Not Set'} />
            <DetailField label="Expiry Date" value={license.expiry_date ? fmtDate(license.expiry_date) : 'Not Set'} highlight />
            <DetailField label="Days Remaining" value={daysLabel(license.days_remaining)} />
          </div>
        </section>
      </div>

      <section className="sa-card sa-device-info-card">
        <div className="sa-device-card-head">
          <SectionTitle icon="DV">Device Information</SectionTitle>
          {!isDeviceRegistered && <span className="sa-device-empty-badge">Not Registered</span>}
        </div>
        <div className="sa-device-grid">
          <DetailField label="Device Name" value={deviceNameValue(device.device_name)} />
          <DetailField label="Device ID" value={deviceFieldValue(device.device_fingerprint)} />
          <DetailField label="App Version" value={deviceFieldValue(device.app_version)} />
          <DetailField label="First Seen" value={device.first_seen ? fmtDateTime(device.first_seen) : 'Not Available'} />
          <DetailField label="Last Seen" value={device.last_seen ? fmtDateTime(device.last_seen) : 'Not Available'} />
        </div>
      </section>
    </div>
  )
}
