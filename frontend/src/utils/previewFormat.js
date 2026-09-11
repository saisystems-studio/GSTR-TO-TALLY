// File Preview value display + local search -- pure logic, shared by
// ExcelPreviewGrid.jsx (rendering) and GstTallyImport.jsx's PreviewScreen
// (search). Kept framework-free so it's directly unit-testable (see
// previewFormat.test.js) without a JSX/component test harness.

// A faithful read-only view of the source file: a missing value must render
// as a genuinely empty cell, never a "-" placeholder -- and a real 0/0.00
// must never be mistaken for missing (0 is falsy but not null/undefined/'').
export function displayValue(value) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function formattedPreviewValue(value, format) {
  const shown = displayValue(value)
  if (!shown || !format) return shown
  if (format === 'date') return shown
  const number = Number(shown.replace(/[₹,%\s]/g, ''))
  if (!Number.isFinite(number)) return shown
  if (format === 'percent') return `${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(number)}%`
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 }).format(number)
}

// Instant local search across every column already loaded in `rows` -- no
// backend call. Case-insensitive, partial match, and String(0) stays "0" so
// a genuine zero value is searchable like any other value. Never mutates or
// reorders `rows`; an empty/blank term returns the same rows unchanged so
// clearing the search always restores the full dataset.
export function filterPreviewRows(rows, term) {
  const needle = String(term ?? '').trim().toLowerCase()
  if (!needle) return rows
  return rows.filter(row => Object.values(row).some(value => String(value ?? '').toLowerCase().includes(needle)))
}
