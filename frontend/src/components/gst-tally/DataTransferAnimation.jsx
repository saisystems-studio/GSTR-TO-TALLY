function DataSource() {
  return <div className="dt-source" aria-hidden="true"><span className="dt-fold"/><strong>DATA</strong><small>RECORDS</small></div>
}

function DataBlocks() {
  return <div className="dt-stream" aria-hidden="true">{Array.from({ length: 10 }, (_, index) => <i key={index} style={{ '--particle': index }} />)}</div>
}

function ProcessingCore() {
  return <div className="dt-core" aria-hidden="true"><span/><span/><span/></div>
}

function TableBuilder({ ready }) {
  return <div className={`dt-table ${ready ? 'is-ready' : ''}`} aria-hidden="true">{Array.from({ length: 12 }, (_, index) => <i key={index} style={{ '--cell': index }} />)}{ready && <b>✓</b>}</div>
}

export default function DataTransferAnimation({ mode = 'source', ready = false }) {
  const copy = ready ? ['Ready to Display', 'Your preview is ready'] : mode === 'build' ? ['Preparing Table', 'Building the preview grid'] : mode === 'align' ? ['Structuring Records', 'Arranging fields and values'] : mode === 'flow' ? ['Processing Data', 'Reading and organizing records'] : ['Data Received', 'Preparing your data']
  return <div className={`dt-transform scene-${mode} ${ready ? 'is-ready' : ''}`}>
    <DataSource/><DataBlocks/><ProcessingCore/><span className="dt-output" aria-hidden="true"/><TableBuilder ready={ready}/>
    <div className="dt-caption"><strong>{copy[0]}</strong><span>{copy[1]}</span></div>
  </div>
}
