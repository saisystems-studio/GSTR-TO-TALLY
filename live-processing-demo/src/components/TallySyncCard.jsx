import { useEffect, useState } from 'react'
import './TallySyncCard.css'

const CENTER = { x: 755, y: 420 }

// State A (processing): 5 nodes around the ring.
const NODES_PROCESSING = [
  { key: 'source', x: 440, y: 175, label: 'Source Data', sub: 'Loaded', state: 'done', icon: 'document', labelSide: 'left' },
  { key: 'party', x: 1075, y: 175, label: 'Party Details', sub: 'Fetched', state: 'done', icon: 'people', labelSide: 'right' },
  { key: 'tally', x: 310, y: 420, label: 'Tally System', sub: 'Validating Company', state: 'active', icon: 'system', labelSide: 'left' },
  { key: 'masters', x: 1200, y: 420, label: 'Masters Ready', sub: 'Pending', state: 'pending', icon: 'layers', labelSide: 'right' },
  { key: 'verify', x: 755, y: 665, label: 'Company Verify', sub: 'Pending', state: 'pending', icon: 'shield', labelSide: 'below' },
]

// State B (complete): 6 nodes, two rows of three -- every node done/green.
const NODES_COMPLETE = [
  { key: 'source', x: 390, y: 175, label: 'Source Data', sub: 'Loaded', icon: 'document', labelSide: 'left' },
  { key: 'party', x: 755, y: 150, label: 'Party Details', sub: 'Fetched', icon: 'people', labelSide: 'above' },
  { key: 'tally', x: 1120, y: 175, label: 'Tally System', sub: 'Connected', icon: 'system', labelSide: 'right' },
  { key: 'masters', x: 390, y: 665, label: 'Masters Ready', sub: 'Ready', icon: 'layers', labelSide: 'left' },
  { key: 'license', x: 755, y: 690, label: 'Tally License', sub: 'Verified', icon: 'license', labelSide: 'below' },
  { key: 'verify', x: 1120, y: 665, label: 'Company Verify', sub: 'Verified', icon: 'shield', labelSide: 'right' },
]

// State A connectors -- solid sweep + three dashed arcs, each with a
// continuously travelling dot (spec: "actively animated traveling dots").
const CONNECTORS_PROCESSING = [
  { id: 'ts-sweep', kind: 'solid', color: '#b9c6dd', dot: '#2f6fed', dur: '3.2s',
    d: 'M 310,420 C 400,590 600,665 755,665 C 900,665 1100,600 1200,420' },
  { id: 'ts-top', kind: 'dashed', color: '#a9c9f5', dot: '#2f6fed', dur: '2.8s',
    d: 'M 440,175 C 560,60 950,60 1075,175' },
  { id: 'ts-right', kind: 'dashed', color: '#7c5cff', dot: '#7c5cff', dur: '3s',
    d: 'M 1075,175 C 1180,240 1220,320 1200,420' },
  { id: 'ts-short', kind: 'dashed', color: '#7c5cff', dot: '#7c5cff', dur: '2.4s',
    d: 'M 755,665 C 950,650 1120,560 1200,420' },
]

// State B connectors -- a loose static hexagon ring joining all 6 nodes,
// per spec "mostly static ... just faint pulsing dots at a couple of joints".
const CONNECTORS_COMPLETE = [
  { id: 'tc-1', d: 'M 390,175 C 550,140 620,130 755,150' },
  { id: 'tc-2', d: 'M 755,150 C 900,130 980,140 1120,175' },
  { id: 'tc-3', d: 'M 1120,175 C 1200,300 1200,340 1120,665' },
  { id: 'tc-4', d: 'M 1120,665 C 980,700 900,708 755,690' },
  { id: 'tc-5', d: 'M 755,690 C 620,708 550,700 390,665' },
  { id: 'tc-6', d: 'M 390,665 C 310,340 310,300 390,175' },
]

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(query.matches)
    const onChange = event => setReduced(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return reduced
}

function NodeIcon({ icon, size = 24, stroke = 'currentColor' }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke, strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }
  if (icon === 'document') return <svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h5" /></svg>
  if (icon === 'people') return <svg {...common}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
  if (icon === 'system') return <svg {...common}><path d="M9 2v4M15 2v4" /><path d="M7 8h10l-1 6a4 4 0 0 1-4 4h0a4 4 0 0 1-4-4z" /><path d="M12 18v4" /></svg>
  if (icon === 'layers') return <svg width={size} height={size} viewBox="0 0 24 24" fill="none"><path d="M12 2 3 7l9 5 9-5-9-5Z" fill={stroke} /><path d="M3 12l9 5 9-5" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity=".62" fill="none" /><path d="M3 17l9 5 9-5" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity=".32" fill="none" /></svg>
  if (icon === 'shield') return <svg {...common}><path d="M12 2 4 5v6c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V5z" /><path d="M8.5 12.5l2.5 2.5 5-5" /></svg>
  if (icon === 'license') return <svg {...common}><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="8.5" cy="12" r="2.2" /><path d="M13 9.5h6M13 12h6M13 14.5h4" /></svg>
  return null
}

function labelPosition(node) {
  const { x, y, labelSide } = node
  if (labelSide === 'left') return { titleX: x - 68, titleY: y - 4, subY: y + 16, anchor: 'end' }
  if (labelSide === 'right') return { titleX: x + 68, titleY: y - 4, subY: y + 16, anchor: 'start' }
  if (labelSide === 'above') return { titleX: x, titleY: y - 66, subY: y - 48, anchor: 'middle' }
  return { titleX: x, titleY: y + 78, subY: y + 98, anchor: 'middle' } // below
}

function StatusNode({ node, complete, reducedMotion }) {
  const state = complete ? 'done' : node.state
  const { titleX, titleY, subY, anchor } = labelPosition(node)
  const r = 46
  const iconColor = state === 'done' ? '#16a34a' : state === 'active' ? '#2f6fed' : '#5b6478'
  const circleFill = state === 'done' ? '#e6f7ec' : '#eef1f7'
  const subColor = state === 'done' ? '#16a34a' : state === 'active' ? '#2f6fed' : '#5b6478'
  return <g className={`ts-node ts-node-${state}`}>
    {state === 'active' && <circle cx={node.x} cy={node.y} r={r + 10} className={`ts-node-halo ${reducedMotion ? '' : 'is-pulsing'}`} fill="none" />}
    <circle cx={node.x} cy={node.y} r={r} fill={circleFill} stroke={state === 'pending' ? '#dbe2ef' : 'none'} strokeWidth="1.5" />
    <g transform={`translate(${node.x - 12},${node.y - 12})`}><NodeIcon icon={node.icon} size={24} stroke={iconColor} /></g>
    {state === 'done' && <g transform={`translate(${node.x + 30}, ${node.y + 30})`} className={reducedMotion ? '' : 'ts-badge-pop'}>
      <circle r="11" fill="#16a34a" />
      <path d="M-4.5,0 L-1.2,3.6 L4.5,-3.6" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </g>}
    <text x={titleX} y={titleY} textAnchor={anchor} className="ts-node-title">{node.label}</text>
    <text x={titleX} y={subY} textAnchor={anchor} className="ts-node-sub" fill={subColor}>{state === 'pending' ? 'Pending' : node.sub}</text>
  </g>
}

function TravelDot({ connector, reducedMotion }) {
  if (reducedMotion) return null
  return <circle r="5" fill={connector.dot} className="ts-travel-dot">
    <animateMotion dur={connector.dur} repeatCount="indefinite" rotate="auto"><mpath href={`#${connector.id}`} /></animateMotion>
  </circle>
}

export default function TallySyncCard({ state = 'processing' }) {
  const complete = state === 'complete'
  const reducedMotion = usePrefersReducedMotion()
  const nodes = complete ? NODES_COMPLETE : NODES_PROCESSING
  const connectors = complete ? CONNECTORS_COMPLETE : CONNECTORS_PROCESSING

  return (
    <div className="ts-card">
      <div className="ts-header">
        <div className="ts-header-row">
          <span className={`ts-live-dot ${complete ? 'is-complete' : ''}`} aria-hidden="true" />
          <span className={`ts-live-title ${complete ? 'is-complete' : ''}`}>{complete ? '✓ VERIFICATION COMPLETE' : 'LIVE PROCESSING'}</span>
        </div>
        <p className="ts-subtitle">{complete ? 'All Tally verification steps completed successfully.' : 'Your data is being processed securely in the background.'}</p>
      </div>

      <svg className="ts-diagram" viewBox="0 0 1536 780" role="img" aria-label={complete ? 'Verification complete diagram' : 'Live processing diagram'}>
        <defs>
          {/* ================= EXACT GLOW SPEC (verbatim per state) ================= */}
          {!complete && <>
            <radialGradient id="glowGrad" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="rgba(160,205,255,0.9)" />
              <stop offset="45%" stopColor="rgba(140,190,255,0.35)" />
              <stop offset="100%" stopColor="rgba(140,190,255,0)" />
            </radialGradient>
            <radialGradient id="glowCore" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="rgba(255,255,255,0.95)" />
              <stop offset="60%" stopColor="rgba(200,230,255,0.4)" />
              <stop offset="100%" stopColor="rgba(200,230,255,0)" />
            </radialGradient>
          </>}
          {complete && <>
            <radialGradient id="glowGrad" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="rgba(160,225,180,0.9)" />
              <stop offset="45%" stopColor="rgba(150,215,175,0.35)" />
              <stop offset="100%" stopColor="rgba(150,215,175,0)" />
            </radialGradient>
            <radialGradient id="glowCore" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="rgba(255,255,255,0.95)" />
              <stop offset="60%" stopColor="rgba(210,240,220,0.4)" />
              <stop offset="100%" stopColor="rgba(210,240,220,0)" />
            </radialGradient>
          </>}
          <filter id="softBlur" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="6" />
          </filter>

          {/* Cube gradients (State A) */}
          <linearGradient id="cubeTop" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#d6ecff" /><stop offset="1" stopColor="#8fc4fb" /></linearGradient>
          <linearGradient id="cubeLeft" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#3f7fe0" /><stop offset="1" stopColor="#1f4fa8" /></linearGradient>
          <linearGradient id="cubeRight" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#5b98ee" /><stop offset="1" stopColor="#2f66c9" /></linearGradient>

          {/* Ribbon gradients (State B) -- teal-to-blue, deliberately distinct
              from the surrounding green theme (spec is explicit about this). */}
          <linearGradient id="ribbonTop" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#7fe0d0" /><stop offset="1" stopColor="#3fb0c9" /></linearGradient>
          <linearGradient id="ribbonLeft" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2f8fc9" /><stop offset="1" stopColor="#1d5fa8" /></linearGradient>
          <linearGradient id="ribbonRight" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#4fb0d8" /><stop offset="1" stopColor="#2f7fc0" /></linearGradient>
        </defs>

        {/* connectors (drawn first, under everything else) */}
        {connectors.map(connector => (
          <path key={connector.id} id={connector.id} d={connector.d} fill="none"
            className={complete ? 'ts-link ts-link-static' : `ts-link ts-link-${connector.kind}`}
            stroke={complete ? '#c9d3e8' : connector.color} />
        ))}
        {!complete && connectors.map(connector => <TravelDot key={`dot-${connector.id}`} connector={connector} reducedMotion={reducedMotion} />)}
        {complete && !reducedMotion && [connectors[0], connectors[3]].map(connector => (
          <circle key={`pulse-${connector.id}`} r="4" fill="#7fd3a0" className="ts-joint-pulse">
            <animateMotion dur="4s" repeatCount="indefinite"><mpath href={`#${connector.id}`} /></animateMotion>
          </circle>
        ))}

        {/* rings */}
        <circle cx={CENTER.x} cy={CENTER.y} r="345" fill="none" stroke="#cddaf3" strokeWidth="1.6" strokeDasharray="3 7"
          className={complete ? 'ts-ring' : `ts-ring ${reducedMotion ? '' : 'spin-slow'}`} />
        <circle cx={CENTER.x} cy={CENTER.y} r="255" fill="none" stroke="#c3d3f6" strokeWidth="1.6"
          className={complete ? 'ts-ring' : `ts-ring ${reducedMotion ? '' : 'spin-slow-rev'}`} />

        {/* ================= glow (verbatim radii/blur/animation) ================= */}
        <circle cx={CENTER.x} cy={CENTER.y} r="175" fill="url(#glowGrad)" filter="url(#softBlur)" className={`glowc ${reducedMotion ? '' : 'is-breathing'}`} />
        <circle cx={CENTER.x} cy={CENTER.y} r="95" fill="url(#glowCore)" className={`glowc ${reducedMotion ? '' : 'is-breathing'}`} />

        {!complete && <>
          {/* decorative static dots + sparkle, State A only */}
          <circle cx={CENTER.x - 150} cy={CENTER.y - 170} r="5" fill="#3fbf6f" opacity=".85" />
          <circle cx={CENTER.x + 170} cy={CENTER.y - 150} r="4" fill="#f0b429" opacity=".85" />
          <circle cx={CENTER.x - 190} cy={CENTER.y + 40} r="4" fill="#c9d6ee" opacity=".8" />
          <path d="M0,-7 L1.6,-1.6 L7,0 L1.6,1.6 L0,7 L-1.6,1.6 L-7,0 L-1.6,-1.6 Z" fill="#fff" opacity=".9"
            transform={`translate(${CENTER.x - 165},${CENTER.y - 210}) scale(1.2)`} />
        </>}

        {/* ================= center icon ================= */}
        {!complete && <g className={reducedMotion ? '' : 'cubegrp'}>
          <g transform="translate(665,325)">
            <polygon points="90,8 168,50 90,92 12,50" fill="url(#cubeTop)" />
            <polygon points="12,50 90,92 90,182 12,140" fill="url(#cubeLeft)" />
            <polygon points="168,50 90,92 90,182 168,140" fill="url(#cubeRight)" />
            <polygon points="90,8 168,50 90,92 12,50" fill="none" stroke="rgba(255,255,255,.55)" strokeWidth="1.5" />
            <path d="M40,60 L40,110 M56,68 L56,120 M72,76 L72,128" stroke="rgba(255,255,255,.35)" strokeWidth="1.2" />
            <path d="M140,60 L140,110 M124,68 L124,120 M108,76 L108,128" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
            <polygon points="70,38 90,48 110,38 90,28" fill="rgba(255,255,255,.55)" />
          </g>
        </g>}
        {!complete && <>
          <polygon points="695,505 815,505 845,525 665,525" fill="#f2f6ff" />
          <polygon points="665,525 845,525 845,538 665,538" fill="#e2eaf9" />
        </>}

        {complete && <g transform={`translate(${CENTER.x},${CENTER.y})`}>
          <polygon points="0,-80 65,10 0,40 -65,10" fill="url(#ribbonTop)" />
          <polygon points="-65,10 0,40 0,80 -65,45" fill="url(#ribbonLeft)" />
          <polygon points="65,10 0,40 0,80 65,45" fill="url(#ribbonRight)" />
          <polygon points="0,-80 65,10 0,40 -65,10" fill="none" stroke="rgba(255,255,255,.5)" strokeWidth="1.5" />
          <circle cx="0" cy="10" r="10" fill="#f5c542" className={reducedMotion ? '' : 'gold-pulse'} />
        </g>}

        <text x={CENTER.x} y={complete ? CENTER.y + 148 : CENTER.y + 160} textAnchor="middle" className="ts-engine-label">Tally Engine</text>

        {nodes.map(node => <StatusNode key={node.key} node={node} complete={complete} reducedMotion={reducedMotion} />)}
      </svg>

      {complete && (
        <div className="ts-confirm-row">
          <span className="ts-confirm-pill">
            <span className="ts-confirm-check" aria-hidden="true">✓</span> Verification Complete
          </span>
          <button type="button" className="ts-confirm-button">
            <span aria-hidden="true">✓</span> Confirmed
          </button>
        </div>
      )}

      <div className="ts-operation-bar">
        <div className="ts-operation-main">
          <span className={`ts-operation-icon ${complete ? 'is-idle' : 'is-live'}`} aria-hidden="true">
            {complete
              ? <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>
              : <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 21V7l8-4 8 4v14" /><path d="M9 21v-6h6v6" /><path d="M9 11h.01M15 11h.01M9 15h.01M15 15h.01" /></svg>}
          </span>
          <span className="ts-operation-text">
            <span className={`ts-operation-eyebrow ${complete ? 'is-idle' : ''}`}>CURRENT OPERATION</span>
            <span className="ts-operation-title">{complete ? 'Idle' : 'Verifying company'}</span>
            <span className="ts-operation-desc">{complete ? 'No background operation is currently running.' : 'Matching the selected company with the company currently open in Tally.'}</span>
          </span>
        </div>
        <div className="ts-operation-info">
          <span className="ts-operation-info-item">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>
            <span><b>Estimated time remaining</b>Just a few seconds</span>
          </span>
          <span className="ts-operation-sep" aria-hidden="true" />
          <span className="ts-operation-info-item">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="11" width="16" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
            <span><b>Data is secure</b>256-bit encrypted transfer</span>
          </span>
          {complete && <>
            <span className="ts-operation-sep" aria-hidden="true" />
            <span className="ts-operation-waiting-pill">
              <span className={`ts-gold-dot ${reducedMotion ? '' : 'is-pulsing'}`} aria-hidden="true" />
              Waiting for Confirmation
            </span>
          </>}
        </div>
      </div>
    </div>
  )
}
