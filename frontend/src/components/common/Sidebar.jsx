import { useEffect } from 'react'
import AppBrand from './AppBrand'

const icons = {
  home: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 11l9-8 9 8" /><path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10" /></svg>,
  upload: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></svg>,
  preview: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" /></svg>,
  parties: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 12l2 2 4-4" /><circle cx="12" cy="12" r="9" /></svg>,
  masters: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16M4 12h16M4 18h10" /></svg>,
  vouchers: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 4h9l5 5v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z" /><path d="M9 13h6M9 17h6" /></svg>,
  import: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 4v12M7 11l5 5 5-5" /><path d="M4 20h16" /></svg>,
  profile: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 4-6 8-6s8 2 8 6" /></svg>,
  logout: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></svg>,
}

const ChevronLeft = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
const ChevronRight = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round"><path d="M9 6l6 6-6 6" /></svg>

const MENU_ITEMS = [
  { key: 'upload', label: 'Upload', icon: icons.upload },
  { key: 'preview', label: 'Invoice Preview', icon: icons.preview },
  { key: 'parties', label: 'Party Details', icon: icons.parties },
  { key: 'masters', label: 'Tally Masters', icon: icons.masters },
  { key: 'vouchers', label: 'Voucher Preview', icon: icons.vouchers },
  { key: 'import', label: 'Import to Tally', icon: icons.import },
]

// A step ahead of wherever the workflow has actually reached can't render
// yet -- its screen depends on data an earlier step produces (masters need a
// batch, vouchers need masters, ...). Those items stay visible but disabled
// (see reachableIndex from GstTallyImport's stepOrder) rather than hidden,
// so the full menu is always shown while nothing is ever navigated to before
// its data exists -- no change to the underlying step workflow itself.
const STEP_INDEX = { upload: 0, preview: 1, parties: 2, masters: 3, vouchers: 4, import: 5 }

// mobileOpen (ephemeral drawer state) and collapsed (desktop 250px/90px
// state, persisted in AppShell via localStorage) are independent booleans --
// which one has any visual effect at all is decided purely by CSS @media
// queries (see gst-tally.css), never here, so this component never needs to
// know the current viewport.
export default function Sidebar({ mobileOpen, collapsed, onCloseMobile, onToggle, activeStep, reachableIndex, onNavigate, onHome, profileActive, onProfile, onLogout }) {
  useEffect(() => {
    if (!mobileOpen) return
    const onKeyDown = event => { if (event.key === 'Escape') onCloseMobile() }
    document.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [mobileOpen, onCloseMobile])

  const select = item => {
    if (STEP_INDEX[item.key] > reachableIndex) return
    onNavigate(item.key)
    onCloseMobile()
  }

  return <>
    {mobileOpen && <div className="sidebar-overlay" onClick={onCloseMobile} aria-hidden="true" />}
    <aside className={`app-sidebar ${mobileOpen ? 'open' : ''} ${collapsed ? 'collapsed' : ''}`}>
      <button type="button" className="sidebar-edge-toggle" aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'} aria-expanded={!collapsed} onClick={onToggle}>
        {collapsed ? ChevronRight : ChevronLeft}
      </button>

      <div className="sidebar-header">
        <button
          type="button"
          className="sidebar-brand-button"
          aria-label="Go to upload page"
          onClick={() => { onNavigate('upload'); onCloseMobile() }}
          style={{ background: 'transparent', border: 'none', padding: 0, margin: 0, cursor: 'pointer', width: '100%', textAlign: 'left' }}
        >
          <AppBrand />
        </button>
      </div>

      <nav className="sidebar-nav" aria-label="Main navigation">
        {MENU_ITEMS.map(item => {
          const isActive = !profileActive && item.key === activeStep
          const disabled = item.key !== 'home' && STEP_INDEX[item.key] > reachableIndex
          return <button
            key={item.key}
            type="button"
            className={`sidebar-item ${isActive ? 'active' : ''}`}
            disabled={disabled}
            aria-current={isActive ? 'page' : undefined}
            onClick={() => select(item)}
          >
            <span className="sidebar-item-icon">{item.icon}</span>
            <span className="sidebar-item-label">{item.label}</span>
            <span className="sidebar-tooltip" role="tooltip">{item.label}</span>
          </button>
        })}
      </nav>

      <nav className="sidebar-nav sidebar-nav-bottom" aria-label="Account navigation">
        <button
          type="button"
          className={`sidebar-item ${profileActive ? 'active' : ''}`}
          aria-current={profileActive ? 'page' : undefined}
          onClick={() => { onProfile(); onCloseMobile() }}
        >
          <span className="sidebar-item-icon">{icons.profile}</span>
          <span className="sidebar-item-label">Profile</span>
          <span className="sidebar-tooltip" role="tooltip">Profile</span>
        </button>
        <button type="button" className="sidebar-item" onClick={onLogout}>
          <span className="sidebar-item-icon">{icons.logout}</span>
          <span className="sidebar-item-label">Logout</span>
          <span className="sidebar-tooltip" role="tooltip">Logout</span>
        </button>
      </nav>
    </aside>
  </>
}
