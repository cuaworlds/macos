import { FiLogOut, FiUser } from 'react-icons/fi'
import { BrowserRouter, Link, Outlet, Route, Routes } from 'react-router-dom'
import Logo from './components/Logo'
import { IS_LOCAL } from './lib/api'
import { AuthProvider, RequireAuth } from './lib/auth'
import { useAuth } from './lib/auth-context'
import Login from './pages/Login'
import Profile from './pages/Profile'
import RunsList from './pages/RunsList'
import RunDetail from './pages/RunDetail'
import TrajectoryView from './pages/TrajectoryView'

function AppLayout() {
  const { user, logout } = useAuth()
  return (
    <>
      <header className="topbar">
        <Link className="brand" to="/" aria-label="CUA Worlds home">
          <Logo size={26} />
          <span className="brand-name">
            cua<span className="dim">worlds</span>
          </span>
        </Link>
        <span className="topbar-right muted">
          {IS_LOCAL ? (
            <span className="pill">local</span>
          ) : (
            <>
              <Link className="topbar-user" to="/profile" title="Profile">
                <FiUser size={15} />
                {user?.username}
              </Link>
              <button className="icon-btn" onClick={logout} aria-label="Sign out" title="Sign out">
                <FiLogOut size={19} />
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
            <Route path="/profile" element={<Profile />} />
            <Route path="/r/:runId" element={<RunDetail />} />
            <Route path="/r/:runId/t/:taskId" element={<TrajectoryView />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
