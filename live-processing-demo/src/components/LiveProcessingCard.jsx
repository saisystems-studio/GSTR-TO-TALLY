import { useEffect, useState } from 'react'
import './LiveProcessingCard.css'

const CENTER = { x: 768, y: 390 }
const OUTER_R = 345
const INNER_R = 255

// Fixed spatial/visual layout for the 5 named nodes -- this is what makes the
// diagram match the reference exactly (shape, position, which side the label
// sits on). `steps` (by key) only ever supplies the *dynamic* bits: label
// text, sub text, and state -- so the same rendered layout is reusable for
// other stages of the same wizard just by passing different step data.
const NODE_LAYOUT = {
  source: { x: 430, y: 150, shape: 'circle', r: 46, labelSide: 'left', icon: 'document' },
  party: { x: 1106, y: 150, shape: 'circle', r: 46, labelSide: 'right', icon: 'people' },
  tally: { x: 330, y: 400, shape: 'square', r: 54, labelSide: 'left', icon: 'cube', emphasized: true },
  masters: { x: 1206, y: 400, shape: 'circle', r: 50, labelSide: 'right', icon: 'layers', bordered: true },
  verify: { x: 990, y: 660, shape: 'circle', r: 50, labelSide: 'below', icon: 'shield', bordered: true },
}

const CONNECTORS = [
  { id: 'link-sweep', kind: 'solid', color: 'var(--blue-gray)', dot: 'var(--blue)', dur: '3.2s',
    d: 'M 330,400 C 420,560 600,650 768,655 C 900,658 1080,600 1206,400' },
  { id: 'link-top', kind: 'dashed', color: 'var(--blue-soft)', dot: 'var(--blue)', dur: '2.8s',
    d: 'M 430,150 C 550,40 950,40 1106,150' },
  { id: 'link-right', kind: 'dashed', color: 'var(--purple)', dot: 'var(--purple)', dur: '3s',
    d: 'M 1106,150 C 1200,220 1230,300 1206,400' },
  { id: 'link-short', kind: 'dashed', color: 'var(--purple)', dot: 'var(--purple)', dur: '2.4s',
    d: 'M 990,660 C 1080,600 1150,520 1206,400' },
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

function NodeIcon({ icon, size = 22, stroke = 'currentColor' }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke, strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }
  if (icon === 'document') return <svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h5" /></svg>
  if (icon === 'people') return <svg {...common}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
  if (icon === 'layers') return <svg width={size} height={size} viewBox="0 0 24 24" fill="none"><path d="M12 2 3 7l9 5 9-5-9-5Z" fill={stroke} opacity="1" /><path d="M3 12l9 5 9-5" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity=".62" fill="none" /><path d="M3 17l9 5 9-5" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity=".32" fill="none" /></svg>
  if (icon === 'shield') return <svg {...common}><path d="M12 2 4 5v6c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V5z" /><path d="M8.5 12.5l2.5 2.5 5-5" /></svg>
  return null
}

function CubeGlyph({ size = 26 }) {
  // Small 3-face isometric cube glyph, white-on-blue -- the "system" icon
  // inside the Tally System node (distinct from the big central engine cube).
  return <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
    <path d="M16 4 27 10 16 16 5 10Z" fill="#fff" opacity=".95" />
    <path d="M5 10 16 16 16 28 5 22Z" fill="#fff" opacity=".72" />
    <path d="M27 10 16 16 16 28 27 22Z" fill="#fff" opacity=".85" />
  </svg>
}

function stateSubColor(state) {
  if (state === 'done') return 'var(--green)'
  if (state === 'active') return 'var(--blue)'
  return 'var(--text-mid)'
}

function EngineNode({ layout, step, reducedMotion }) {
  const { x, y, shape, r, labelSide, icon, bordered, emphasized } = layout
  const state = step?.state || 'pending'
  const label = step?.label || ''
  const sub = step?.sub || ''
  const iconColor = state === 'done' ? 'var(--green)' : bordered || emphasized ? (emphasized ? '#fff' : 'var(--text-dark)') : 'var(--blue)'

  const textBlock = <g className={`node-label node-label-${labelSide}`}>
    <text
      x={labelSide === 'left' ? x - r - 16 : labelSide === 'right' ? x + r + 16 : x}
      y={labelSide === 'below' ? y + r + 26 : y - 4}
      textAnchor={labelSide === 'left' ? 'end' : labelSide === 'right' ? 'start' : 'middle'}
      className="node-title"
    >{label}</text>
    <text
      x={labelSide === 'left' ? x - r - 16 : labelSide === 'right' ? x + r + 16 : x}
      y={labelSide === 'below' ? y + r + 46 : y + 16}
      textAnchor={labelSide === 'left' ? 'end' : labelSide === 'right' ? 'start' : 'middle'}
      className="node-sub"
      fill={stateSubColor(state)}
    >{sub}</text>
    {state === 'active' && (
      <g transform={`translate(${labelSide === 'left' ? x - r - 16 - 34 : labelSide === 'right' ? x + r + 16 : x - 17}, ${(labelSide === 'below' ? y + r + 54 : y + 24)})`}>
        <circle cx="4" cy="4" r="3.5" className={`pulse-dot pulse-dot-1 ${reducedMotion ? 'no-anim' : ''}`} />
        <circle cx="16" cy="4" r="3.5" className={`pulse-dot pulse-dot-2 ${reducedMotion ? 'no-anim' : ''}`} />
        <circle cx="28" cy="4" r="3.5" className={`pulse-dot pulse-dot-3 ${reducedMotion ? 'no-anim' : ''}`} />
      </g>
    )}
  </g>

  return <g className={`engine-node engine-node-${state}`}>
    {emphasized && <circle cx={x} cy={y} r={r + 20} className={`node-halo ${state === 'active' && !reducedMotion ? 'is-pulsing' : ''}`} fill="none" />}
    {shape === 'square'
      ? <rect x={x - r} y={y - r} width={r * 2} height={r * 2} rx="20"
          className={`node-shape node-shape-square node-shape-${state}`} />
      : <circle cx={x} cy={y} r={r} className={`node-shape node-shape-circle ${bordered ? 'node-shape-bordered' : 'node-shape-tinted'}`} />}
    <g transform={`translate(${x},${y})`} style={{ pointerEvents: 'none' }}>
      <g transform="translate(-13,-13)">
        {icon === 'cube' ? <CubeGlyph size={26} /> : <NodeIcon icon={icon} size={26} stroke={iconColor} />}
      </g>
    </g>
    {state === 'done' && (
      <g transform={`translate(${x + r * 0.62}, ${y + r * 0.62})`} className={reducedMotion ? '' : 'badge-pop'}>
        <circle r="12" className="node-badge-bg" />
        <path d="M-5,0 L-1.5,4 L5,-4" stroke="#fff" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </g>
    )}
    {textBlock}
  </g>
}

function TravellingDot({ connector, reducedMotion }) {
  if (reducedMotion) return null
  return <circle r="5" fill={connector.dot} className="travel-dot">
    <animateMotion dur={connector.dur} repeatCount="indefinite" rotate="auto">
      <mpath href={`#${connector.id}`} />
    </animateMotion>
  </circle>
}

export default function LiveProcessingCard({ status = 'Live Processing', steps = [], operation }) {
  const reducedMotion = usePrefersReducedMotion()
  const stepByKey = Object.fromEntries(steps.map(step => [step.key, step]))
  const complete = String(status || '').toLowerCase().includes('complete')

  return (
    <div className="lp-card">
      <div className="lp-header">
        <div className="lp-header-row">
          <span className={`lp-live-dot ${complete ? 'is-complete' : ''}`} aria-hidden="true" />
          <span className={`lp-live-title ${complete ? 'is-complete' : ''}`}>{complete ? '✓ ' : ''}{status.toUpperCase()}</span>
        </div>
        <p className="lp-subtitle">Your data is being processed securely in the background.</p>
      </div>

      <svg className="lp-diagram" viewBox="0 0 1536 780" role="img" aria-label="Live processing diagram">
        <defs>
          <radialGradient id="engineGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#bfe0ff" stopOpacity=".95" />
            <stop offset="55%" stopColor="#9cc9f7" stopOpacity=".5" />
            <stop offset="100%" stopColor="#9cc9f7" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="engineCore" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity=".95" />
            <stop offset="100%" stopColor="#dcefff" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="cubeTop" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#eaf6ff" /><stop offset="100%" stopColor="#bfe3ff" />
          </linearGradient>
          <linearGradient id="cubeLeft" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1d3f8f" /><stop offset="100%" stopColor="#12285e" />
          </linearGradient>
          <linearGradient id="cubeRight" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4d8bf0" /><stop offset="100%" stopColor="#2f6fed" />
          </linearGradient>
          <linearGradient id="tallySquareActive" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#4d8bf0" /><stop offset="100%" stopColor="#1d3f8f" />
          </linearGradient>
          <linearGradient id="tallySquareDone" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#3fcf7f" /><stop offset="100%" stopColor="#16a34a" />
          </linearGradient>
        </defs>

        {/* Hidden connector paths (also used as the motion path for each
            travelling dot via <mpath>) -- drawn once, referenced twice. */}
        {CONNECTORS.map(connector => (
          <path key={connector.id} id={connector.id} d={connector.d} fill="none"
            className={`connector connector-${connector.kind}`} stroke={connector.color} />
        ))}
        {CONNECTORS.map(connector => <TravellingDot key={`dot-${connector.id}`} connector={connector} reducedMotion={reducedMotion} />)}

        {/* Rings */}
        <circle cx={CENTER.x} cy={CENTER.y} r={OUTER_R} className={`ring ring-outer ${reducedMotion ? '' : 'spin-cw'}`} />
        <circle cx={CENTER.x} cy={CENTER.y} r={INNER_R} className={`ring ring-inner ${reducedMotion ? '' : 'spin-ccw'}`} />
        {[0, 90, 180, 260].map(angle => {
          const rad = (angle * Math.PI) / 180
          const rr = angle % 180 === 0 ? OUTER_R : INNER_R
          return <circle key={angle} cx={CENTER.x + rr * Math.cos(rad)} cy={CENTER.y + rr * Math.sin(rad)} r="4"
            className={`ring-dot ${reducedMotion ? '' : 'ring-dot-pulse'}`} />
        })}

        {/* Orbiting particles */}
        {!reducedMotion && <>
          <circle r="5" className="orbit-particle orbit-particle-a">
            <animateMotion dur="6s" repeatCount="indefinite"
              path={`M ${CENTER.x + 300},${CENTER.y} A 300,300 0 1,1 ${CENTER.x - 300},${CENTER.y} A 300,300 0 1,1 ${CENTER.x + 300},${CENTER.y}`} />
          </circle>
          <circle r="4" className="orbit-particle orbit-particle-b">
            <animateMotion dur="8s" repeatCount="indefinite"
              path={`M ${CENTER.x - 220},${CENTER.y} A 220,220 0 1,0 ${CENTER.x + 220},${CENTER.y} A 220,220 0 1,0 ${CENTER.x - 220},${CENTER.y}`} />
          </circle>
        </>}

        {/* Central engine */}
        <g className={reducedMotion ? '' : 'engine-breathe'}>
          <circle cx={CENTER.x} cy={CENTER.y} r="230" fill="url(#engineGlow)" />
        </g>
        <circle cx={CENTER.x} cy={CENTER.y} r="95" fill="url(#engineCore)" />
        <circle cx={CENTER.x - 150} cy={CENTER.y - 170} r="5" fill="var(--green)" opacity=".8" />
        <circle cx={CENTER.x + 170} cy={CENTER.y - 150} r="4" fill="#f5c542" opacity=".8" />
        <circle cx={CENTER.x - 190} cy={CENTER.y + 40} r="4" fill="var(--blue)" opacity=".6" />
        <path
          d="M0,-7 L1.6,-1.6 L7,0 L1.6,1.6 L0,7 L-1.6,1.6 L-7,0 L-1.6,-1.6 Z"
          fill="#fff" opacity=".9"
          transform={`translate(${CENTER.x - 165},${CENTER.y - 210}) scale(1.2)`}
        />

        <g transform={`translate(${CENTER.x},${CENTER.y})`} className={reducedMotion ? '' : 'cube-float'}>
          <ellipse cx="0" cy="112" rx="86" ry="16" fill="#ffffff" opacity=".9" />
          <ellipse cx="0" cy="112" rx="86" ry="16" fill="none" stroke="#dbe8fb" />
          <g transform="translate(0,-10)">
            <path d="M0,-88 68,-46 0,-4 -68,-46 Z" fill="url(#cubeTop)" stroke="#ffffff" strokeWidth="1.5" />
            <path d="M-68,-46 0,-4 0,84 -68,42 Z" fill="url(#cubeLeft)" stroke="#ffffff" strokeOpacity=".4" />
            <path d="M68,-46 0,-4 0,84 68,42 Z" fill="url(#cubeRight)" stroke="#ffffff" strokeOpacity=".4" />
            {[10, 25, 40].map(dx => <line key={dx} x1={-68 + dx * 1.35} y1={-46 + dx * 0.62 + 4} x2={-68 + dx * 1.35} y2={42 + dx * 0.62 - 30} stroke="#ffffff" strokeOpacity=".14" strokeWidth="1" />)}
            {[10, 25, 40].map(dx => <line key={`r${dx}`} x1={68 - dx * 1.35} y1={-46 + dx * 0.62 + 4} x2={68 - dx * 1.35} y2={42 + dx * 0.62 - 30} stroke="#ffffff" strokeOpacity=".18" strokeWidth="1" />)}
          </g>
          <text x="0" y="160" textAnchor="middle" className="engine-label">Tally Engine</text>
        </g>

        {Object.entries(NODE_LAYOUT).map(([key, layout]) => (
          <EngineNode key={key} layout={layout} step={stepByKey[key]} reducedMotion={reducedMotion} />
        ))}
      </svg>

      {operation && (
        <div className="lp-operation">
          <div className="lp-operation-main">
            <span className="lp-operation-icon" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 21V7l8-4 8 4v14" /><path d="M9 21v-6h6v6" /><path d="M9 11h.01M15 11h.01M9 15h.01M15 15h.01" /></svg>
            </span>
            <span className="lp-operation-text">
              <span className="lp-operation-eyebrow">CURRENT OPERATION</span>
              <span className="lp-operation-title">{operation.title}</span>
              <span className="lp-operation-desc">{operation.description}</span>
            </span>
          </div>
          <div className="lp-operation-info">
            <span className="lp-operation-info-item">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>
              <span><b>Estimated time remaining</b>{operation.etaText}</span>
            </span>
            <span className="lp-operation-sep" aria-hidden="true" />
            <span className="lp-operation-info-item">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="11" width="16" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
              <span><b>Data is secure</b>256-bit encrypted transfer</span>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
