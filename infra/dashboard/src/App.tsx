import { BrowserRouter, Link, Outlet, Route, Routes } from 'react-router-dom'
import Logo from './components/Logo'
import { IS_LOCAL } from './lib/api'
import { AuthProvider, RequireAuth } from './lib/auth'
import { useAuth } from './lib/auth-context'
import Login from './pages/Login'
import RunsList from './pages/RunsList'
import RunDetail from './pages/RunDetail'
import TrajectoryView from './pages/TrajectoryView'

function AppLayout() {
  const { user, logout } = useAuth()
  return (
    <>
      <header className="topbar">
        <Link className="brand" to="/" aria-label="CUA Worlds home">
          <Logo size={22} />
          <span className="brand-name">
            cua<span className="dim">worlds</span>
          </span>
        </Link>
        <span className="topbar-right muted">
          {IS_LOCAL ? (
            <span className="pill">local</span>
          ) : (
            <>
              {user?.username}
              <button className="link-btn" onClick={logout}>
                sign out
              </button>
            </>
          )}
        </span>
      </header>
      <Outlet />
    </>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<RunsList />} />
            <Route path="/r/:runId" element={<RunDetail />} />
            <Route path="/r/:runId/t/:taskId" element={<TrajectoryView />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
