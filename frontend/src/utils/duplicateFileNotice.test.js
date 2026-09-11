import test from 'node:test'
import assert from 'node:assert/strict'

import { duplicateFileNoticeFromError, chooseAnotherFile } from './duplicateFileNotice.js'

test('duplicate upload errors become a non-blocking previously processed file notice', () => {
  const notice = duplicateFileNoticeFromError({
    code: 'DUPLICATE_FILE_ALREADY_IMPORTED',
    previous_import: {
      batch_id: 8,
      imported_date: '2026-09-09T10:15:00Z',
    },
  })

  assert.deepEqual(notice, {
    title: 'Previously Processed File',
    message: 'Already imported vouchers will be skipped automatically. Corrected or eligible records will continue for validation.',
    batchId: 8,
    importedDate: '2026-09-09T10:15:00Z',
  })
})

test('ordinary upload errors do not become duplicate notices', () => {
  assert.equal(duplicateFileNoticeFromError({ code: 'INVALID_FILE', message: 'Bad file' }), null)
})

test('choosing another file dismisses the notice, clears the file, and opens the picker', () => {
  const events = []

  chooseAnotherFile({
    dismiss: () => events.push('dismiss'),
    clearFile: () => events.push('clear'),
    openPicker: () => events.push('open'),
  })

  assert.deepEqual(events, ['dismiss', 'clear', 'open'])
})
