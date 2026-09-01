const display = value => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const normalize = value => display(value).trim().toLowerCase().replace(/₹/g, '').replace(/[^a-z0-9]/g, '')

const gstr1Columns = [
  { label: 'Invoice Date', width: 120, align: 'center', format: 'date', aliases: ['invoice_date', 'Invoice Date', 'idt'] },
  { label: 'Customer GSTIN', width: 190, aliases: ['customer_gstin', 'Customer GSTIN', 'ctin', 'Recipient GSTIN', 'Buyer GSTIN'] },
  { label: 'Invoice No', width: 120, aliases: ['invoice_no', 'invoice_number', 'Invoice No', 'Invoice Number', 'inum', 'inv_no'] },
  { label: 'Taxable Value', width: 140, format: 'money', aliases: ['taxable_value', 'Taxable Value', 'txval'] },
  { label: 'Tax %', width: 90, format: 'percent', aliases: ['tax_rate', 'Tax %', 'Rate (%)', 'rt', 'rate'] },
  { label: 'CGST', width: 120, format: 'money', aliases: ['cgst', 'CGST', 'Central Tax', 'camt'] },
  { label: 'SGST', width: 120, format: 'money', aliases: ['sgst', 'SGST', 'State/UT Tax', 'samt'] },
  { label: 'IGST', width: 120, format: 'money', aliases: ['igst', 'IGST', 'Integrated Tax', 'iamt'] },
  { label: 'Invoice Value', width: 140, format: 'money', aliases: ['invoice_value', 'Invoice Value', 'Invoice Value (₹)', 'val', 'total_value'] },
  { label: 'State Code', width: 110, align: 'center', aliases: ['state_code', 'State Code', 'pos', 'Place of Supply'] },
  { label: 'Reverse Charge', width: 130, align: 'center', aliases: ['reverse_charge', 'Reverse Charge', 'Supply Attract Reverse Charge', 'rchrg'] },
  { label: 'Invoice Type', width: 130, aliases: ['invoice_type', 'Invoice Type', 'inv_typ'] },
  { label: 'Filing Period', width: 130, align: 'center', aliases: ['filing_period', 'Filing Period', 'return_period', 'fp'] },
  { label: 'Filing Type', width: 120, aliases: ['filing_type', 'Filing Type'] },
  { label: 'Filing Date', width: 130, align: 'center', format: 'date', aliases: ['filing_date', 'filling_date', 'Filing Date', 'Filling Date'] },
]

const gstr2aColumns = gstr1Columns.map(column => ({
  ...column,
  align: ['Invoice Date', 'Invoice No', 'Tax %', 'State Code', 'Reverse Charge', 'Invoice Type', 'Filing Period', 'Filing Type', 'Filing Date'].includes(column.label) ? 'center' : column.align,
  aliases: column.label === 'Tax %' ? ['tax_percent', ...column.aliases] : column.aliases,
}))

const gstr2bColumns = gstr2aColumns

const legacyPrimaryColumns = [
  { label: 'Invoice Date', aliases: ['Invoice Date', 'idt', 'invoice_date'] },
  { label: 'GSTIN', aliases: ['GSTIN', 'Supplier GSTIN', 'GSTIN of supplier', 'gstin'] },
  { label: 'Customer GSTIN', aliases: ['Customer GSTIN', 'CTIN', 'Recipient GSTIN', 'Buyer GSTIN'] },
  { label: 'Invoice No', aliases: ['Invoice No', 'Invoice Number', 'inum', 'inv_no', 'invoice_number'] },
  { label: 'Taxable Value', aliases: ['Taxable Value', 'txval', 'taxable_value'] },
  { label: 'Tax %', aliases: ['Tax %', 'Rate (%)', 'rt', 'rate', 'tax_rate'] },
  { label: 'CGST', aliases: ['CGST', 'Central Tax', 'camt'] },
  { label: 'SGST', aliases: ['SGST', 'State/UT Tax', 'samt'] },
  { label: 'IGST', aliases: ['IGST', 'Integrated Tax', 'iamt'] },
  { label: 'Invoice Value', aliases: ['Invoice Value', 'Invoice Value (₹)', 'val', 'invoice_value'] },
  { label: 'State code', aliases: ['State code', 'pos', 'Place of supply'] },
  { label: 'Reverse Charge', aliases: ['Reverse Charge', 'Supply Attract Reverse Charge', 'rchrg'] },
  { label: 'Invoice Type', aliases: ['Invoice Type', 'inv_typ', 'invoice_type'] },
]

const preferredWidth = label => {
  const value = normalize(label)
  if (value.includes('gstin')) return 190
  if (value.includes('invoiceno')) return 130
  if (value.includes('invoicedate')) return 130
  return Math.min(260, Math.max(110, String(label).length * 8))
}

function findSourceIndex(sourceColumns, aliases, used = new Set()) {
  const normalizedAliases = aliases.map(normalize)
  return sourceColumns.findIndex((name, index) => {
    if (used.has(index)) return false
    const normalizedName = normalize(name)
    return normalizedAliases.some(alias => normalizedName === alias || normalizedName.endsWith(alias))
  })
}

function buildFixedColumns(sourceColumns, definitions) {
  return definitions.map(column => {
    const sourceIndex = findSourceIndex(sourceColumns, column.aliases)
    return { ...column, format: column.format || (/date/i.test(column.label) ? 'date' : undefined), sourceIndex: sourceIndex >= 0 ? sourceIndex : null }
  })
}

function buildLegacyColumns(sourceColumns) {
  const used = new Set()
  const fixed = legacyPrimaryColumns.map(column => {
    const sourceIndex = findSourceIndex(sourceColumns, column.aliases, used)
    if (sourceIndex >= 0) used.add(sourceIndex)
    return { ...column, format: column.format || (/date/i.test(column.label) ? 'date' : undefined), sourceIndex: sourceIndex >= 0 ? sourceIndex : null }
  })
  const extras = sourceColumns.flatMap((label, sourceIndex) => used.has(sourceIndex) ? [] : [{ label: display(label), sourceIndex }])
  return [...fixed, ...extras]
}

function formattedValue(value, format) {
  const shown = display(value)
  if (shown === '-' || !format) return shown
  if (format === 'date') return formatDate(value)
  const number = Number(shown.replace(/[₹,%\s]/g, ''))
  if (!Number.isFinite(number)) return shown
  if (format === 'percent') return `${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(number)}%`
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 }).format(number)
}

export default function ExcelPreviewGrid({ preview }) {
  const sourceColumns = preview.columns || []
  const rows = preview.rows || []
  const widths = preview.column_widths || []
  const isGstr1 = preview.return_type === 'GSTR1'
  const isGstr2a = preview.return_type === 'GSTR2A'
  const isGstr2b = preview.return_type === 'GSTR2B'
  const columns = isGstr1 ? buildFixedColumns(sourceColumns, gstr1Columns) : isGstr2a ? buildFixedColumns(sourceColumns, gstr2aColumns) : isGstr2b ? buildFixedColumns(sourceColumns, gstr2bColumns) : buildLegacyColumns(sourceColumns)
  return <div className="source-grid-wrap excel-preview preview-table-wrapper" aria-label="Read-only spreadsheet preview">
    <table className={`source-grid preview-table${isGstr1 ? ' gstr1-preview-grid' : isGstr2a ? ' gstr2a-preview-grid' : isGstr2b ? ' gstr2b-preview-grid' : ''}`}>
      <colgroup>{columns.map((column, index) => <col key={index} style={{ width: `${column.width || (column.sourceIndex !== null && widths[column.sourceIndex] ? Math.min(360, Math.max(88, widths[column.sourceIndex] * 7)) : preferredWidth(column.label))}px` }} />)}</colgroup>
      <thead><tr className="preview-column-header">{columns.map((column, index) => <th key={index}>{column.label}</th>)}</tr></thead>
      <tbody>{rows.length === 0
        ? <tr><td className="empty" colSpan={columns.length}>No records available.</td></tr>
        : rows.map((row, rowIndex) => <tr key={rowIndex}>{columns.map((column, columnIndex) => <td className={column.format ? 'preview-number' : column.align ? `preview-${column.align}` : undefined} key={columnIndex}>{column.sourceIndex === null ? '-' : formattedValue(row[column.sourceIndex], column.format)}</td>)}</tr>)}</tbody>
    </table>
  </div>
}
import { formatDate } from '../../utils/date'
