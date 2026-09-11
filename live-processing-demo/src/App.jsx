import LiveProcessingCard from './components/LiveProcessingCard.jsx'
import TallySyncCard from './components/TallySyncCard.jsx'

const STEPS = [
  { key: 'source', label: 'Source Data', sub: 'Loaded', state: 'done' },
  { key: 'party', label: 'Party Details', sub: 'Fetched', state: 'done' },
  { key: 'tally', label: 'Tally System', sub: 'Validating Company', state: 'active' },
  { key: 'masters', label: 'Masters Ready', sub: 'Pending', state: 'pending' },
  { key: 'verify', label: 'Company Verify', sub: 'Pending', state: 'pending' },
]

const OPERATION = {
  title: 'Verifying company',
  description: 'Matching the selected company with the company currently open in Tally.',
  etaText: 'Just a few seconds',
}

export default function App() {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 48, padding: '32px 16px' }}>
      <section style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
        <h2 style={{ margin: 0, color: '#5b6478', fontSize: 13, fontWeight: 700, letterSpacing: '.4px', textTransform: 'uppercase' }}>LiveProcessingCard</h2>
        <LiveProcessingCard status="Live Processing" steps={STEPS} operation={OPERATION} />
      </section>

      <section style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
        <h2 style={{ margin: 0, color: '#5b6478', fontSize: 13, fontWeight: 700, letterSpacing: '.4px', textTransform: 'uppercase' }}>TallySyncCard -- state="processing"</h2>
        <TallySyncCard state="processing" />
      </section>

      <section style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
        <h2 style={{ margin: 0, color: '#5b6478', fontSize: 13, fontWeight: 700, letterSpacing: '.4px', textTransform: 'uppercase' }}>TallySyncCard -- state="complete"</h2>
        <TallySyncCard state="complete" />
      </section>
    </div>
  )
}
