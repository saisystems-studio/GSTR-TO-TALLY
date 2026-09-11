import { useEffect, useState } from 'react'
import '../styles/super-admin.css'
import SuperAdminShell from './components/SuperAdminShell.jsx'
import Login from './pages/Login.jsx'
import ForgotPassword from './pages/ForgotPassword.jsx'
import ResetPassword from './pages/ResetPassword.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Customers from './pages/Customers.jsx'
import CustomerDetail from './pages/CustomerDetail.jsx'
import Payments from './pages/Payments.jsx'
import PaymentDetail from './pages/PaymentDetail.jsx'
import AuditLogs from './pages/AuditLogs.jsx'
import ProductLicenses from './pages/ProductLicenses.jsx'
import DeviceRequests from './pages/DeviceRequests.jsx'
import Settings from './pages/Settings.jsx'
import Profile from './pages/Profile.jsx'
import { SA_SESSION_EXPIRED_EVENT, clearSession, fetchMe, hasSession, signOut } from './services/superadminAuthApi.js'

const VALID_SECTIONS = new Set(['dashboard', 'customers', 'licenses', 'device-requests', 'payments', 'audit-logs', 'settings', 'profile'])

// Manual pathname routing under /superadmin/* -- mirrors the customer app's
// own convention in App.jsx/GstTallyImport.jsx (no react-router in this
// project). Never touches any /gst-tally or /login path.
function parseRoute(pathname) {
  const match = pathname.match(/^\/superadmin\/([a-z-]+)(?:\/([^/]+))?\/?$/)
  if (!match) return { section: 'dashboard', param: null }
  if (match[1] === 'change-password') return { section: 'profile', param: null }
  if (match[1] === 'product-licenses') return { section: 'licenses', param: match[2] || null }
  if (!VALID_SECTIONS.has(match[1])) return { section: 'dashboard', param: null }
  return { section: match[1], param: match[2] || null }
}

function pushRoute(section, param) {
  const path = param ? `/superadmin/${section}/${param}` : `/superadmin/${section}`
  if (window.location.pathname !== path) window.history.pushState({}, '', path)
}

export default function SuperAdminApp() {
  const [authState, setAuthState] = useState({ status: 'loading', profile: null })
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname))

  useEffect(() => {
    let cancelled = false
    const onExpired = () => { if (!cancelled) { setAuthState({ status: 'anonymous', profile: null }); pushRoute('login') } }
    window.addEventListener(SA_SESSION_EXPIRED_EVENT, onExpired)
    const onPop = () => setRoute(parseRoute(window.location.pathname))
    window.addEventListener('popstate', onPop)

    if (!hasSession()) {
      setAuthState({ status: 'anonymous', profile: null })
      if (window.location.pathname.startsWith('/superadmin') && route.section !== 'login' && route.section !== 'forgot-password' && route.section !== 'reset-password') {
        pushRoute('login')
      }
    } else {
      fetchMe().then(profile => {
        if (cancelled) return
        setAuthState({ status: 'authenticated', profile })
      }).catch(() => {
        if (cancelled) return
        clearSession()
        setAuthState({ status: 'anonymous', profile: null })
        pushRoute('login')
      })
    }
    return () => { cancelled = true; window.removeEventListener(SA_SESSION_EXPIRED_EVENT, onExpired); window.removeEventListener('popstate', onPop) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const goTo = (section, param) => { pushRoute(section, param); setRoute({ section, param: param || null }) }

  const handleLogin = profile => {
    setAuthState({ status: 'authenticated', profile })
    goTo('dashboard')
  }

  const handleLogout = async () => {
    await signOut()
    setAuthState({ status: 'anonymous', profile: null })
    goTo('login')
  }

  if (authState.status === 'loading') {
    return <div className="sa-root sa-loading-screen">Loading Super Admin session...</div>
  }

  if (authState.status === 'anonymous') {
    if (route.section === 'forgot-password') return <div className="sa-root"><ForgotPassword onBack={() => goTo('login')} /></div>
    if (route.section === 'reset-password') return <div className="sa-root"><ResetPassword onDone={() => goTo('login')} /></div>
    return <div className="sa-root"><Login onLogin={handleLogin} onForgotPassword={() => goTo('forgot-password')} /></div>
  }

  const { section, param } = route
  return (
    <SuperAdminShell activeSection={section} onNavigate={s => goTo(s)} onLogout={handleLogout} displayName={authState.profile?.name || authState.profile?.display_name}>
      {section === 'dashboard' && <Dashboard onNavigate={goTo} />}
      {section === 'customers' && !param && <Customers onOpen={id => goTo('customers', id)} />}
      {section === 'customers' && param && <CustomerDetail userId={param} onBack={() => goTo('customers')} />}
      {section === 'payments' && !param && <Payments onOpen={id => goTo('payments', id)} />}
      {section === 'payments' && param && <PaymentDetail paymentId={param} onBack={() => goTo('payments')} />}
      {section === 'audit-logs' && <AuditLogs />}
      {section === 'licenses' && <ProductLicenses />}
      {section === 'device-requests' && <DeviceRequests />}
      {section === 'settings' && <Settings />}
      {section === 'profile' && <Profile profile={authState.profile} onUpdated={profile => setAuthState(s => ({ ...s, profile }))} />}
    </SuperAdminShell>
  )
}
