import BackButton from './BackButton'
import UserMenu from './UserMenu'

export default function AppShell({ user, onHome, onLogout, canGoBack, onBack, children }) {
  return <main className="app-workspace">
    <header className="top-header">
      <div className="top-header-left">
        {canGoBack && <BackButton onClick={onBack} />}
        <button className="brand-button" onClick={onHome} aria-label="Go to GSTR Import">
          <span className="brand-mark">GT</span>
          <strong>GSTR 2 Tally</strong>
        </button>
      </div>
      <UserMenu user={user} onLogout={onLogout} />
    </header>
    <section className="workflow-shell">{children}</section>
  </main>
}
