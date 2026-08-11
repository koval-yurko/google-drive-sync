import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getDownloads, runAction } from '../api/client'
import type { ActionSpec, Downloads } from '../api/types'
import { InflightTable } from '../components/InflightTable'
import { JobProgress } from '../components/JobProgress'
import { ParamsForm, toPayload, type ParamValues } from '../components/ParamsForm'

const POLL_MS = 1000

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  const { actionId } = useParams()
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloads, setDownloads] = useState<Downloads | null>(null)
  const [values, setValues] = useState<ParamValues>({})

  useEffect(() => setValues({}), [actionId])

  const watching = actionId === 'organize' && jobId !== null

  useEffect(() => {
    if (!watching) return
    let stopped = false
    let timer: ReturnType<typeof setInterval>

    const poll = async () => {
      const current = await getDownloads()
      if (stopped) return
      setDownloads(current)
      // The backend closes the run folder when the run ends, so this is the
      // run telling us it is over — no need to ask the job.
      if (current.run_dir === null) {
        stopped = true
        clearInterval(timer)
      }
    }

    poll()
    timer = setInterval(poll, POLL_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [watching])

  const action = actions.find((a) => a.id === actionId)
  if (!action) return <p>Unknown action: {actionId}</p>

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const job = await runAction(action.id, toPayload(action.schema, values))
      setJobId(job.id)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h2>{action.title}</h2>
      <p>{action.description}</p>
      <ParamsForm schema={action.schema} values={values} onChange={setValues} />
      <button onClick={start} disabled={busy}>
        {busy ? 'Starting…' : 'Run'}
      </button>
      {error && <p className="error">{error}</p>}
      {jobId && <JobProgress jobId={jobId} />}
      {downloads && <InflightTable downloads={downloads} />}
    </>
  )
}
