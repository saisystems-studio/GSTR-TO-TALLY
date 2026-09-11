// Decimal strings to integer paise: no binary floating-point currency arithmetic.
export function currencyPaise(value) {
  const match = String(value).trim().match(/^([+-]?)(\d+)(?:\.(\d{0,2}))?$/)
  if (!match) return null
  const amount = BigInt(match[2]) * 100n + BigInt((match[3] || '').padEnd(2, '0'))
  return match[1] === '-' ? -amount : amount
}
export function decimalPaise(value) {
  const abs = value < 0n ? -value : value
  return `${value < 0n ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`
}
export function formatPaise(value) {
  const abs = value < 0n ? -value : value
  return `${value < 0n ? '-' : ''}\u20b9${(abs / 100n).toLocaleString('en-IN')}.${String(abs % 100n).padStart(2, '0')}`
}
export function invoiceTotals(row, enteredRoundOff) {
  const amount = key => currencyPaise(row[key] ?? '0') ?? 0n
  const component = ['taxable_total', 'cgst', 'sgst', 'igst', 'cess'].reduce((sum, key) => sum + amount(key), 0n)
  const invoice = amount('invoice_total')
  const roundOff = enteredRoundOff ?? amount('round_off')
  const final = component + roundOff
  return { component, invoice, roundOff, final, difference: invoice - component, remaining: invoice - final }
}
