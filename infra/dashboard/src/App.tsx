import { BrowserRouter, Outlet, Route, Routes } from 'react-router-dom'
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
        <span className="brand">CUA Worlds</span>
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
