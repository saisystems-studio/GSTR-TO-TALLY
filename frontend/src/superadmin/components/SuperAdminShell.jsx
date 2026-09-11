import { useEffect, useState } from 'react'
import SuperAdminSidebar from './SuperAdminSidebar.jsx'

const TITLES = {
  dashboard: 'Dashboard', customers: 'Customers', licenses: 'Licenses',
  payments: 'Payments', 'device-requests': 'Device Requests',
  'audit-logs': 'Audit Logs', settings: 'Settings', profile: 'Profile',
}

export default function SuperAdminShell({ activeSection, onNavigate, onLogout, displayName, children }) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem('superadmin_sidebar_collapsed') === 'true'
  })
  const initials = (displayName || 'SA').slice(0, 2).toUpperCase()

  useEffect(() => {
    window.localStorage.setItem('superadmin_sidebar_collapsed', String(sidebarCollapsed))
  }, [sidebarCollapsed])

  const toggleSidebar = () => {
    if (window.matchMedia('(max-width: 900px)').matches) {
      setMobileSidebarOpen(open => !open)
      return
    }
    setSidebarCollapsed(collapsed => !collapsed)
  }

  const handleNavigate = key => {
    onNavigate(key)
    setMobileSidebarOpen(false)
  }

  return (
    <div className={`sa-root sa-workspace ${sidebarCollapsed ? 'is-sidebar-collapsed' : ''}`}>
      <SuperAdminSidebar
        activeSection={activeSection}
        onNavigate={handleNavigate}
        onLogout={onLogout}
        open={mobileSidebarOpen}
        collapsed={sidebarCollapsed}
        displayName={displayName}
      />
      {mobileSidebarOpen && <button type="button" className="sa-mobile-sidebar-backdrop" onClick={() => setMobileSidebarOpen(false)} aria-label="Close menu" />}
      <div className="sa-main">
        <header className="sa-topbar">
          <div className="sa-topbar-left">
            <button type="button" className="sa-hamburger" onClick={toggleSidebar} aria-label="Toggle sidebar">☰</button>
            <div>
              <h1>{TITLES[activeSection] || 'Super Admin'}</h1>
              <span>Secure commercial administration</span>
            </div>
          </div>
          <div className="sa-profile-chip">
            <span className="sa-avatar">{initials}</span>
            <div>
              <strong>{displayName || 'Super Admin'}</strong>
              <small>SUPER_ADMIN</small>
            </div>
          </div>
        </header>
        <div className="sa-content">{children}</div>
      </div>
    </div>
  )
}
