import { useEffect } from 'react'
import InvoiceLineItems from './InvoiceLineItems'
import { formatPaise, invoiceTotals } from '../../utils/invoiceTotals'
const returnLabels = { GSTR1: 'GSTR-1', GSTR2A: 'GSTR-2A', GSTR2B: 'GSTR-2B' }

export default function CompactInvoiceViewModal({ row, onClose }) {
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [])
  const totals = invoiceTotals(row)
  const normalizeGSTIN = value => String(value ?? '').trim().toUpperCase()
  const gstinCandidates = [row.supplier_gstin, row.customer_gstin]
  if (!gstinCandidates.some(value => normalizeGSTIN(value))) gstinCandidates.push(row.party?.gstin, row.party_gstin)
  const gstins = [...new Set(gstinCandidates.map(normalizeGSTIN).filter(Boolean))]
  const badges = [returnLabels[row.return_type] || row.return_type, row.voucher_type, row.status].filter(Boolean)
  return <div className="mismatch-modal-backdrop">
    <div className="mismatch-modal invoice-summary-modal invoice-view-modal" role="dialog" aria-modal="true" aria-labelledby="compact-invoice-view-title">
      <header className="mismatch-modal-header">
        <div className="invoice-modal-header-main">
          <div className="invoice-title-row">
            <span className="invoice-modal-title" id="compact-invoice-view-title">View</span>
            <div className="invoice-number-line"><span>Invoice No.</span><strong>{row.invoice_number}</strong></div>
            <div className="invoice-status-badges">{badges.map((label, index) => <span className="invoice-status-badge" key={index}>{label}</span>)}</div>
          </div>
          <div className="invoice-modal-meta"><span>{row.invoice_date || row.invoice_date_iso || '—'}</span>{gstins.map(gstin => <span key={gstin}><span className="invoice-modal-meta-separator">•</span><span>{gstin}</span></span>)}</div>
        </div>
        <button className="mismatch-modal-close" onClick={onClose} aria-label="Close">&times;</button>
      </header>
      <div className="mismatch-modal-body">
        <InvoiceLineItems row={row} />
        <section><h3>Total Comparison</h3><div className="invoice-summary-columns">
          <div className="invoice-summary-card"><span>Component Total</span><strong>{formatPaise(totals.component)}</strong><small>Taxable + CGST + SGST + IGST + Cess</small></div>
          <div className="invoice-summary-card"><span>Source Invoice Value</span><strong>{formatPaise(totals.invoice)}</strong><small>Source invoice total, taken once</small></div>
        </div></section>
        <section><h3>Summary</h3><div className="invoice-summary-card invoice-view-summary">
          {[['Component Total', totals.component], ['Round Off', totals.roundOff], ['Final Total', totals.final], ['Invoice Value', totals.invoice], ['Remaining Difference', totals.remaining]].map(([label, amount]) => <div className="compare-row" key={label}><span>{label}</span><b>{formatPaise(amount)}</b></div>)}
          <p className={totals.remaining === 0n ? 'invoice-matched' : 'invoice-review'}>{totals.remaining === 0n ? '✓ Totals Matched' : 'Review Required'}</p>
        </div></section>
      </div>
    </div>
  </div>
}
