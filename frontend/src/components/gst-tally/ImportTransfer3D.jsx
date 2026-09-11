import { useMemo } from 'react'

// Step 6's hero visual: a premium glass/neon 3D illustration of validated
// vouchers flowing from a floating 3D file stack into a floating 3D Tally
// monitor, connected by animated cyan waves, particles and pulsing data
// dots. Pure CSS + inline SVG (no canvas, no animation library) -- every
// number it renders (ready count, via headline/subline) comes from real
// props, and `status` (ACTIVE | PAUSED | COMPLETED | PARTIAL | FAILED) only
// tunes how lively the motion is; it never gates the illustration on/off.
// PAUSED freezes the transfer motion in place (the backend job genuinely
// isn't writing anything right now); PARTIAL reuses COMPLETED's calmer
// motion but an amber badge, never the green "fully successful" one.

const FILE_ROWS = [
  { key: 'excel', label: 'Excel', glyph: 'X' },
  { key: 'csv', label: 'CSV', glyph: '≡' },
  { key: 'json', label: 'JSON', glyph: '{ }' },
]

// The center lane reads as beads on a string -- two small data dots, then a
// glowing node, repeated -- rather than a row of nodes with a separate row
// of dots underneath.
const FLOW_ROW = [
  'dot', 'dot', 'node', 'dot', 'dot', 'node', 'dot', 'dot', 'node', 'dot', 'dot',
]

// Deterministic pseudo-random particle field -- computed once per mount
// (useMemo), not on every render, so the flow never visibly jumps. Kept to
// a narrow band around the wave's own centerline so the particles read as
// "riding the transfer path" instead of scattered noise across the scene.
function useParticles(count) {
  return useMemo(() => Array.from({ length: count }, (_, i) => {
    const seed = (i * 137.5) % 100
    return {
      key: i,
      top: 38 + ((seed * 0.24) % 24),
      size: [2, 3, 3, 4][i % 4],
      delay: -((i * 0.37) % 3.4),
      duration: 2.6 + ((i * 0.29) % 1.8),
      opacity: 0.5 + ((i * 0.11) % 0.4),
    }
  }), [count])
}

export default function ImportTransfer3D({ headline, subline, status = 'ACTIVE' }) {
  const particles = useParticles(12)
  const failed = status === 'FAILED'
  const partial = status === 'PARTIAL'
  const paused = status === 'PAUSED'
  const completed = status === 'COMPLETED' && !failed && !partial
  const stageModifier = failed ? 'failed' : partial ? 'partial' : paused ? 'paused' : completed ? 'completed' : 'active'

  return (
    <div className="transfer3d">
      <div className="transfer3d-head">
        <h3>{headline}</h3>
        <p>{subline}</p>
      </div>

      <div className={`transfer3d-stage t3d-stage--${stageModifier}`}>
        <div className="t3d-scene">
          <div className="t3d-glow-wash" aria-hidden="true" />
          <div className="t3d-glow t3d-glow-left" aria-hidden="true" />
          <div className="t3d-glow t3d-glow-right" aria-hidden="true" />

          <svg className="t3d-waves" viewBox="0 0 800 220" preserveAspectRatio="none" aria-hidden="true" focusable="false">
            <defs>
              <linearGradient id="t3d-wave-grad-glow" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#35D6FF" stopOpacity="0" />
                <stop offset="50%" stopColor="#35D6FF" stopOpacity=".45" />
                <stop offset="100%" stopColor="#35D6FF" stopOpacity="0" />
              </linearGradient>
              <linearGradient id="t3d-wave-grad-core" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#8FF3FF" stopOpacity="0" />
                <stop offset="45%" stopColor="#35D6FF" stopOpacity=".95" />
                <stop offset="55%" stopColor="#E8FDFF" stopOpacity=".95" />
                <stop offset="100%" stopColor="#168CF4" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path className="t3d-wave t3d-wave-glow" d="M-100,112 C120,60 260,168 400,112 C540,58 680,166 900,112"
              fill="none" stroke="url(#t3d-wave-grad-glow)" strokeWidth="26" strokeLinecap="round" />
            <path className="t3d-wave t3d-wave-core" d="M-100,110 C120,58 260,166 400,110 C540,56 680,164 900,110"
              fill="none" stroke="url(#t3d-wave-grad-core)" strokeWidth="6" strokeLinecap="round" />
          </svg>

          <div className="t3d-particles" aria-hidden="true">
            {particles.map(p => (
              <span
                key={p.key}
                className="t3d-particle"
                style={{
                  top: `${p.top}%`,
                  width: `${p.size}px`,
                  height: `${p.size}px`,
                  animationDelay: `${p.delay}s`,
                  animationDuration: `${p.duration}s`,
                  '--particle-opacity': p.opacity,
                }}
              />
            ))}
          </div>

          {/* ---------------- Left: source file card ---------------- */}
          <div className="t3d-source">
            <div className="t3d-source-stack">
              <div className="t3d-source-card">
                {FILE_ROWS.map(row => (
                  <div className="t3d-file-row" key={row.key}>
                    <span className={`t3d-file-icon t3d-file-icon--${row.key}`}>{row.glyph}</span>
                    <span className="t3d-file-label">{row.label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ---------------- Center: dots + glowing document nodes, one lane --- */}
          <div className="t3d-middle">
            <div className="t3d-flow-row">
              {(() => {
                let nodeCount = 0
                return FLOW_ROW.map((kind, i) => kind === 'node' ? (
                  <div key={i} className={`t3d-node t3d-node-${++nodeCount}`}><DocGlyph /></div>
                ) : (
                  <span key={i} className="t3d-dot" style={{ animationDelay: `${-(i * 0.22)}s` }} />
                ))
              })()}
            </div>
          </div>

          {/* ---------------- Right: 3D Tally Prime monitor ---------------------- */}
          <div className="t3d-monitor t3d-monitor--system">
            <div className="t3d-monitor-unit">
              <div className="t3d-monitor-body">
                <div className="t3d-monitor-screen">
                  <img className="t3d-tally-prime-logo" src="/assets/tally-prime-logo.png" alt="Tally Prime" />
                </div>
              </div>
              <div className="t3d-monitor-neck" />
              <div className="t3d-monitor-base" />
              {/* Remounted (key=stageModifier) whenever the status category
                  itself changes -- e.g. active -> completed -- so the
                  entrance pop (t3dBadgePop, see CSS) genuinely replays at the
                  moment success/partial/failure is reached, rather than only
                  ever playing once on the very first mount. */}
              <div key={stageModifier} className={`t3d-badge ${failed ? 't3d-badge--fail' : partial ? 't3d-badge--warn' : 't3d-badge--ok'}`}>
                {failed || partial ? '!' : '✓'}
              </div>
            </div>
            <div className="t3d-monitor-shadow" />
          </div>
        </div>
      </div>
    </div>
  )
}

function DocGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="60%" height="60%" aria-hidden="true" focusable="false">
      <rect x="4" y="2" width="16" height="20" rx="2.4" fill="#FFFFFF" />
      <rect x="7" y="7" width="10" height="1.8" rx="0.9" fill="#8FE0FF" />
      <rect x="7" y="11" width="10" height="1.8" rx="0.9" fill="#8FE0FF" />
      <rect x="7" y="15" width="6.5" height="1.8" rx="0.9" fill="#8FE0FF" />
    </svg>
  )
}
