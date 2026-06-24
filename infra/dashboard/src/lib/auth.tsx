import { useEffect, useState, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { PageLoader } from '../components/Loading'
import { clearTokens, getAccess, IS_LOCAL, login as apiLogin, me, setAuthErrorHandler, type User } from './api'
import { AuthContext, useAuth } from './auth-context'

const LOCAL_USER: User = { id: 0, username: 'local' }

export function AuthProvider({ children }: { children: ReactNode }) {
  // Offline mode bypasses auth: a stand-in user keeps the route guard open.
  const [user, setUser] = useState<User | null>(IS_LOCAL ? LOCAL_USER : null)
  // Start in "loading" only when there's a token to validate.
  const [loading, setLoading] = useState(() => !IS_LOCAL && Boolean(getAccess()))

  useEffect(() => {
    if (IS_LOCAL) return
    setAuthErrorHandler(() => {
      clearTokens()
      setUser(null)
    })
    if (!getAccess()) return
    me()
      .then(setUser)
      .catch(() => {
        clearTokens()
        setUser(null)
      })
      .finally(() => setLoading(false))
  }, [])

  const login = async (username: string, password: string) => {
    await apiLogin(username, password)
    setUser(await me())
  }

  const logout = () => {
    clearTokens()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, setUser }}>{children}</AuthContext.Provider>
  )
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <PageLoader />
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}
