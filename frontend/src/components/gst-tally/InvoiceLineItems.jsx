import { currencyPaise, formatPaise } from '../../utils/invoiceTotals'

const fields = [
  ['taxable_value', 'Taxable'],
  ['cgst', 'CGST'],
  ['sgst', 'SGST'],
  ['igst', 'IGST'],
  ['cess', 'Cess'],
]

const returnLabels = { GSTR1: 'GSTR-1 DATA', GSTR2A: 'GSTR-2A DATA', GSTR2B: 'GSTR-2B DATA' }

function amount(value) {
  return formatPaise(currencyPaise(value ?? '0') ?? 0n)
}

function itemTotal(item) {
  return fields.reduce((total, [field]) => total + Number(item[field] || 0), 0)
}

export default function InvoiceLineItems({ row, correctionType, correctionValue, onCorrectionChange, changedFields = [] }) {
  const items = row.items || []
  if (!items.length) return null
  const editable = Boolean(onCorrectionChange)
  const changed = field => changedFields.includes(field)
  return <section className="invoice-line-items">
    <h3>{returnLabels[row.return_type] || 'RETURN DATA'}</h3>
    <div className="invoice-items-scroll">
      <table>
        <thead><tr><th>ROT</th>{fields.map(([, label]) => <th key={label}>{label}</th>)}<th>Component Total</th><th>Invoice Value</th></tr></thead>
        <tbody>
          {items.map((item, index) => <tr key={index}>
            <td>{item.gst_rate || '—'}%</td>
            {fields.map(([field]) => <td key={field} className={changed(field) ? 'invoice-value-changed' : ''}>{amount(item[field])}</td>)}
            <td>{amount(itemTotal(item).toFixed(2))}</td>
            <td>{index === items.length - 1 ? amount(row.invoice_total) : '—'}</td>
          </tr>)}
          <tr className="invoice-line-items-total">
            <th>Total</th>
            {fields.map(([field, label]) => <td key={field} className={changed(field) ? 'invoice-value-changed' : ''}>{amount(row[field === 'taxable_value' ? 'taxable_total' : field])}</td>)}
            <td>{amount(row.component_total)}</td>
            <td>{editable && correctionType !== 'round_off'
              ? <input className={changed('invoice_total') ? 'invoice-correction-input invoice-value-changed' : 'invoice-correction-input'} value={correctionType === 'invoice_value' ? correctionValue : amount(row.invoice_total).replace('₹', '')} onChange={event => onCorrectionChange('invoice_value', event.target.value)} aria-label="Corrected invoice value" />
              : amount(row.invoice_total)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
}
