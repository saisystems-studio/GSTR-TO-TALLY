import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ImportTransfer3D.jsx', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../../styles/gst-tally.css', import.meta.url), 'utf8')

test('Step 6 keeps the full transfer scene and changes only the destination system', () => {
  assert.match(source, /className="t3d-source"/)
  assert.match(source, /className="t3d-middle"/)
  assert.match(source, /className="t3d-waves"/)
  assert.match(source, /className="t3d-monitor t3d-monitor--system"/)
})

test('destination system neck and base are centered beneath the monitor', () => {
  assert.match(styles, /\.t3d-monitor--system :is\(\.t3d-monitor-neck, \.t3d-monitor-base\)\s*\{[^}]*margin-inline:\s*auto/s)
})

test('destination screen uses the exact Tally Prime image asset', () => {
  assert.match(source, /src="\/assets\/tally-prime-logo\.png"/)
  assert.match(source, /className="t3d-tally-prime-logo"/)
  assert.doesNotMatch(source, /className="t3d-logo-tally"/)
  assert.doesNotMatch(source, /className="t3d-logo-prime"/)
  assert.doesNotMatch(source, /className="t3d-tally-word"/)
  assert.doesNotMatch(source, /className="t3d-prime-word"/)
})
