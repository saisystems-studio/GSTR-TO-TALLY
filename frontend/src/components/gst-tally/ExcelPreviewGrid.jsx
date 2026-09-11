import { formatDate } from '../../utils/date'
import { displayValue, formattedPreviewValue } from '../../utils/previewFormat'

// Permanent 13-column canonical order for every GST source-file preview grid
// (GSTR-1/2A/2B, JSON/Excel/CSV alike) -- must match CANONICAL_COLUMNS in
// backend services/source_preview.py. Any column the backend sends that
// isn't part of this fixed set (e.g. a dynamically detected "extra:" source
// column) is appended after it, in the order the backend already sent it --
// column order is never taken from Object.keys(row).
const CANONICAL_COLUMNS = [
  'Invoice Date',
  'Customer GSTIN',
  'Invoice No',
  'Taxable Value',
  'Tax %',
  'CGST',
  'SGST',
  'IGST',
  'Cess',
  'Invoice Value',
  'State code',
  'Reverse Charge',
  'Invoice Type',
]

function orderedColumns(columns) {
  const byLabel = new Map(columns.map(column => [column.label, column]))
  const canonical = CANONICAL_COLUMNS.map(label => byLabel.get(label)).filter(Boolean)
  const canonicalLabels = new Set(CANONICAL_COLUMNS)
  const extra = columns.filter(column => !canonicalLabels.has(column.label))
  return [...canonical, ...extra]
}

// formatDate() itself falls back to "-" for a missing value (the convention
// used everywhere else in the app), which is exactly what File Preview must
// not do -- so the empty case is handled here, before formatDate ever runs.
function formattedValue(value, format) {
  if (format === 'date') {
    const shown = displayValue(value)
    return shown ? formatDate(value) : shown
  }
  return formattedPreviewValue(value, format)
}

// Columns/rows now come straight from the backend's canonical normalizer
// (services/source_preview.py) -- one dict per row keyed by canonical field
// name, in the permanent 14-column order (see CANONICAL_COLUMNS above). No
// client-side header guessing.
// Purely visual per-column alignment -- text stays left, the date column
// centers, and money/percent columns right-align, matching the actual data
// shape each format already encodes. Never affects which columns exist, their
// order, or their values.
function alignmentClass(format) {
  if (format === 'date') return 'preview-date'
  if (format === 'money' || format === 'percent') return 'preview-number'
  return undefined
}

export default function ExcelPreviewGrid({ preview }) {
  const columns = orderedColumns(preview.columns || [])
  const rows = preview.rows || []
  const gridClass = preview.return_type === 'GSTR1' ? ' gstr1-preview-grid' : preview.return_type === 'GSTR2A' ? ' gstr2a-preview-grid' : preview.return_type === 'GSTR2B' ? ' gstr2b-preview-grid' : ''
  return <div className="source-grid-wrap excel-preview preview-table-wrapper" aria-label="Read-only spreadsheet preview">
    <table className={`source-grid preview-table${gridClass}`}>
      <thead><tr className="preview-column-header">{columns.map(column => <th className={alignmentClass(column.format)} key={column.key}>{column.label}</th>)}</tr></thead>
      <tbody>{rows.length === 0
        ? <tr><td className="empty" colSpan={columns.length}>No records available.</td></tr>
        : rows.map((row, rowIndex) => <tr key={rowIndex}>{columns.map(column => <td className={alignmentClass(column.format)} key={column.key}>{formattedValue(row[column.key], column.format)}</td>)}</tr>)}</tbody>
    </table>
  </div>
}
