export default function DataTransferAnimation({ mode = 'source', ready = false }) {
  const orbitDots = Array.from({ length: 5 }, (_, index) => index + 1)
  const transferDots = Array.from({ length: 8 }, (_, index) => index + 1)
  const cells = Array.from({ length: 9 }, (_, index) => index + 1)

  return <div className={`data-transfer-animation scene-${mode} ${ready ? 'is-ready' : ''}`}>
    <div className="source-box">
      <span>DATA</span>
    </div>

    <div className="orbit-processor" aria-hidden="true">
      {orbitDots.map(index => <span key={index} className={`orbit-dot dot-${index}`} />)}
    </div>

    <div className="transfer-dots" aria-hidden="true">
      {transferDots.map(index => <span key={index} />)}
    </div>

    <div className="table-grid-3x3" aria-hidden="true">
      {cells.map(index => <span key={index} />)}
    </div>

    {ready && <div className="transfer-ready-check" aria-hidden="true">✓</div>}
  </div>
}
