/**
 * ProcessingStages
 * -----------------------------------------------------------------------
 * A horizontal 4-stage pipeline loader shown while an uploaded file is
 * parsed into a table preview. Pure presentational component: it derives
 * every stage's status from `currentStep` and holds no internal state.
 * -----------------------------------------------------------------------
 */

export type StageStatus = 'pending' | 'active' | 'complete'

export interface StageLabel {
  title: string
  caption: string
}

export interface ProcessingStagesProps {
  currentStep: 1 | 2 | 3 | 4 | 5
  stages?: StageLabel[]
  className?: string
}

const TOKENS = {
  blue600: '#2563EB',
  blue500: '#3B82F6',
  blue400: '#60A5FA',
  blue200: '#BFDBFE',
  blue50: '#EFF6FF',
  navy900: '#0B2A6B',
  slate400: '#94A3B8',
  captionText: '#5B6B84',
  green500: '#22C55E',
  greenLight: '#6EE7A0',
  cyan400: '#38BDF8',
} as const

const GLOW = '0 0 32px rgba(59,130,246,0.45)'

const DEFAULT_STAGES: StageLabel[] = [
  { title: 'Data Received', caption: 'Preparing your data' },
  { title: 'Processing Data', caption: 'Reading and organizing records' },
  { title: 'Structuring Records', caption: 'Arranging fields and values' },
  { title: 'Preparing Table', caption: 'Building the preview grid' },
]

function getStatus(stepIndex: 1 | 2 | 3 | 4, currentStep: number): StageStatus {
  if (currentStep > stepIndex) return 'complete'
  if (currentStep === stepIndex) return 'active'
  return 'pending'
}

const DOT_COUNT = 8
const SQUARE_COUNT = 9

const dotStagger = Array.from({ length: DOT_COUNT }, (_, i) =>
  `.ps-anim-dot-${i} { animation: ps-dot-pulse 1.8s ease-in-out infinite; animation-delay: ${i * 90}ms; }`
).join('\n  ')

const squareStagger = Array.from({ length: SQUARE_COUNT }, (_, i) =>
  `.ps-anim-square-${i} { animation: ps-square-pop 320ms cubic-bezier(.34,1.56,.64,1) both; animation-delay: ${i * 60}ms; }`
).join('\n  ')

const badgeDelay = SQUARE_COUNT * 60 + 200
const rayDelay = badgeDelay + 250

const KEYFRAMES = `
@keyframes ps-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
@keyframes ps-glow-breathe { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
@keyframes ps-spin { to { transform: rotate(360deg); } }
@keyframes ps-spin-rev { to { transform: rotate(-360deg); } }
@keyframes ps-dash-flow { to { stroke-dashoffset: -48; } }
@keyframes ps-dot-pulse { 0%, 100% { transform: translateX(0) scale(1); } 50% { transform: translateX(6px) scale(1.08); } }
@keyframes ps-sphere-bob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
@keyframes ps-square-pop { from { opacity: 0; transform: scale(0.8); } to { opacity: 1; transform: scale(1); } }
@keyframes ps-badge-spring { 0% { opacity: 0; transform: scale(0.6); } 70% { opacity: 1; transform: scale(1.12); } 100% { opacity: 1; transform: scale(1); } }
@keyframes ps-ray-flash { 0% { opacity: 0; } 35% { opacity: 1; } 100% { opacity: 0.85; } }
@keyframes ps-pulse-ring { 0% { transform: scale(0.85); opacity: 0.6; } 100% { transform: scale(1.55); opacity: 0; } }

@media (prefers-reduced-motion: no-preference) {
  .ps-anim-float { animation: ps-float 3s ease-in-out infinite; }
  .ps-anim-glow-breathe { animation: ps-glow-breathe 3s ease-in-out infinite; }
  .ps-anim-ribbon-back, .ps-anim-ribbon-front { animation: ps-spin 2.4s linear infinite; }
  .ps-anim-dash-rise, .ps-anim-dash-fall { animation: ps-spin-rev 3.6s linear infinite, ps-dash-flow 1.2s linear infinite; }
  ${dotStagger}
  .ps-anim-sphere { animation: ps-sphere-bob 1.8s ease-in-out infinite; }
  ${squareStagger}
  .ps-anim-badge { animation: ps-badge-spring 480ms cubic-bezier(.34,1.56,.64,1) both; animation-delay: ${badgeDelay}ms; }
  .ps-anim-badge-init { opacity: 0; }
  .ps-anim-ray { animation: ps-ray-flash 650ms ease-out both; animation-delay: ${rayDelay}ms; }
  .ps-anim-ray-init { opacity: 0; }
  .ps-anim-pulse-ring { animation: ps-pulse-ring 1.6s ease-out infinite; }
}
`

function DocumentGlyph({ id, opacity = 1 }: { id: string; opacity?: number }) {
  return (
    <g opacity={opacity}>
      <defs>
        <linearGradient id={`${id}-body`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#FFFFFF" />
          <stop offset="100%" stopColor={TOKENS.blue50} />
        </linearGradient>
        <linearGradient id={`${id}-fold`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={TOKENS.blue500} />
          <stop offset="100%" stopColor={TOKENS.blue600} />
        </linearGradient>
      </defs>
      <rect width="132" height="164" rx="14" fill={`url(#${id}-body)`} stroke="#DBEAFE" strokeWidth="1" />
      <path d="M94 0 H132 V38 Z" fill={`url(#${id}-fold)`} />
      <path d="M94 0 L132 38 H104 Z" fill="#93C5FD" opacity="0.55" />
      <rect x="18" y="56" width="60" height="10" rx="5" fill={TOKENS.blue600} />
      <rect x="18" y="80" width="75" height="10" rx="5" fill={TOKENS.blue600} />
      <rect x="18" y="104" width="67" height="10" rx="5" fill={TOKENS.blue600} />
    </g>
  )
}

function DataReceivedArt({ status }: { status: StageStatus }) {
  const active = status === 'active'
  return (
    <svg viewBox="0 0 176 220" width={176} height={220} aria-hidden="true" focusable="false" className="block">
      <defs>
        <radialGradient id="dr-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={TOKENS.blue400} stopOpacity="0.55" />
          <stop offset="100%" stopColor={TOKENS.blue400} stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="88" cy="206" rx="66" ry="8" fill={TOKENS.blue400} opacity="0.16" />
      <ellipse cx="88" cy="216" rx="46" ry="4" fill={TOKENS.blue400} opacity="0.08" />
      <ellipse cx="88" cy="188" rx="96" ry="28" fill="url(#dr-glow)" className={active ? 'ps-anim-glow-breathe' : undefined} />
      <ellipse cx="88" cy="188" rx="84" ry="17" fill={TOKENS.blue400} />
      <g transform="translate(22 20)" className={active ? 'ps-anim-float' : undefined}>
        <DocumentGlyph id="dr-doc" />
      </g>
    </svg>
  )
}

function DataReceivedArtGhost() {
  return (
    <g transform="translate(36 6)" opacity="0.25">
      <DocumentGlyph id="pd-ghost" />
    </g>
  )
}

function OrbitRibbon() {
  return (
    <g className="ps-anim-ribbon-back" style={{ transformOrigin: '88px 92px' }}>
      <path d="M 4 92 A 84 32 0 0 1 172 92" fill="none" stroke={TOKENS.blue500} strokeWidth="10" strokeLinecap="round" />
      <polygon points="0,-6 12,0 0,6" fill={TOKENS.blue600} transform="translate(4 92) rotate(200)" />
    </g>
  )
}

function OrbitRibbonFront() {
  return (
    <g className="ps-anim-ribbon-front" style={{ transformOrigin: '88px 92px' }}>
      <path d="M 4 92 A 84 32 0 0 0 172 92" fill="none" stroke={TOKENS.blue600} strokeWidth="10" strokeLinecap="round" />
      <polygon points="0,-6 12,0 0,6" fill={TOKENS.blue600} transform="translate(172 92) rotate(20)" />
    </g>
  )
}

function DashedArcs() {
  return (
    <>
      <g className="ps-anim-dash-rise" style={{ transformOrigin: '30px 46px' }}>
        <path d="M 8 62 A 34 34 0 0 1 46 26" fill="none" stroke={TOKENS.blue500} strokeWidth="2.5" strokeLinecap="round" strokeDasharray="2 10" />
        <polygon points="0,-4.5 8,0 0,4.5" fill={TOKENS.blue500} transform="translate(46 26) rotate(-40)" />
      </g>
      <g className="ps-anim-dash-fall" style={{ transformOrigin: '146px 140px' }}>
        <path d="M 168 122 A 34 34 0 0 1 130 158" fill="none" stroke={TOKENS.blue500} strokeWidth="2.5" strokeLinecap="round" strokeDasharray="2 10" />
        <polygon points="0,-4.5 8,0 0,4.5" fill={TOKENS.blue500} transform="translate(130 158) rotate(140)" />
      </g>
    </>
  )
}

function ProcessingDataArt({ status }: { status: StageStatus }) {
  const active = status === 'active'
  return (
    <svg viewBox="0 0 176 220" width={176} height={220} aria-hidden="true" focusable="false" className="block">
      <defs>
        <radialGradient id="pd-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={TOKENS.blue400} stopOpacity="0.55" />
          <stop offset="100%" stopColor={TOKENS.blue400} stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="88" cy="188" rx="96" ry="28" fill="url(#pd-glow)" className={active ? 'ps-anim-glow-breathe' : undefined} />
      <ellipse cx="88" cy="188" rx="84" ry="17" fill={TOKENS.blue400} />
      <g transform="translate(2 66)">
        <DashedArcs />
        <OrbitRibbon />
        <g transform="translate(20 -46)">
          <DataReceivedArtGhost />
          <DocumentGlyph id="pd-doc" />
        </g>
        <OrbitRibbonFront />
      </g>
    </svg>
  )
}

function StructuringRecordsArt({ status }: { status: StageStatus }) {
  const active = status === 'active'
  const dots = Array.from({ length: DOT_COUNT }, (_, i) => {
    const t = i / (DOT_COUNT - 1)
    return { cx: 24 + i * 46, r: 3 + t * 6, opacity: 0.25 + t * 0.75, i }
  })
  const sphereX = 24 + DOT_COUNT * 46 + 4

  return (
    <svg viewBox={`0 0 ${sphereX + 40} 60`} width="100%" height="60" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false" className="block">
      <defs>
        <radialGradient id="sr-sphere" cx="35%" cy="30%" r="70%">
          <stop offset="0%" stopColor="#DBEAFE" />
          <stop offset="45%" stopColor={TOKENS.blue500} />
          <stop offset="100%" stopColor={TOKENS.blue600} />
        </radialGradient>
      </defs>
      {dots.map(dot => (
        <circle key={dot.i} cx={dot.cx} cy="30" r={dot.r} fill={TOKENS.blue500} opacity={dot.opacity} className={active ? `ps-anim-dot-${dot.i}` : undefined} />
      ))}
      <rect x={sphereX - 46} y="28.5" width="26" height="3" rx="1.5" fill={TOKENS.blue400} opacity="0.15" />
      <rect x={sphereX - 34} y="29" width="20" height="2" rx="1" fill={TOKENS.blue400} opacity="0.25" />
      <rect x={sphereX - 22} y="29.3" width="14" height="1.4" rx="0.7" fill={TOKENS.blue400} opacity="0.4" />
      <circle cx={sphereX} cy="30" r="10" fill="url(#sr-sphere)" className={active ? 'ps-anim-sphere' : undefined} />
    </svg>
  )
}

function PreparingTableArt({ status }: { status: StageStatus }) {
  const active = status === 'active'
  const showBadge = active || status === 'complete'
  const cell = 46
  const gap = 10
  const gridSize = cell * 3 + gap * 2
  const originX = 88 - gridSize / 2
  const originY = 130 - gridSize / 2
  const badgeCx = originX + gridSize - 8
  const badgeCy = originY + 8
  const squares = Array.from({ length: SQUARE_COUNT }, (_, i) => ({
    i,
    x: originX + (i % 3) * (cell + gap),
    y: originY + Math.floor(i / 3) * (cell + gap),
  }))

  return (
    <svg viewBox="0 0 176 220" width={176} height={220} aria-hidden="true" focusable="false" className="block">
      <defs>
        <linearGradient id="pt-square" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={TOKENS.blue400} />
          <stop offset="100%" stopColor={TOKENS.blue500} />
        </linearGradient>
        <radialGradient id="pt-badge" cx="35%" cy="30%" r="75%">
          <stop offset="0%" stopColor={TOKENS.greenLight} />
          <stop offset="100%" stopColor={TOKENS.green500} />
        </radialGradient>
        <filter id="pt-square-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {squares.map(sq => (
        <rect key={sq.i} x={sq.x} y={sq.y} width={cell} height={cell} rx="12" fill="url(#pt-square)" filter="url(#pt-square-glow)" className={active ? `ps-anim-square-${sq.i}` : undefined} />
      ))}

      {showBadge && (
        <g>
          {[-60, -30, 0, 30, 60].map((deg, i) => (
            <line key={deg} x1={badgeCx} y1={badgeCy - 30} x2={badgeCx} y2={badgeCy - 30 - (10 + (i % 2) * 4)} stroke={TOKENS.cyan400} strokeWidth="3" strokeLinecap="round" opacity="0.85" transform={`rotate(${deg} ${badgeCx} ${badgeCy})`} className={active ? 'ps-anim-ray ps-anim-ray-init' : undefined} />
          ))}
          <circle cx={badgeCx} cy={badgeCy} r="26" fill="url(#pt-badge)" stroke="#FFFFFF" strokeWidth="3" className={active ? 'ps-anim-badge ps-anim-badge-init' : undefined} />
          <path d={`M ${badgeCx - 10} ${badgeCy} l 6 7 l 12 -14`} fill="none" stroke="#FFFFFF" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
        </g>
      )}
    </svg>
  )
}

function NumberBadge({ index, status }: { index: number; status: StageStatus }) {
  const isPending = status === 'pending'
  return (
    <div className="relative flex h-10 w-10 items-center justify-center">
      {status === 'active' && (
        <span aria-hidden="true" className="ps-anim-pulse-ring absolute inset-0 rounded-full" style={{ border: `2px solid ${TOKENS.blue500}` }} />
      )}
      <span aria-hidden="true" className="relative flex h-10 w-10 items-center justify-center rounded-full text-sm font-bold" style={{ backgroundColor: isPending ? '#E9EEF6' : TOKENS.blue200, color: isPending ? TOKENS.slate400 : TOKENS.navy900 }}>
        {index}
      </span>
    </div>
  )
}

type StageIndex = 1 | 2 | 3 | 4
type ArtworkComponent = (props: { status: StageStatus }) => JSX.Element

const ARTWORK: Record<StageIndex, ArtworkComponent> = {
  1: DataReceivedArt,
  2: ProcessingDataArt,
  3: StructuringRecordsArt,
  4: PreparingTableArt,
}

function StageColumn({
  index,
  status,
  label,
}: {
  index: StageIndex
  status: StageStatus
  label: StageLabel
}) {
  const Art = ARTWORK[index]
  const isThird = index === 3
  const isPending = status === 'pending'
  const isActive = status === 'active'
  const stateClasses = isPending
    ? 'grayscale-[55%] opacity-[0.45] scale-100'
    : isActive
      ? 'grayscale-0 opacity-100 scale-[1.03]'
      : 'grayscale-0 opacity-100 scale-100'

  return (
    <div className={isThird ? 'flex flex-1 min-w-[160px] flex-col items-center' : 'flex w-[260px] flex-none flex-col items-center'}>
      <div
        className={`flex h-[220px] w-full ${isThird ? 'items-center' : 'items-end'} justify-center transition-[filter,opacity,transform] duration-[400ms] ease-out ${stateClasses}`}
        style={{ filter: isActive ? `drop-shadow(${GLOW})` : status === 'complete' ? 'drop-shadow(0 0 16px rgba(59,130,246,0.22))' : undefined }}
      >
        <Art status={status} />
      </div>
      <div className="mt-5">
        <NumberBadge index={index} status={status} />
      </div>
      <h3 className="mt-4 text-xl font-bold" style={{ color: TOKENS.navy900 }}>{label.title}</h3>
      <p className="mt-1 hidden text-[15px] font-normal sm:block" style={{ color: TOKENS.captionText }}>{label.caption}</p>
      <span className="sr-only">{`${label.title}, step ${index} of 4, ${status}`}</span>
    </div>
  )
}

function ProcessingStagesImpl({ currentStep, stages, className }: ProcessingStagesProps) {
  const labels = stages && stages.length === 4 ? stages : DEFAULT_STAGES
  const stepIndices: StageIndex[] = [1, 2, 3, 4]
  const clampedStep = Math.min(currentStep, 4) as StageIndex
  const activeLabel = labels[clampedStep - 1]

  return (
    <div role="status" aria-live="polite" aria-atomic="false" className={`w-full bg-[radial-gradient(60%_80%_at_50%_40%,#F0F7FF_0%,#FFFFFF_70%)] px-6 py-10 ${className ?? ''}`}>
      <style>{KEYFRAMES}</style>
      <span className="sr-only">
        {currentStep <= 4 ? `${activeLabel.title}, step ${currentStep} of 4, active` : 'All steps complete, ready to display'}
      </span>
      <div className="mx-auto flex w-full max-w-5xl flex-col items-center gap-10 md:flex-row md:items-start md:gap-6">
        {stepIndices.map(index => (
          <StageColumn key={index} index={index} status={getStatus(index, currentStep)} label={labels[index - 1]} />
        ))}
      </div>
    </div>
  )
}

export function ProcessingStages(props: ProcessingStagesProps) {
  return <ProcessingStagesImpl {...props} />
}

export default ProcessingStages
