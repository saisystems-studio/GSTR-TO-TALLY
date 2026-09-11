import { useEffect, useState } from 'react'
import InvoiceLineItems from './InvoiceLineItems'
import { saveVoucherCorrection } from '../../services/gstTallyApi'
import { currencyPaise, invoiceTotals, formatPaise, decimalPaise } from '../../utils/invoiceTotals'

const returnLabels = { GSTR1: 'GSTR-1', GSTR2A: 'GSTR-2A', GSTR2B: 'GSTR-2B' }
const correctionOptions = [['invoice_value', 'Invoice Value'], ['taxable_value', 'Taxable Value'], ['cgst', 'CGST'], ['sgst', 'SGST'], ['igst', 'IGST'], ['cess', 'Cess'], ['round_off', 'Round Off']]
const VIEW_STATUSES = ['ready', 'validated', 'correct', 'successfully corrected', 'no attention required', 'ready with warning', 'ready with gstin fallback']
const FIX_STATUSES = ['review required', 'needs attention', 'mismatch', 'correction required', 'validation failed']

function correctedTotals(row, originalTotals, correctionType, entered) {
  const fieldMap = { invoice_value: 'invoice', taxable_value: 'component', cgst: 'component', sgst: 'component', igst: 'component', cess: 'component', round_off: 'roundOff' }
  const correctionField = correctionType === 'taxable_value' ? 'taxable_total' : correctionType
  const field = fieldMap[correctionType]
  if (!field) return originalTotals

  const original = field === 'component'
    ? currencyPaise(row[correctionField] ?? '0') ?? 0n
    : originalTotals[field]
  const totals = { ...originalTotals }
  const delta = entered - original
  totals[field] += delta
  totals.final = totals.component + totals.roundOff
  totals.difference = totals.invoice - totals.component
  totals.remaining = totals.invoice - totals.final
  return totals
}

function determineInvoiceMode(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (!normalized) return 'view'
  if (VIEW_STATUSES.some(value => normalized === value || normalized.startsWith(value))) return 'view'
  if (FIX_STATUSES.some(value => normalized === value || normalized.includes(value))) return 'fix'
  return normalized.includes('review') || normalized.includes('attention') || normalized.includes('mismatch') || normalized.includes('correction') || normalized.includes('validation failed') ? 'fix' : 'view'
}

export default function VoucherMismatchModal({ row, batchId, onClose, onSaved }) {
  const [value, setValue] = useState(String(row.round_off ?? '0.00'))
  const [source, setSource] = useState('manual')
  const [correctionType, setCorrectionType] = useState('round_off')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // This is intentionally independent from the text input. Invalid draft text
  // must never remove the last valid Tally Import Data snapshot.
  const [tallyImportData, setTallyImportData] = useState(() => invoiceTotals(row))
  const mode = determineInvoiceMode(row.status)
  const readOnly = mode === 'view'
  const before = invoiceTotals(row)
  useEffect(() => { setTallyImportData(invoiceTotals(row)) }, [row])
  useEffect(() => {
    if (readOnly) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [readOnly])
  const entered = currencyPaise(value)
  const correctionField = correctionType === 'taxable_value' ? 'taxable_total' : correctionType
  const correctionChanged = correctionType === 'round_off' ? entered : currencyPaise(value)
  const after = tallyImportData
  const validationError = entered === null ? 'Enter a valid amount with up to 2 decimal places.' : ''
  const canSave = correctionChanged !== null && !saving && (correctionType !== 'round_off' || (after && after.remaining === 0n))
  const save = async () => {
    if (!canSave) return
    setSaving(true); setError('')
    try {
      const result = await saveVoucherCorrection(batchId, row.party.gstin, row.invoice_number, row.invoice_date_iso || row.invoice_date, correctionField, decimalPaise(correctionChanged), source)
      onSaved?.(result)
      onClose()
    } catch (err) { setError(err.message || 'Unable to fix this invoice.') }
    finally { setSaving(false) }
  }
  const updateCorrectionValue = (nextValue, nextCorrectionType = correctionType) => {
    setValue(nextValue)
    setSource('manual')
    setError('')
    const nextEntered = currencyPaise(nextValue)
    if (nextEntered !== null) setTallyImportData(correctedTotals(row, before, nextCorrectionType, nextEntered))
  }
  const comparison = (title, totals) => <section className={`invoice-summary-card${title === 'Tally Import Data' ? ' invoice-tally-import-card' : ''}`}>
    <h3><span className="invoice-card-icon" aria-hidden="true">{title === 'Tally Import Data' ? '▣' : '▤'}</span><span>{title}</span></h3>
    <small className="invoice-card-description">{title === 'Tally Import Data' ? 'Corrected working values' : 'Original values from the source file'}</small>
    <>{[['Component Total', 'component'], ['Round Off', 'roundOff'], ['Final Total', 'final'], ['Invoice Value', 'invoice'], ['Remaining Difference', 'remaining']].map(([label, field]) => <div className="compare-row" key={label}><span>{label}</span><b className={totals[field] !== before[field] ? 'invoice-value-changed' : ''}>{formatPaise(totals[field])}</b></div>)}
      <p className={totals.remaining === 0n ? 'invoice-matched' : 'invoice-review'}>{totals.remaining === 0n ? '\u2713 Totals Matched' : 'Review Required'}</p>
    </>
  </section>
  const badgeLabels = [
    returnLabels[row.return_type] || row.return_type,
    row.voucher_type,
    readOnly ? row.status || 'Ready' : after?.remaining === 0n ? '\u2713 Totals Matched' : 'Review Required',
  ].filter(Boolean)
  const invoiceDate = row.invoice_date || row.invoice_date_iso || '—'
  const gstin = row.party?.gstin || row.party_gstin || '—'
  return <div className="mismatch-modal-backdrop" onMouseDown={event => !saving && event.target === event.currentTarget && onClose()}>
    <div className={`mismatch-modal invoice-summary-modal ${readOnly ? 'invoice-view-modal' : 'invoice-fix-modal'}`} role="dialog" aria-modal="true" aria-labelledby="invoice-summary-title">
      <header className="mismatch-modal-header">
        <div className="invoice-modal-header-main">
          <div className="invoice-title-row">
            <div className="invoice-number-block">
              <span className="invoice-modal-title" id="invoice-summary-title">{readOnly ? 'View' : 'Fix Invoice'}</span>
              <div className="invoice-number-line"><span>Invoice No.</span> <strong>{row.invoice_number}</strong></div>
            </div>
            <div className="invoice-status-badges">{badgeLabels.map(label => <span className={`invoice-status-badge${!readOnly && label === 'Review Required' ? ' invoice-badge-review' : !readOnly && label.includes('Totals Matched') ? ' invoice-badge-matched' : ''}`} key={label}>{label}</span>)}</div>
          </div>
          <div className="invoice-modal-meta"><span>{invoiceDate}</span><span className="invoice-modal-meta-separator">•</span><span>{gstin}</span></div>
        </div>
        <button className="mismatch-modal-close" disabled={saving} onClick={onClose} aria-label="Close">&times;</button>
      </header>
      <div className="mismatch-modal-body">
        <InvoiceLineItems row={row} correctionType={correctionType} correctionValue={value}
          onCorrectionChange={(field, nextValue) => { if (field === 'invoice_value') updateCorrectionValue(nextValue, 'invoice_value') }}
          changedFields={correctionType === 'round_off' ? ['round_off'] : [correctionField]} />
        <section><h3>Total Comparison</h3><div className="invoice-summary-columns">
          <div className="invoice-summary-card"><h4><span className="invoice-card-icon" aria-hidden="true">▥</span><span>Component Total</span></h4><strong>{formatPaise(before.component)}</strong><small>Taxable + CGST + SGST + IGST + Cess</small></div>
          <div className="invoice-summary-card"><h4><span className="invoice-card-icon" aria-hidden="true">▤</span><span>Source Invoice Value</span></h4><strong>{formatPaise(before.invoice)}</strong><small>Source invoice total, taken once</small></div>
        </div></section>
        {!readOnly && <section className="invoice-correction-section"><h3>Correction</h3><div className="invoice-correction-controls">
          <label>Correction Type <select value={correctionType} onChange={event => { setCorrectionType(event.target.value); setValue(''); setTallyImportData(before); setError('') }}>{correctionOptions.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label>Correction Value <input type="text" inputMode="decimal" value={value} disabled={saving} onChange={event => updateCorrectionValue(event.target.value)} aria-label="Correction value" aria-invalid={Boolean(validationError)} />{validationError && <small className="invoice-correction-validation">{validationError}</small>}</label>
          <span className="invoice-suggested-value">Suggested Value <b>{decimalPaise(before.difference)}</b></span>
          <button className="mismatch-btn" disabled={saving} onClick={() => { const suggested = decimalPaise(before.difference); setCorrectionType('round_off'); setValue(suggested); setTallyImportData(correctedTotals(row, before, 'round_off', currencyPaise(suggested))); setSource('suggested'); setError('') }}>Apply Suggested Value</button>
        </div></section>}
        {readOnly && <section className="invoice-summary-card"><h3>Summary</h3>
          <div className="compare-row"><span>Component Total</span><b>{formatPaise(before.component)}</b></div>
          <div className="compare-row"><span>Round Off</span><b>{formatPaise(before.roundOff)}</b></div>
          <div className="compare-row"><span>Final Total</span><b>{formatPaise(before.final)}</b></div>
          <div className="compare-row"><span>Invoice Value</span><b>{formatPaise(before.invoice)}</b></div>
          <div className="compare-row"><span>Remaining Difference</span><b>{formatPaise(before.remaining)}</b></div>
          <p className={before.remaining === 0n ? 'invoice-matched' : 'invoice-review'}>{before.remaining === 0n ? '\u2713 Totals Matched' : 'Review Required'}</p>
        </section>}
        {!readOnly && <div className="invoice-summary-columns" aria-live="polite">{comparison('Source File Data', before)}{comparison('Tally Import Data', after)}</div>}
        {error && <div className="mismatch-banner mismatch-banner-error" role="alert">{error}</div>}
      </div>
      {!readOnly && <footer className="mismatch-modal-footer"><button className="mismatch-btn" disabled={saving} onClick={onClose}>Cancel</button>
        <button className="mismatch-btn mismatch-btn-primary" disabled={!canSave} onClick={save}>{saving ? 'Validating...' : 'Fix Invoice'}</button>
      </footer>}
    </div>
  </div>
}
