import { memo, useMemo, useState } from 'react'
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
  'Voucher Date',
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

const ROW_HEIGHT = 39
const HEADER_HEIGHT = 38
const OVERSCAN_ROWS = 4

function ExcelPreviewGrid({ preview }) {
  const columns = useMemo(() => orderedColumns(preview.columns || []), [preview.columns])
  const rows = preview.rows || []
  const [scrollTop, setScrollTop] = useState(0)
  const viewportHeight = preview.viewportHeight || 500
  const visibleStart = Math.max(0, Math.floor(Math.max(0, scrollTop - HEADER_HEIGHT) / ROW_HEIGHT) - OVERSCAN_ROWS)
  const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN_ROWS * 2
  const visibleRows = rows.slice(visibleStart, visibleStart + visibleCount)
  const topSpacer = visibleStart * ROW_HEIGHT
  const bottomSpacer = Math.max(0, (rows.length - visibleStart - visibleRows.length) * ROW_HEIGHT)
  const gridClass = preview.return_type === 'GSTR1' ? ' gstr1-preview-grid' : preview.return_type === 'GSTR2A' ? ' gstr2a-preview-grid' : preview.return_type === 'GSTR2B' ? ' gstr2b-preview-grid' : ''
  return <div className="source-grid-wrap excel-preview preview-table-wrapper" aria-label="Read-only spreadsheet preview" onScroll={event => setScrollTop(event.currentTarget.scrollTop)}>
    <table className={`source-grid preview-table${gridClass}`}>
      <thead><tr className="preview-column-header">{columns.map(column => <th className={alignmentClass(column.format)} key={column.key}>{column.label}</th>)}</tr></thead>
      <tbody>{rows.length === 0
        ? <tr><td className="empty" colSpan={columns.length}>No records available.</td></tr>
        : <>
          <tr className="preview-virtual-spacer" aria-hidden="true"><td colSpan={columns.length} style={{ height: topSpacer, padding: 0 }} /></tr>
          {visibleRows.map((row, index) => <tr key={visibleStart + index} className={row._preview_status === 'ALREADY_IMPORTED' || row._preview_status === 'NOT_ELIGIBLE' ? 'preview-row-not-importable' : ''}>{columns.map(column => <td className={alignmentClass(column.format)} key={column.key}>{formattedValue(row[column.key], column.format)}</td>)}</tr>)}
          <tr className="preview-virtual-spacer" aria-hidden="true"><td colSpan={columns.length} style={{ height: bottomSpacer, padding: 0 }} /></tr>
        </>}</tbody>
    </table>
  </div>
}

export default memo(ExcelPreviewGrid)
