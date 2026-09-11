import { formatDate } from '../../utils/date'

export default function DuplicateFileToast({ notice, onClose, onChooseAnother }) {
  if (!notice) return null

  return <aside className="duplicate-file-toast" role="alert" aria-live="assertive" aria-label="Duplicate file detected">
    <span className="duplicate-file-toast-icon" aria-hidden="true">!</span>
    <div className="duplicate-file-toast-content">
      <h2>{notice.title}</h2>
      <p>{notice.message}</p>
      <dl>
        <div><dt>Previous Import:</dt><dd>{notice.batchId ? `Batch #${notice.batchId}` : '-'}</dd></div>
        <div><dt>Imported Date:</dt><dd>{notice.importedDate ? formatDate(notice.importedDate) : '-'}</dd></div>
      </dl>
      <button type="button" className="duplicate-file-choose" onClick={onChooseAnother}>Choose Another File</button>
    </div>
    <button type="button" className="duplicate-file-close" aria-label="Close duplicate file notification" onClick={onClose}>×</button>
  </aside>
}
