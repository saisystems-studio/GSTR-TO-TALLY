import test from 'node:test'
import assert from 'node:assert/strict'
import { currencyPaise, invoiceTotals, decimalPaise, formatPaise } from './invoiceTotals.js'
test('multi-item invoice preview uses aggregated components and one source total', () => {
 const row = { taxable_total: '300', cgst: '23', sgst: '23', invoice_total: '340', round_off: '0' }
 const before = invoiceTotals(row)
 assert.equal(before.component, 34600n)
 assert.equal(before.difference, -600n)
 assert.equal(invoiceTotals(row, currencyPaise('-6.00')).remaining, 0n)
 assert.equal(before.final, 34600n)
 assert.equal(row.round_off, '0')
})
test('signed, zero and decimal amounts remain exact', () => {
 for (const value of ['-6', '-6.00', '+6', '6', '6.00', '0.50', '-0.50', '0']) assert.notEqual(currencyPaise(value), null)
 for (const value of ['', '-', 'NaN', 'Infinity', '1.001', '1e3']) assert.equal(currencyPaise(value), null)
 assert.equal(decimalPaise(currencyPaise('-0.50')), '-0.50')
 assert.equal(formatPaise(12345678n), '\u20b91,23,456.78')
 assert.equal(invoiceTotals({taxable_total:'0.10', igst:'0.20', invoice_total:'0.30'}).remaining, 0n)
 assert.equal(invoiceTotals({taxable_total:'334', invoice_total:'340'}, 600n).remaining, 0n)
})
