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
    ['Processing Data', 'Reading and organizing records'],
    ['Structuring Records', 'Arranging fields and values'],
    ['Preparing Table', 'Building the preview grid'],
    ['Ready to Display', 'Your preview is ready'],
  ])

  const combinedCopy = PROCESSING_STAGES.flatMap(stage => [stage.title, stage.subtitle]).join(' ')
  assert.equal(/\b(json|csv|excel|workbook|parser|backend|api|loading)\b/i.test(combinedCopy), false)
})

test('processing animation advances quickly but never reports ready before backend completion', () => {
  assert.equal(getProcessingStage(0, false).title, 'Data Received')
  assert.equal(getProcessingStage(300, false).title, 'Processing Data')
  assert.equal(getProcessingStage(700, false).title, 'Structuring Records')
  assert.equal(getProcessingStage(1000, false).title, 'Preparing Table')
  assert.equal(getProcessingStage(8000, false).title, 'Preparing Table')
  assert.equal(getProcessingStage(1, true).title, 'Ready to Display')
})

test('processing animation maps elapsed time to data transfer scene phases', () => {
  assert.equal(getProcessingVisualMode(0, false), 'source')
  assert.equal(getProcessingVisualMode(300, false), 'flow')
  assert.equal(getProcessingVisualMode(500, false), 'flow')
  assert.equal(getProcessingVisualMode(700, false), 'align')
  assert.equal(getProcessingVisualMode(1000, false), 'build')
  assert.equal(getProcessingVisualMode(4000, false), 'build')
  assert.equal(getProcessingVisualMode(8000, false), 'build')
  assert.equal(getProcessingVisualMode(8000, true), 'ready')
})

test('processing completion follows actual backend completion without an artificial wait', () => {
  assert.equal(isProcessingComplete(1, true), true)
  assert.equal(isProcessingComplete(8000, false), false)
})
