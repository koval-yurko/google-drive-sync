import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getDownloads, runAction } from '../api/client'
import type { ActionSpec, Downloads, Job } from '../api/types'
import { InflightTable } from '../components/InflightTable'
import { JobProgress } from '../components/JobProgress'
import { ParamsForm, toPayload, type ParamValues } from '../components/ParamsForm'

const POLL_MS = 1000

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  const { actionId } = useParams()
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloads, setDownloads] = useState<Downloads | null>(null)
  const [values, setValues] = useState<ParamValues>({})

  useEffect(() => {
    setValues({})
    setJobId(null)
    setJob(null)
  }, [actionId])

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
      const started = await runAction(action.id, toPayload(action.schema, values))
      setJobId(started.id)
      setJob(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  // A dry run's plan lives keyed under its own run_id, and confirming must
  // act on exactly that plan — never a fresh one the server would mint on
  // an unset run_id. So this offers to confirm only the flow run that just
  // finished, and reposts using its run_id and the params it actually ran
  // with, rather than whatever is currently sitting in the form.
  const canConfirm =
    action.group === 'flow' &&
    job !== null &&
    job.action === action.id &&
    job.status === 'done' &&
    job.params?.confirm !== true

  const confirmPlan = async () => {
    if (!job) return
    setBusy(true)
    setError(null)
    try {
      const confirmed = await runAction(action.id, {
        ...job.params,
        confirm: true,
        run_id: job.run_id,
      })
      setJobId(confirmed.id)
      setJob(null)
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
      {canConfirm && (
        <button onClick={confirmPlan} disabled={busy}>
          Confirm this plan
        </button>
      )}
      {error && <p className="error">{error}</p>}
      {jobId && <JobProgress jobId={jobId} onUpdate={setJob} />}
      {downloads && <InflightTable downloads={downloads} />}
    </>
  )
}
