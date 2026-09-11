import { useEffect, useState } from 'react'
import BackButton from './BackButton'
import Sidebar from './Sidebar'
import UserMenu from './UserMenu'
import SubscriptionBanner, { SubscriptionCompactInfo } from './SubscriptionBanner.jsx'

const COLLAPSE_STORAGE_KEY = 'gstr2tally_sidebar_collapsed'

function loadCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export default function AppShell({ user, activeStep, stepNumber, stepLabel, stepSubtitle, batchId, reachableIndex, onHome, onNavigate, onLogout, onProfile, profileActive, canGoBack, onBack, subscription, children }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(loadCollapsed)

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0')
    } catch {
      // ignore -- persistence is a convenience, not a requirement
    }
  }, [collapsed])

  // Same button toggles expand<->collapse on desktop and open<->close on
  // mobile at once -- only one of those two effects is ever visible at the
  // current viewport (gated purely by CSS @media queries), so flipping both
  // booleans together is safe and keeps this one handler viewport-agnostic.
  // Lives inside the sidebar itself (desktop) and, redundantly but
  // harmlessly, also in the header (the only way to reopen a closed mobile
  // drawer, which is invisible and thus can't hold its own opener then).
  const toggleSidebar = () => {
    setMobileOpen(value => !value)
    setCollapsed(value => !value)
  }
  const closeMobileOnly = () => setMobileOpen(false)

  return <div className="app-workspace">
    <Sidebar
      mobileOpen={mobileOpen}
      collapsed={collapsed}
      onCloseMobile={closeMobileOnly}
      onToggle={toggleSidebar}
      activeStep={activeStep}
      reachableIndex={reachableIndex}
      onNavigate={onNavigate}
      onHome={onHome}
      profileActive={profileActive}
      onProfile={onProfile}
      onLogout={onLogout}
    />
    <main className="app-main">
      <SubscriptionBanner subscription={subscription} />
      <header className="top-header">
        <div className="top-header-left">
          <button type="button" className="hamburger-btn mobile-hamburger-btn" aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'} aria-expanded={!collapsed} onClick={toggleSidebar}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
          {canGoBack && <BackButton onClick={onBack} />}
          <div className="top-header-titles">
            <span className="top-header-title">{stepNumber ? `Step ${stepNumber} — ${stepLabel}` : stepLabel || 'GSTR 2 Tally'}</span>
            {stepSubtitle && <span className="top-header-subtitle">{stepSubtitle}</span>}
          </div>
        </div>
        <div className="top-header-right">
          {batchId && <span className="top-header-batch">Batch #{batchId}</span>}
          <SubscriptionCompactInfo subscription={subscription} />
          <button type="button" className="notification-btn" aria-label="Notifications">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></svg>
          </button>
          <UserMenu user={user} onLogout={onLogout} />
        </div>
      </header>
      <section className="workflow-shell">{children}</section>
    </main>
  </div>
}
