import { useCallback, useEffect, useState } from 'react'
import { applySessionFromUrl, clearSessionId, getJson, getSessionId } from './api'
import Login from './components/Login'
import TimesheetApp from './components/TimesheetApp'

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)

  const checkAuth = useCallback(async () => {
    if (!getSessionId()) {
      setAuthenticated(false)
      return
    }
    try {
      const status = await getJson<{ authenticated: boolean }>('/api/auth/status')
      setAuthenticated(status.authenticated)
      if (!status.authenticated) clearSessionId()
    } catch {
      setAuthenticated(false)
      clearSessionId()
    }
  }, [])

  useEffect(() => {
    applySessionFromUrl()
    void checkAuth()
  }, [checkAuth])

  if (authenticated === null) {
    return <main className="shell loading">Проверяем сессию TFS…</main>
  }

  if (!authenticated) {
    return <Login onSuccess={() => setAuthenticated(true)} />
  }

  return <TimesheetApp onLogout={() => setAuthenticated(false)} />
}
