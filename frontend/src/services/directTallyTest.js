const DIRECT_TALLY_URL = 'http://127.0.0.1:9000'
const DIRECT_TALLY_TIMEOUT_MS = 4000

const viteEnv = import.meta.env || {}
export const DIRECT_TALLY_TEST_ENABLED = viteEnv.VITE_DIRECT_TALLY_TEST === 'true'

export const DIRECT_TALLY_XML = `<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE><ID>GSTRDirectBrowserCompany</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE><COLLECTION NAME="GSTRDirectBrowserCompany"><TYPE>Company</TYPE><FETCH>NAME,GSTREGISTRATIONNUMBER,STATENAME</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>`

function classifyError(error, { timedOut, securePage }) {
  if (timedOut) return 'REQUEST_TIMEOUT'
  const message = String(error?.message || error || '').toLowerCase()
  if (securePage && message.includes('mixed content')) return 'MIXED_CONTENT_BLOCKED'
  if (message.includes('private network') || message.includes('local network')) return 'PRIVATE_NETWORK_BLOCKED'
  if (message.includes('blocked by client') || message.includes('network access denied')) return 'BROWSER_BLOCKED'
  if (message.includes('cors') || message.includes('cross-origin')) return 'CORS_BLOCKED'
  if (message.includes('connection refused') || message.includes('econnrefused')) return 'TALLY_NOT_RUNNING'
  // Browsers intentionally expose a generic TypeError for both CORS and a
  // refused localhost socket. CORS is the useful actionable classification;
  // the raw console error remains available for distinguishing the two.
  return 'CORS_BLOCKED'
}

function parseCompanyResponse(text) {
  const document = new DOMParser().parseFromString(text, 'application/xml')
  if (document.querySelector('parsererror')) throw new Error('Tally returned malformed XML.')
  const envelope = document.documentElement
  const status = envelope.querySelector('HEADER > STATUS, STATUS')?.textContent?.trim()
  if (!envelope || envelope.nodeName !== 'ENVELOPE' || status && status !== '1') {
    throw new Error('Tally returned an invalid or unsuccessful XML response.')
  }
  const company = envelope.querySelector('COMPANY')
  return {
    company_name: company?.getAttribute('NAME')?.trim() || company?.querySelector('NAME')?.textContent?.trim() || '',
    company_gstin: company?.querySelector('GSTREGISTRATIONNUMBER')?.textContent?.trim()?.toUpperCase() || '',
    company_state: company?.querySelector('STATENAME')?.textContent?.trim() || '',
  }
}

export async function testDirectTallyConnection({
  fetchImpl = globalThis.fetch,
  timeoutMs = DIRECT_TALLY_TIMEOUT_MS,
  securePage = typeof window !== 'undefined' && window.location.protocol === 'https:',
} = {}) {
  if (securePage) {
    const error = new Error('The HTTPS page cannot fetch the HTTP Tally endpoint (mixed content).')
    console.error('[DIRECT_TALLY_TEST]', 'MIXED_CONTENT_BLOCKED', error)
    return { code: 'MIXED_CONTENT_BLOCKED', connected: false, company: null, error: error.message }
  }
  const controller = new AbortController()
  let timer
  try {
    timer = setTimeout(() => controller.abort(), timeoutMs)
    const response = await fetchImpl(DIRECT_TALLY_URL, {
      method: 'POST', mode: 'cors', signal: controller.signal,
      headers: { 'Content-Type': 'application/xml; charset=utf-8' }, body: DIRECT_TALLY_XML,
    })
    const text = await response.text()
    if (!response.ok) return { code: 'INVALID_TALLY_RESPONSE', connected: false, company: null, httpStatus: response.status }
    const company = parseCompanyResponse(text)
    return { code: 'DIRECT_TALLY_CONNECTED', connected: true, company }
  } catch (error) {
    const timedOut = error?.name === 'AbortError'
    const code = classifyError(error, { timedOut, securePage })
    console.error('[DIRECT_TALLY_TEST]', code, error)
    return { code, connected: false, company: null, error: String(error?.message || error) }
  } finally {
    clearTimeout(timer)
  }
}
