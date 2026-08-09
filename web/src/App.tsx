import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { listActions } from './api/client'
import type { ActionSpec } from './api/types'
import { Nav } from './components/Nav'
import { ActionPage } from './pages/ActionPage'
import { JobsPage } from './pages/JobsPage'
import { SettingsPage } from './pages/SettingsPage'

export default function App() {
  const [actions, setActions] = useState<ActionSpec[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listActions().then(setActions).catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="layout">
      <Nav actions={actions} />
      <main>
        {error && <p className="error">{error}</p>}
        <Routes>
          <Route path="/" element={<Navigate to="/settings" replace />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/actions/:actionId" element={<ActionPage actions={actions} />} />
          <Route path="/jobs" element={<JobsPage />} />
        </Routes>
      </main>
    </div>
  )
}
