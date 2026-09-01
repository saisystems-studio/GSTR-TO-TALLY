import { useEffect, useRef, useState } from 'react'

export default function UserMenu({ user, onLogout }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const name = user?.username || user?.email || 'User'

  useEffect(() => {
    if (!open) return
    const onDocClick = event => { if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false) }
    const onKeyDown = event => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return <div className="user-menu" ref={rootRef}>
    <button type="button" className="user-menu-trigger" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      Hi, {name} <span className="user-menu-caret" aria-hidden="true">▾</span>
    </button>
    {open && <div className="user-menu-panel" role="menu">
      <div className="user-menu-heading">Hi, {name}</div>
      <div className="user-menu-divider" />
      <button type="button" role="menuitem" className="user-menu-item" onClick={() => { setOpen(false); onLogout() }}>Logout</button>
    </div>}
  </div>
}
