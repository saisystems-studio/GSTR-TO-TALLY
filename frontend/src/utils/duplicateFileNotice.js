const DUPLICATE_FILE_CODE = 'DUPLICATE_FILE_ALREADY_IMPORTED'

export function duplicateFileNoticeFromError(error) {
  if (error?.code !== DUPLICATE_FILE_CODE) return null

  const previous = error.previous_import || {}
  return {
    title: 'Previously Processed File',
    message: 'Already imported vouchers will be skipped automatically. Corrected or eligible records will continue for validation.',
    batchId: previous.batch_id ?? null,
    importedDate: previous.imported_date || null,
  }
}

export function chooseAnotherFile({ dismiss, clearFile, openPicker }) {
  dismiss()
  clearFile()
  openPicker()
}
