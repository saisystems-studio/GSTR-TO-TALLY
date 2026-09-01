import { useMemo, useState } from 'react'
import { saveVoucherCorrection } from '../../services/gstTallyApi'

const num = value => Number(value || 0)
const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(num(value))
const signedMoney = value => `${num(value) > 0 ? '+' : num(value) < 0 ? '-' : ''}${money(Math.abs(num(value)))}`
const FIELD_OPTIONS = [
  ['invoice_value', 'Invoice Value'], ['taxable_value', 'Taxable Value'], ['cgst', 'CGST'],
  ['sgst', 'SGST'], ['igst', 'IGST'], ['cess', 'Cess'], ['round_off', 'Round Off'],
]
const ROW_FIELDS = { invoice_value: 'invoice_total', taxable_value: 'taxable_total', cgst: 'cgst', sgst: 'sgst', igst: 'igst', cess: 'cess', round_off: 'round_off' }

export default function VoucherMismatchModal({ row, batchId, onClose, onSaved }) {
  const suggestedField = row.mismatch_analysis?.suggested_field || 'invoice_value'
  const [field, setField] = useState(suggestedField)
  const currentFor = selected => String(row[ROW_FIELDS[selected]] ?? '0.00')
  const suggestionFor = selected => selected === suggestedField ? String(row.mismatch_analysis?.suggested_value || '') : selected === 'round_off' && Math.abs(num(row.difference)) <= 1 ? String(row.difference) : ''
  const [value, setValue] = useState(currentFor(suggestedField))
  const [source, setSource] = useState('manual')
  const [saving, setSaving] = useState(false), [error, setError] = useState(''), [recalculated, setRecalculated] = useState(false)
  const selectedSuggestion = suggestionFor(field)
  const sourceMatched = Math.abs(num(row.difference)) <= 0.01
  const after = useMemo(() => {
    const values = { invoice: num(row.invoice_total), taxable: num(row.taxable_total), cgst: num(row.cgst), sgst: num(row.sgst), igst: num(row.igst), cess: num(row.cess), roundOff: num(row.round_off) }
    const target = { invoice_value: 'invoice', taxable_value: 'taxable', cgst: 'cgst', sgst: 'sgst', igst: 'igst', cess: 'cess', round_off: 'roundOff' }[field]
    values[target] = num(value)
    const calculated = values.taxable + values.cgst + values.sgst + values.igst + values.cess + values.roundOff
    const difference = values.invoice - calculated
    const sourceTaxable = num(row.taxable_total)
    const expectedTax = (row.rate_allocations || []).reduce((total, allocation) => {
      const share = sourceTaxable ? num(allocation.taxable_value) / sourceTaxable : 0
      return total + values.taxable * share * num(allocation.gst_rate) / 100
    }, 0)
    const tolerance = Math.max(0.02, Math.abs(expectedTax) * 0.0001)
    const intra = row.transaction_type === 'INTRA-STATE'
    const gstValid = intra
      ? Math.abs(values.cgst - expectedTax / 2) <= tolerance && Math.abs(values.sgst - expectedTax / 2) <= tolerance && Math.abs(values.igst) <= 0.02
      : Math.abs(values.cgst) <= 0.02 && Math.abs(values.sgst) <= 0.02 && Math.abs(values.igst - expectedTax) <= tolerance
    return { ...values, calculated, difference, totalsMatch: Math.abs(difference) <= 0.01, gstValid }
  }, [field, value, row])
  const canSave = value !== '' && Number.isFinite(Number(value)) && after.totalsMatch && after.gstValid && (field !== 'round_off' || Math.abs(num(value)) <= 1)
  const chooseField = event => { const next = event.target.value; setField(next); setValue(currentFor(next)); setSource('manual'); setError(''); setRecalculated(false) }
  const applySuggestion = () => { if (selectedSuggestion !== '') { setValue(selectedSuggestion); setSource('suggested'); setRecalculated(true) } }
  const save = async () => {
    if (!canSave) return
    setSaving(true); setError('')
    try {
      const result = await saveVoucherCorrection(batchId, row.party.gstin, row.invoice_number, row.invoice_date_iso, field, value, source)
      onSaved?.(result); onClose()
    } catch (err) { setError(err.message || 'Unable to save this correction.') }
    finally { setSaving(false) }
  }
  const validated = after.totalsMatch && after.gstValid
  return <div className="mismatch-modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className="mismatch-modal" role="dialog" aria-modal="true" aria-label="Invoice mismatch details">
    <div className="mismatch-modal-header">
      <div>
        <h2>Invoice Mismatch Details</h2>
        <p className="mismatch-modal-identity">Invoice #{row.invoice_number} · {row.party?.name}</p>
        <p className="mismatch-modal-gstin">GSTIN: {row.party?.gstin}</p>
      </div>
      <button className="mismatch-modal-close" onClick={onClose} aria-label="Close">×</button>
    </div>

    <div className="mismatch-modal-body">
      <div className="mismatch-summary-grid">
        <div className="mismatch-metric"><span>Source Invoice Value</span><b>{money(row.source_invoice_value)}</b></div>
        <div className="mismatch-metric"><span>Taxable Value</span><b>{money(row.taxable_total)}</b></div>
        <div className="mismatch-metric"><span>CGST</span><b>{money(row.cgst)}</b></div>
        <div className="mismatch-metric"><span>SGST</span><b>{money(row.sgst)}</b></div>
        <div className="mismatch-metric"><span>IGST</span><b>{money(row.igst)}</b></div>
        <div className="mismatch-metric"><span>Cess</span><b>{money(row.cess)}</b></div>
        <div className="mismatch-metric"><span>Calculated Total</span><b>{money(row.component_total)}</b></div>
        <div className={`mismatch-metric mismatch-metric-diff ${sourceMatched ? 'is-ok' : 'is-bad'}`}><span>Difference</span><b>{signedMoney(row.difference)}</b></div>
      </div>

      {!validated && <div className="mismatch-banner mismatch-banner-error">
        <strong>⚠ Review Required</strong>
        <span>The calculated invoice total does not match the source invoice value.</span>
      </div>}

      <section className="mismatch-suggestion">
        <h3>Suggested Correction</h3>
        {selectedSuggestion ? <>
          <div className="mismatch-suggestion-values"><span>Current {FIELD_OPTIONS.find(x => x[0] === field)?.[1]}<b>{money(currentFor(field))}</b></span><span>Suggested Value<b>{money(selectedSuggestion)}</b></span></div>
          <p>{row.mismatch_analysis?.reason}</p>
          <button className="mismatch-apply-suggestion" onClick={applySuggestion}>Apply Suggested Value {money(selectedSuggestion)}</button>
        </> : <p>No safe automatic correction was identified. Select the incorrect field below and enter the correct value.</p>}
      </section>

      <section className="mismatch-correction">
        <h3>Correct Value</h3>
        <div className="mismatch-correction-row">
          <label className="form-field"><span>Edit Field</span><select value={field} onChange={chooseField}>{FIELD_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label className="form-field"><span>Correct Value</span><div className="currency-input"><i>₹</i><input type="number" step="0.01" value={value} onChange={event => { setValue(event.target.value); setSource('manual'); setRecalculated(false) }} /></div></label>
        </div>
      </section>

      <div className="mismatch-compare-grid">
        <div className="mismatch-compare-card">
          <h4>Before Correction</h4>
          <div className="compare-row"><span>Invoice Value</span><b>{money(row.invoice_total)}</b></div>
          <div className="compare-row"><span>Calculated Total</span><b>{money(row.component_total)}</b></div>
          <div className="compare-row"><span>Difference</span><b>{signedMoney(row.difference)}</b></div>
        </div>
        <div className={`mismatch-compare-card ${validated ? 'is-success' : ''}`}>
          <h4>After Correction</h4>
          <div className="compare-row"><span>Invoice Value</span><b>{money(after.invoice)}</b></div>
          <div className="compare-row"><span>Calculated Total</span><b>{money(after.calculated)}</b></div>
          <div className="compare-row"><span>Difference</span><b>{signedMoney(after.difference)}</b></div>
          <div className="mismatch-badges">
            <span className={`mismatch-badge ${after.totalsMatch ? 'badge-success' : 'badge-danger'}`}>{after.totalsMatch ? '✓ Totals Match' : 'Totals Mismatch'}</span>
            <span className={`mismatch-badge ${after.gstValid ? 'badge-success' : 'badge-danger'}`}>{after.gstValid ? '✓ GST Valid' : 'GST Invalid'}</span>
          </div>
        </div>
      </div>

      {field === 'round_off' && Math.abs(num(value)) > 1 && <div className="mismatch-banner mismatch-banner-error">Round Off must remain between -₹1.00 and +₹1.00.</div>}
      {error && <div className="mismatch-banner mismatch-banner-error">{error}</div>}
    </div>

    <div className="mismatch-modal-footer">
      <button className="mismatch-btn" onClick={onClose}>Cancel</button>
      <button className="mismatch-btn mismatch-btn-outline" onClick={() => setRecalculated(true)}>Recalculate</button>
      <button className="mismatch-btn mismatch-btn-primary" disabled={!canSave || saving} onClick={save}>{saving ? 'Applying…' : 'Apply & Revalidate'}</button>
    </div>
  </div></div>
}
