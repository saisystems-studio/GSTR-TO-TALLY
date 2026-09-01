import test from 'node:test'
import assert from 'node:assert/strict'

import {
  PROCESSING_STAGES,
  getProcessingStage,
  getProcessingVisualMode,
  isProcessingComplete,
} from './processingAnimation.js'

test('processing animation exposes the exact neutral stage copy', () => {
  assert.deepEqual(PROCESSING_STAGES.map(stage => [stage.title, stage.subtitle]), [
    ['Data Received', 'Preparing your data'],
    ['Processing Data', 'Organizing records'],
    ['Transferring Data', 'Structuring fields'],
    ['Preparing Table', 'Building preview grid'],
    ['Ready to Display', 'Your preview is ready'],
  ])

  const combinedCopy = PROCESSING_STAGES.flatMap(stage => [stage.title, stage.subtitle]).join(' ')
  assert.equal(/\b(json|csv|excel|workbook|parser|backend|api|loading)\b/i.test(combinedCopy), false)
})

test('processing animation holds on table preparation until backend completion', () => {
  assert.equal(getProcessingStage(0, false).title, 'Data Received')
  assert.equal(getProcessingStage(500, false).title, 'Processing Data')
  assert.equal(getProcessingStage(1800, false).title, 'Transferring Data')
  assert.equal(getProcessingStage(2300, false).title, 'Preparing Table')
  assert.equal(getProcessingStage(8000, false).title, 'Preparing Table')
  assert.equal(getProcessingStage(8000, true).title, 'Ready to Display')
})

test('processing animation maps elapsed time to data transfer scene phases', () => {
  assert.equal(getProcessingVisualMode(0, false), 'source')
  assert.equal(getProcessingVisualMode(600, false), 'orbit')
  assert.equal(getProcessingVisualMode(1300, false), 'orbit-active')
  assert.equal(getProcessingVisualMode(1900, false), 'transfer')
  assert.equal(getProcessingVisualMode(2400, false), 'grid')
  assert.equal(getProcessingVisualMode(3000, false), 'grid-fill')
  assert.equal(getProcessingVisualMode(8000, false), 'grid-fill')
  assert.equal(getProcessingVisualMode(8000, true), 'success')
})

test('processing completion waits for the ready stage display window', () => {
  assert.equal(isProcessingComplete(3200, true), false)
  assert.equal(isProcessingComplete(3600, true), true)
  assert.equal(isProcessingComplete(8000, false), false)
})
