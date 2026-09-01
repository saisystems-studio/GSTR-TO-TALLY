import { useEffect, useState } from 'react'
import GstTallyImport from './pages/GstTallyImport.jsx'
import Login from './pages/Login.jsx'
import { clearSession, signOut, validateSession } from './services/authApi.js'

export default function App() {
  const [authState, setAuthState] = useState({ status: 'loading', user: null })

  useEffect(() => {
    const sessionExpired = () => {
      setAuthState({ status: 'anonymous', user: null })
      window.history.replaceState({}, '', '/login')
    }
    window.addEventListener('gst-session-expired', sessionExpired)
    validateSession().then(session => {
      const user = session?.user || null
      setAuthState(user ? { status: 'authenticated', user } : { status: 'anonymous', user: null })
      window.history.replaceState({}, '', user ? '/gst-tally' : '/login')
    }).catch(() => {
      clearSession()
      setAuthState({ status: 'anonymous', user: null })
      window.history.replaceState({}, '', '/login')
    })
    return () => window.removeEventListener('gst-session-expired', sessionExpired)
  }, [])

  const login = user => {
    window.history.pushState({}, '', '/gst-tally')
    setAuthState({ status: 'authenticated', user: user || null })
  }
  const logout = async () => {
    await signOut()
    window.history.pushState({}, '', '/login')
    setAuthState({ status: 'anonymous', user: null })
  }

  if (authState.status === 'loading') {
    return <main className="auth-loading" aria-label="Validating session"><span>Loading secure session...</span></main>
  }
  return authState.status === 'authenticated'
    ? <GstTallyImport user={authState.user} onLogout={logout} />
    : <Login onLogin={login} />
}
