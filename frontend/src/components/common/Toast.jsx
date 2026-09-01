import { useEffect } from 'react'

const icons = { success: '✓', warning: '⚠', error: '✕', info: 'ℹ' }

export default function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast || toast.kind === 'error') return
    const timer = window.setTimeout(onDismiss, 4000)
    return () => window.clearTimeout(timer)
  }, [toast, onDismiss])

  if (!toast) return null
  const kind = toast.kind || 'success'
  return <div className={`toast toast-${kind}`} role="status">
    <span className="toast-icon" aria-hidden="true">{icons[kind] || icons.success}</span>
    <span className="toast-message">{toast.message}</span>
    <button type="button" className="toast-close" aria-label="Dismiss notification" onClick={onDismiss}>×</button>
  </div>
}
