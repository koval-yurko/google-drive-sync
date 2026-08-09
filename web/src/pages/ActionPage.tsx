import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { runAction } from '../api/client'
import type { ActionSpec } from '../api/types'
import { JobProgress } from '../components/JobProgress'

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  const { actionId } = useParams()
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const action = actions.find((a) => a.id === actionId)
  if (!action) return <p>Unknown action: {actionId}</p>

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const job = await runAction(action.id, {})
      setJobId(job.id)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const hasParams = Object.keys(action.schema.properties ?? {}).length > 0

  return (
    <>
      <h2>{action.title}</h2>
      <p>{action.description}</p>
      {hasParams && (
        <pre className="log">{JSON.stringify(action.schema, null, 2)}</pre>
      )}
      <button onClick={start} disabled={busy}>
        {busy ? 'Starting…' : 'Run'}
      </button>
      {error && <p className="error">{error}</p>}
      {jobId && <JobProgress jobId={jobId} />}
    </>
  )
}
