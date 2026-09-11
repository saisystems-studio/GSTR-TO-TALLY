import { useEffect, useState } from 'react'
import GstTallyImport from './pages/GstTallyImport.jsx'
import Login from './pages/Login.jsx'
import SubscriptionExpired from './pages/SubscriptionExpired.jsx'
import SuperAdminApp from './superadmin/SuperAdminApp.jsx'
import { clearSession, signOut, validateSession } from './services/authApi.js'
import { getMySubscription } from './services/subscriptionApi.js'

export default function App() {
  return window.location.pathname.startsWith('/superadmin') ? <SuperAdminApp /> : <CustomerApp />
}

function CustomerApp() {
  const [authState, setAuthState] = useState({ status: 'loading', user: null })
  const [loginNotice, setLoginNotice] = useState('')
  // Fetched once per session (spec section 19) -- null just means "not
  // known yet"/"couldn't be read", which never blocks anything on its own;
  // only an actual EXPIRED response (here, or from the subscription-blocked
  // event below) switches the app to the expired screen. This keeps a
  // hiccup in this brand-new endpoint from taking down the existing
  // Login -> Upload -> ... flow for every other status.
  const [subscription, setSubscription] = useState(null)

  useEffect(() => {
    let cancelled = false
    const isProtectedRoute = /^\/gst-tally(?:\/.*)?$/.test(window.location.pathname)
    const sessionExpired = event => {
      if (cancelled) return
      const message = event?.detail?.message
      if (message) setLoginNotice(message)
      setAuthState({ status: 'anonymous', user: null })
      if (isProtectedRoute) window.history.replaceState({}, '', '/login')
    }

    const fallbackTimer = window.setTimeout(() => {
      if (cancelled) return
      setAuthState({ status: 'anonymous', user: null })
      if (isProtectedRoute) window.history.replaceState({}, '', '/login')
    }, 8000)

    window.addEventListener('gst-session-expired', sessionExpired)
    validateSession().then(session => {
      if (cancelled) return
      const user = session?.user || null
      const nextState = user ? { status: 'authenticated', user } : { status: 'anonymous', user: null }
      setAuthState(nextState)
      if (user) {
        if (!/^\/gst-tally(?:\/.*)?$/.test(window.location.pathname)) {
          window.history.replaceState({}, '', '/gst-tally')
        }
      } else {
        window.history.replaceState({}, '', '/login')
      }
    }).catch(() => {
      if (cancelled) return
      clearSession()
      setAuthState({ status: 'anonymous', user: null })
      if (isProtectedRoute) window.history.replaceState({}, '', '/login')
    }).finally(() => {
      if (!cancelled) window.clearTimeout(fallbackTimer)
    })

    return () => {
      cancelled = true
      window.clearTimeout(fallbackTimer)
      window.removeEventListener('gst-session-expired', sessionExpired)
    }
  }, [])

  // Read once as soon as the session is confirmed (covers both a fresh
  // login and a page reload that resumes an existing session) -- never
  // polled, since the enforcement middleware itself is the real-time check
  // on every protected request; this is only for the banner/expired screen.
  useEffect(() => {
    if (authState.status !== 'authenticated') return
    let cancelled = false
    getMySubscription().then(result => { if (!cancelled) setSubscription(result) }).catch(() => {})
    return () => { cancelled = true }
  }, [authState.status])

  // Mirrors the existing 'gst-session-expired' listener above -- any
  // protected GSTR 2 Tally call the middleware blocks (see
  // gstTallyApi.js's request()) fires this immediately, so the app switches
  // to the expired screen the instant it happens, not only after the next
  // manual subscription-status fetch.
  useEffect(() => {
    const onBlocked = event => {
      setSubscription(previous => ({
        ...(previous || {}),
        subscription_status: event?.detail?.code === 'SUBSCRIPTION_SUSPENDED' ? 'SUSPENDED' : 'EXPIRED',
        is_expired: event?.detail?.code === 'SUBSCRIPTION_EXPIRED',
        expiry_date: event?.detail?.expiry_date || previous?.expiry_date,
      }))
    }
    window.addEventListener('gst-subscription-blocked', onBlocked)
    return () => window.removeEventListener('gst-subscription-blocked', onBlocked)
  }, [])

  const login = user => {
    setLoginNotice('')
    setSubscription(null)
    setAuthState({ status: 'authenticated', user: user || null })
    if (window.location.pathname !== '/gst-tally') {
      window.history.pushState({}, '', '/gst-tally')
    }
  }
  const logout = async () => {
    await signOut()
    setAuthState({ status: 'anonymous', user: null })
    setSubscription(null)
    window.history.pushState({}, '', '/login')
  }

  if (authState.status === 'loading') {
    return <main className="auth-loading" aria-label="Validating session"><span>Loading secure session...</span></main>
  }

  if (authState.status !== 'authenticated') {
    return <Login onLogin={login} notice={loginNotice} />
  }
  // Suspended reads the same as expired here (spec section 7: neither goes
  // to the normal dashboard) -- the screen's own copy always shows the real
  // expiry_date regardless of which of the two blocked it.
  if (subscription?.subscription_status === 'EXPIRED' || subscription?.subscription_status === 'SUSPENDED') {
    return <SubscriptionExpired user={authState.user} subscription={subscription} onLogout={logout} />
  }
  return <GstTallyImport user={authState.user} onLogout={logout} subscription={subscription} />
}
