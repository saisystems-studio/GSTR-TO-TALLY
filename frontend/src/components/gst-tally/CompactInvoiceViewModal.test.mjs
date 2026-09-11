import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createServer } from 'vite'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

test('View routing ignores enrichment warnings and preserves Fix and Duplicate actions', () => {
  const page = readFileSync(new URL('../../pages/GstTallyImport.jsx', import.meta.url), 'utf8')
  const handler = page.match(/className="icon-link voucher-view-btn" onClick=\{\(\) => ([^}]+)\}/)[1]
  const route = new Function('row', 'mismatchRow', 'onReview', 'onViewSkip', 'setViewRow', `return ${handler}`)
  for (const [row, mismatch, expected] of [
    [{ status: 'Ready with GSTIN Fallback', warning_code: 'LOOKUP_FAILED' }, false, 'view'],
    [{ status: 'Validated' }, false, 'view'],
    [{ status: 'Review Required', warning_code: 'LOOKUP_FAILED' }, true, 'fix'],
    [{ status: 'Needs Attention' }, true, 'fix'],
    [{ status: 'Already Imported' }, false, 'duplicate'],
  ]) {
    let action
    route(row, mismatch, () => { action = 'fix' }, () => { action = 'duplicate' }, () => { action = 'view' })
    assert.equal(action, expected)
  }
  assert.match(page, /viewRow && <CompactInvoiceViewModal/)
})

test('View and Fix render the approved return-data table and correction controls', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { default: View } = await server.ssrLoadModule('/src/components/gst-tally/CompactInvoiceViewModal.jsx')
    const items = [{ taxable_value: '19.00', cgst: '0.48', sgst: '0.48', igst: '0', cess: '0', gst_rate: '5' }, { taxable_value: '40647.00', cgst: '3658.23', sgst: '3658.23', igst: '0', cess: '0', gst_rate: '18' }]
    const row = { invoice_number: '6', invoice_date: '04-04-2025', return_type: 'GSTR1', voucher_type: 'Sales', status: 'Ready with GSTIN Fallback', warning_code: 'LOOKUP_FAILED', sandbox_warning: true, party_gstin: '33AAACY4945P1ZS', taxable_total: '40666.00', cgst: '3658.71', sgst: '3658.71', igst: '0', cess: '0', invoice_total: '47983.41', round_off: '0', items }
    const html = renderToStaticMarkup(createElement(View, { row, onClose() {} }))
    for (const heading of ['GSTR-1 DATA', 'ROT', 'Taxable', 'CGST', 'SGST', 'IGST', 'Cess', 'Invoice Value', 'Total Comparison', 'Summary']) assert.ok(html.includes(heading))
    assert.doesNotMatch(html, /Invoice Components|Source Line Items/)
    assert.match(html, /5%/)
    assert.match(html, /18%/)
    assert.match(html, /₹40,647.00/)
    assert.match(html, /₹47,983.41/)
    assert.match(html, /Review Required/)
    assert.equal((html.match(/<button/g) || []).length, 1)
    assert.match(html, /aria-label="Close"/)
    assert.doesNotMatch(html, /Party Information|Ledger Allocation|Additional taxpayer|GSTIN is used|Apply Suggested|Fix Invoice|<footer|<input/)
    const mismatch = renderToStaticMarkup(createElement(View, { row: { ...row, invoice_total: '47983.41' }, onClose() {} }))
    assert.match(mismatch, /Review Required/)
    const { default: Fix } = await server.ssrLoadModule('/src/components/gst-tally/VoucherMismatchModal.jsx')
    const detail = renderToStaticMarkup(createElement(Fix, { row: { ...row, status: 'Review Required' }, onClose() {} }))
    assert.match(detail, /GSTR-1 DATA/)
    assert.match(detail, /Correction Type/)
    for (const label of ['Invoice Value', 'Taxable Value', 'CGST', 'SGST', 'IGST', 'Cess', 'Round Off']) assert.ok(detail.includes(label))
    assert.match(detail, /Apply Suggested Value/)
    assert.match(detail, /aria-label="Correction value"/)
    assert.doesNotMatch(detail, /Invoice Components|Source Line Items/)
    const { default: LineItems } = await server.ssrLoadModule('/src/components/gst-tally/InvoiceLineItems.jsx')
    const changed = renderToStaticMarkup(createElement(LineItems, { row, changedFields: ['cgst'], onCorrectionChange() {} }))
    assert.match(changed, /invoice-value-changed/)
  } finally { await server.close() }
})
