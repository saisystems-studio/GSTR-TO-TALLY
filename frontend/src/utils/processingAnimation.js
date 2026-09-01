export const PROCESSING_STAGES = [
  { key: 'received', title: 'Data Received', subtitle: 'Preparing your data', at: 0 },
  { key: 'processing', title: 'Processing Data', subtitle: 'Organizing records', at: 500 },
  { key: 'transferring', title: 'Transferring Data', subtitle: 'Structuring fields', at: 1800 },
  { key: 'table', title: 'Preparing Table', subtitle: 'Building preview grid', at: 2300 },
  { key: 'ready', title: 'Ready to Display', subtitle: 'Your preview is ready', at: 3200 },
]

export const PROCESSING_READY_COMPLETE_MS = 3600

export function getProcessingStage(elapsedMs = 0, backendComplete = false) {
  if (backendComplete && elapsedMs >= PROCESSING_STAGES[4].at) return PROCESSING_STAGES[4]
  if (elapsedMs >= PROCESSING_STAGES[3].at) return PROCESSING_STAGES[3]
  if (elapsedMs >= PROCESSING_STAGES[2].at) return PROCESSING_STAGES[2]
  if (elapsedMs >= PROCESSING_STAGES[1].at) return PROCESSING_STAGES[1]
  return PROCESSING_STAGES[0]
}

export function getProcessingVisualMode(elapsedMs = 0, backendComplete = false) {
  const stage = getProcessingStage(elapsedMs, backendComplete)
  if (stage.key === 'ready') return 'success'
  if (elapsedMs >= 2800) return 'grid-fill'
  if (stage.key === 'table') return 'grid'
  if (stage.key === 'transferring') return 'transfer'
  if (elapsedMs >= 1200) return 'orbit-active'
  if (stage.key === 'processing') return 'orbit'
  return 'source'
}

export function isProcessingComplete(elapsedMs = 0, backendComplete = false) {
  return Boolean(backendComplete && elapsedMs >= PROCESSING_READY_COMPLETE_MS)
}
