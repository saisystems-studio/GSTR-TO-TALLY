export function formatDate(value) {
  if (value === null || value === undefined || value === '') return '-'
  const text = String(value).trim()
  let match = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s].*)?$/)
  if (match) return `${match[3]}-${match[2]}-${match[1]}`
  match = text.match(/^(\d{2})[/.](\d{2})[/.](\d{4})$/)
  if (match) return `${match[1]}-${match[2]}-${match[3]}`
  match = text.match(/^(\d{4})(\d{2})(\d{2})$/)
  if (match) return `${match[3]}-${match[2]}-${match[1]}`
  const parsed = new Date(text)
  if (Number.isNaN(parsed.getTime())) return text
  const day = String(parsed.getDate()).padStart(2, '0')
  const month = String(parsed.getMonth() + 1).padStart(2, '0')
  return `${day}-${month}-${parsed.getFullYear()}`
}
