import { useState } from 'react'
import LoadingButton from '../common/LoadingButton'
import { DIRECT_TALLY_TEST_ENABLED, testDirectTallyConnection } from '../../services/directTallyTest'

export default function DirectTallyTest() {
  const [result, setResult] = useState(null)
  const [testing, setTesting] = useState(false)
  if (!DIRECT_TALLY_TEST_ENABLED) return null
  const run = async () => {
    setTesting(true)
    try { setResult(await testDirectTallyConnection()) }
    finally { setTesting(false) }
  }
  return <section className={`direct-tally-test ${result?.connected ? 'is-connected' : ''}`} aria-live="polite">
    <div>
      <strong>Temporary browser connectivity test</strong>
      <p>{result?.connected ? 'Direct Tally Connection: Connected' : result ? `Result: ${result.code}` : 'Tests the browser directly against 127.0.0.1:9000. This does not use the Local Connector.'}</p>
      {result?.company && <small>{result.company.company_name || 'Company name unavailable'}{result.company.company_gstin ? ` · ${result.company.company_gstin}` : ''}</small>}
    </div>
    <LoadingButton className="step3-secondary direct-tally-test-button" loading={testing} onClick={run}>{testing ? 'Testing...' : 'Test Direct Tally Connection'}</LoadingButton>
  </section>
}

