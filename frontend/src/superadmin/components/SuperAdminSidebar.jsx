const MENU_ITEMS = [
  { key: 'dashboard', label: 'Dashboard', icon: 'DB' },
  { key: 'customers', label: 'Customers', icon: 'CU' },
  { key: 'licenses', label: 'Licenses', icon: 'LK' },
  { key: 'device-requests', label: 'Device Requests', icon: 'DR' },
  { key: 'payments', label: 'Payments', icon: 'PY' },
  { key: 'audit-logs', label: 'Audit Logs', icon: 'AL' },
  { key: 'settings', label: 'Settings', icon: 'SE' },
]

export default function SuperAdminSidebar({ activeSection, onNavigate, onLogout, open, collapsed, displayName }) {
  return (
    <aside className={`sa-sidebar ${open ? 'is-open' : ''} ${collapsed ? 'is-collapsed' : ''}`}>
      <div className="sa-sidebar-brand">
        <div className="sa-logo-mark">G2</div>
        <div className="sa-sidebar-brand-copy">
          <div className="sa-brand-name">GSTR 2 Tally</div>
          <h2>Super Admin</h2>
        </div>
      </div>
      <nav className="sa-sidebar-nav">
        {MENU_ITEMS.map(item => (
          <button key={item.key} type="button"
                  className={`sa-sidebar-item ${activeSection === item.key ? 'is-active' : ''}`}
                  onClick={() => onNavigate(item.key)}
                  title={item.label}>
            <span className="sa-nav-icon">{item.icon}</span>
            <span className="sa-sidebar-label">{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="sa-sidebar-footer">
        <button type="button" className={`sa-sidebar-item ${activeSection === 'profile' ? 'is-active' : ''}`} onClick={() => onNavigate('profile')} title="Super Admin">
          <span className="sa-nav-icon">ME</span>
          <span className="sa-sidebar-label">{displayName || 'Super Admin'}</span>
        </button>
        <button type="button" className="sa-sidebar-item" onClick={onLogout} title="Logout">
          <span className="sa-nav-icon">LO</span>
          <span className="sa-sidebar-label">Logout</span>
        </button>
      </div>
    </aside>
  )
}
