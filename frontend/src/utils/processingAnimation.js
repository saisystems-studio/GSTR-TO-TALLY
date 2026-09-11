// Visual pacing only -- roughly a 5s sequence when the backend finishes
// promptly. None of these thresholds ever fake success: getProcessingStage
// only ever returns the 'ready' stage when the caller passes a true
// backendComplete, and the visual simply holds on the 'table' stage for as
// long as it takes for that to happen (see the ready-stage check below).
export const PROCESSING_STAGES = [
  { key: 'received', title: 'Data Received', subtitle: 'Preparing your data', at: 0 },
  { key: 'processing', title: 'Processing Data', subtitle: 'Reading and organizing records', at: 800 },
  { key: 'transferring', title: 'Structuring Records', subtitle: 'Arranging fields and values', at: 2000 },
  { key: 'table', title: 'Preparing Table', subtitle: 'Building the preview grid', at: 3100 },
  { key: 'ready', title: 'Ready to Display', subtitle: 'Your preview is ready', at: 4400 },
]

export const PROCESSING_READY_COMPLETE_MS = 5000

export function getProcessingStage(elapsedMs = 0, backendComplete = false) {
  if (backendComplete && elapsedMs >= PROCESSING_STAGES[4].at) return PROCESSING_STAGES[4]
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
  return Boolean(backendComplete && elapsedMs >= PROCESSING_READY_COMPLETE_MS)
}
