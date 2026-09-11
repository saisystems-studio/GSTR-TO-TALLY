import { useEffect, useState } from 'react'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { getPayment, updatePaymentStatus } from '../services/superadminApi.js'
import { ErrorCard, InfoGrid, LoadingCard, PageHeader, Status, fmtDate, fmtDateTime, fmtMoney } from './pageUtils.jsx'

export default function PaymentDetail({ paymentId, onBack }) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })
  const [modal, setModal] = useState(false)

  const load = () => {
    getPayment(paymentId)
      .then(data => setState({ loading: false, data, error: '' }))
      .catch(error => setState({ loading: false, data: null, error: error.message }))
  }
  useEffect(load, [paymentId])

  if (state.loading) return <LoadingCard />
  if (state.error) return <ErrorCard message={state.error} />
  const payment = state.data || {}

  return (
    <>
      <button type="button" className="sa-back-link" onClick={onBack}>Back to payments</button>
      <PageHeader eyebrow="Payment detail" title={payment.invoice_number || 'Payment'} subtitle={`${payment.customer || '-'} | ${payment.plan_name || '-'}`} actions={<Status value={payment.payment_status} />} />
      <section className="sa-card">
        <h2>Invoice Summary</h2>
        <InfoGrid items={[
          { label: 'Invoice Number', value: payment.invoice_number },
          { label: 'Customer', value: payment.customer },
          { label: 'Plan', value: payment.plan_name },
          { label: 'Billing Start', value: fmtDate(payment.billing_period_start) },
          { label: 'Billing End', value: fmtDate(payment.billing_period_end) },
          { label: 'Base Amount', value: fmtMoney(payment.base_amount) },
          { label: 'Discount', value: fmtMoney(payment.discount) },
          { label: 'Tax', value: fmtMoney(payment.tax) },
          { label: 'Final Amount', value: fmtMoney(payment.final_amount) },
          { label: 'Payment Method', value: payment.payment_method },
          { label: 'Transaction ID', value: payment.transaction_id },
          { label: 'Gateway Reference', value: payment.gateway_reference },
          { label: 'Payment Date', value: fmtDate(payment.payment_date) },
          { label: 'Created', value: fmtDateTime(payment.created_at) },
        ]} />
        <div className="sa-actions-row">
          <button className="sa-button sa-button-inline is-secondary" type="button" onClick={() => setModal(true)}>Change Status</button>
        </div>
      </section>
      {modal && (
        <ConfirmModal
          title="Change Payment Status"
          fields={[
            { key: 'payment_status', label: 'Payment Status', type: 'select', defaultValue: payment.payment_status, options: ['PAID', 'PENDING', 'FAILED', 'REFUNDED', 'PARTIALLY_REFUNDED'].map(value => ({ value, label: value.replaceAll('_', ' ') })) },
            { key: 'reason', label: 'Reason', type: 'textarea' },
          ]}
          onCancel={() => setModal(false)}
          onConfirm={async values => { await updatePaymentStatus(paymentId, values.payment_status, values.reason); setModal(false); load() }}
        />
      )}
    </>
  )
}
