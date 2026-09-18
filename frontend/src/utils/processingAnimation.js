// Visual pacing only. It never delays the workflow: a true backend result
// immediately moves to the ready state, while a slower request advances
// through these short neutral stages.
export const PROCESSING_STAGES = [
  { key: 'received', title: 'Data Received', subtitle: 'Preparing your data', at: 0 },
  { key: 'processing', title: 'Processing Data', subtitle: 'Reading and organizing records', at: 250 },
  { key: 'transferring', title: 'Structuring Records', subtitle: 'Arranging fields and values', at: 600 },
  { key: 'table', title: 'Preparing Table', subtitle: 'Building the preview grid', at: 950 },
  { key: 'ready', title: 'Ready to Display', subtitle: 'Your preview is ready', at: 1200 },
]

export const PROCESSING_READY_COMPLETE_MS = 1200

export function getProcessingStage(elapsedMs = 0, backendComplete = false) {
  if (backendComplete) return PROCESSING_STAGES[4]
  if (elapsedMs >= PROCESSING_STAGES[3].at) return PROCESSING_STAGES[3]
  if (elapsedMs >= PROCESSING_STAGES[2].at) return PROCESSING_STAGES[2]
  if (elapsedMs >= PROCESSING_STAGES[1].at) return PROCESSING_STAGES[1]
  return PROCESSING_STAGES[0]
}

export function getProcessingVisualMode(elapsedMs = 0, backendComplete = false) {
  const stage = getProcessingStage(elapsedMs, backendComplete)
  if (stage.key === 'ready') return 'ready'
  if (stage.key === 'table') return 'build'
  if (stage.key === 'transferring') return 'align'
  if (stage.key === 'processing') return 'flow'
  return 'source'
}

export function isProcessingComplete(elapsedMs = 0, backendComplete = false) {
  return Boolean(backendComplete)
}
