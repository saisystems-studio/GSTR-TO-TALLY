import { useEffect, useRef, useState } from 'react'

const icons = { success: '✓', warning: '⚠', error: '✕', info: 'ℹ' }

const AUTO_DISMISS_MS = 3000
const FADE_MS = 280

export default function Toast({ toast, onDismiss }) {
  const [content, setContent] = useState(toast)
  const [visible, setVisible] = useState(Boolean(toast))
  // Read through a ref rather than depending on `onDismiss` directly --
  // the parent passes a fresh inline function on every render, and
  // depending on it would restart the auto-dismiss timer on any unrelated
  // parent re-render instead of only when a genuinely new toast arrives
  // (this was why the toast used to stick around: frequent re-renders kept
  // resetting its countdown before it ever reached 3s).
  const onDismissRef = useRef(onDismiss)
  onDismissRef.current = onDismiss

  // A new toast (including one replacing an already-visible one, and
  // including errors) shows immediately and gets its own fresh 3s
  // auto-dismiss timer.
  useEffect(() => {
    if (!toast) return
    setContent(toast)
    setVisible(true)
    const hideTimer = window.setTimeout(() => setVisible(false), AUTO_DISMISS_MS)
    return () => window.clearTimeout(hideTimer)
  }, [toast])

  // Once the timer hides it, wait for the fade-out transition to finish
  // before telling the parent to drop it from state -- removing it right
  // away would cut the fade off mid-animation.
  useEffect(() => {
    if (visible || !content) return
    const removeTimer = window.setTimeout(() => {
      setContent(null)
      onDismissRef.current()
    }, FADE_MS)
    return () => window.clearTimeout(removeTimer)
  }, [visible, content])

  if (!content) return null
  const kind = content.kind || 'success'
  return <div className={`toast toast-${kind}${visible ? '' : ' toast-out'}`} role="status">
    <span className="toast-icon" aria-hidden="true">{icons[kind] || icons.success}</span>
    <span className="toast-message">{content.message}</span>
    <button
      type="button"
      className="toast-close"
      aria-label="Dismiss notification"
      onClick={() => { setContent(null); onDismissRef.current() }}
    >×</button>
  </div>
}
