import test from 'node:test'
import assert from 'node:assert/strict'

import { displayValue, filterPreviewRows, formattedPreviewValue } from './previewFormat.js'

test('displayValue preserves a real zero, never turns it into a dash', () => {
  assert.equal(displayValue(0), '0')
  assert.equal(displayValue('0'), '0')
  assert.equal(displayValue('0.00'), '0.00')
})

test('displayValue renders missing values as genuinely empty, not "-"', () => {
  assert.equal(displayValue(null), '')
  assert.equal(displayValue(undefined), '')
  assert.equal(displayValue(''), '')
})

test('displayValue passes non-empty source text through unchanged', () => {
  assert.equal(displayValue('No'), 'No')
  assert.equal(displayValue('Tamil Nadu'), 'Tamil Nadu')
  assert.equal(displayValue('KST/IN25-26/997'), 'KST/IN25-26/997')
})

test('formattedPreviewValue formats a real zero amount as money, not a dash', () => {
  assert.equal(formattedPreviewValue('0.00', 'money'), '₹0.00')
  assert.equal(formattedPreviewValue(0, 'money'), '₹0.00')
})

test('formattedPreviewValue leaves a missing amount empty, not "-" or "₹0.00"', () => {
  assert.equal(formattedPreviewValue(null, 'money'), '')
  assert.equal(formattedPreviewValue('', 'percent'), '')
})

test('formattedPreviewValue formats a real zero percent as 0%, not blank/100%', () => {
  assert.equal(formattedPreviewValue('0.000', 'percent'), '0%')
})

test('formattedPreviewValue never fabricates a tax rate for an unreadable one', () => {
  // tax_percent is null when the backend could not reliably determine it
  // (services/canonical_invoice.py) -- the preview must show that as empty,
  // never as a fabricated 100%.
  assert.equal(formattedPreviewValue(null, 'percent'), '')
})

test('formattedPreviewValue formats the acceptance-test row exactly as sourced', () => {
  assert.equal(formattedPreviewValue('3887.46', 'money'), '₹3,887.46')
  assert.equal(formattedPreviewValue('18.000', 'percent'), '18%')
  assert.equal(formattedPreviewValue('349.87', 'money'), '₹349.87')
  assert.equal(formattedPreviewValue('0.00', 'money'), '₹0.00') // IGST
  assert.equal(formattedPreviewValue('0.00', 'money'), '₹0.00') // Cess
  assert.equal(formattedPreviewValue('4587.00', 'money'), '₹4,587.00')
  assert.equal(displayValue('Tamil Nadu'), 'Tamil Nadu')
  assert.equal(displayValue(''), '') // empty State Code
  assert.equal(displayValue('No'), 'No')
})

const ROWS = [
  { invoice_date: '2025-04-15', supplier_gstin: '33FISPS8174Q1ZW', supplier_name: 'K S TRADERS',
    invoice_no: 'KST/IN25-26/997', taxable_value: '3887.46', tax_percent: '18.000',
    cgst: '349.87', sgst: '349.87', igst: '0.00', cess: '0.00', invoice_value: '4587.00',
    place_of_supply: 'Tamil Nadu', state_code: '', reverse_charge: 'No' },
  { invoice_date: '2025-04-20', supplier_gstin: '29AAACB2894G1ZJ', supplier_name: 'PALANI ANDAVAR ENTERPRISES',
    invoice_no: 'INV-0042', taxable_value: '1000.00', tax_percent: null,
    cgst: null, sgst: null, igst: null, cess: null, invoice_value: '1180.00',
    place_of_supply: '29', state_code: '29', reverse_charge: '' },
  { invoice_date: '2025-05-02', supplier_gstin: '27BBBBB1111B1Z5', supplier_name: 'R.R.FOODS',
    invoice_no: 'RRF-55', taxable_value: '500.00', tax_percent: '5.000',
    cgst: '12.50', sgst: '12.50', igst: '0.00', cess: '0.00', invoice_value: '525.00',
    place_of_supply: 'Karnataka', state_code: '29', reverse_charge: 'No' },
]

test('search by GSTIN matches the correct row', () => {
  assert.equal(filterPreviewRows(ROWS, '33FISPS8174Q1ZW').length, 1)
  assert.equal(filterPreviewRows(ROWS, '33fispS8174q1zw')[0].supplier_name, 'K S TRADERS') // case-insensitive
})

test('search by supplier name matches partially and case-insensitively', () => {
  const result = filterPreviewRows(ROWS, 'palani')
  assert.equal(result.length, 1)
  assert.equal(result[0].invoice_no, 'INV-0042')
})

test('search by invoice number matches partially', () => {
  assert.equal(filterPreviewRows(ROWS, 'KST/IN25').length, 1)
  assert.equal(filterPreviewRows(ROWS, '55').length, 1) // RRF-55
})

test('search by amount matches the exact displayed value', () => {
  const result = filterPreviewRows(ROWS, '3887.46')
  assert.equal(result.length, 1)
  assert.equal(result[0].supplier_gstin, '33FISPS8174Q1ZW')
})

test('search by state matches supplier state text', () => {
  const result = filterPreviewRows(ROWS, 'karnataka')
  assert.equal(result.length, 1)
  assert.equal(result[0].supplier_name, 'R.R.FOODS')
})

test('search finds matches across the whole dataset, not just one page', () => {
  // Simulates two rows landing on different pages of a paginated view --
  // filtering must happen over the full array before any pagination slice.
  const manyRows = [...ROWS, ...ROWS.map(row => ({ ...row, invoice_no: `${row.invoice_no}-dup` }))]
  const result = filterPreviewRows(manyRows, 'R.R.FOODS')
  assert.equal(result.length, 2)
})

test('an empty or whitespace-only search term restores every row unchanged', () => {
  assert.deepEqual(filterPreviewRows(ROWS, ''), ROWS)
  assert.deepEqual(filterPreviewRows(ROWS, '   '), ROWS)
  assert.deepEqual(filterPreviewRows(ROWS, undefined), ROWS)
})

test('search never matches a real zero value as if it were empty', () => {
  const result = filterPreviewRows(ROWS, '0.00')
  // Every row with a genuine 0.00 IGST/Cess must be found by searching "0.00".
  assert.ok(result.length >= 2)
})

test('a search with no matches returns an empty array, not the full dataset', () => {
  assert.deepEqual(filterPreviewRows(ROWS, 'nonexistent-value-zzz'), [])
})
