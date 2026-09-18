import test from 'node:test'
import assert from 'node:assert/strict'
import { testDirectTallyConnection } from './directTallyTest.js'

test('direct test reports mixed content before starting fetch', async () => {
  let called = false
  const result = await testDirectTallyConnection({ securePage: true, fetchImpl: async () => { called = true } })
  assert.equal(result.code, 'MIXED_CONTENT_BLOCKED')
  assert.equal(called, false)
})

test('direct test reports timeout and does not claim connection', async () => {
  const result = await testDirectTallyConnection({
    securePage: false, timeoutMs: 1,
    fetchImpl: (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }),
  })
  assert.equal(result.code, 'REQUEST_TIMEOUT')
  assert.equal(result.connected, false)
})
