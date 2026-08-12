import { useEffect, useRef, useState } from 'react'
import { getJob, getJobEvents, streamJob } from '../api/client'
import type { Job, JobEvent } from '../api/types'

interface Props {
  jobId: string
  /** Called with the freshest Job on every poll/stream update, so a parent
   *  that needs to react once the job finishes (e.g. offering to confirm
   *  the plan it just reported) doesn't have to duplicate this fetching. */
  onUpdate?: (job: Job) => void
}

export function JobProgress({ jobId, onUpdate }: Props) {
  const [job, setJob] = useState<Job | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let stopped = false

    const refresh = async () => {
      const [current, log] = await Promise.all([getJob(jobId), getJobEvents(jobId)])
      if (stopped) return
      setJob(current)
      setEvents(log)
      onUpdate?.(current)
    }

    refresh()
    const close = streamJob(jobId, refresh, refresh)
    return () => {
      stopped = true
      close()
    }
  }, [jobId, onUpdate])

  useEffect(() => {
    // scrollTop assignment rather than scrollTo(): the same autoscroll, but
    // jsdom implements the property and not the method.
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [events])

  if (!job) return <p>Starting…</p>

  return (
    <div className="card">
      <p>
        Status: <strong>{job.status}</strong>
        {job.message && ` — ${job.message}`}
      </p>
      {job.run_id && (
        <p className="job-run-id">
          Run: <code>{job.run_id}</code>
        </p>
      )}
      <progress value={job.progress} max={1} />
      {job.phase && (
        <p className="job-phase">
          {job.phase}
          {job.items_total > 0 && ` · ${job.items_done} / ${job.items_total}`}
        </p>
      )}
      {job.error && <pre className="error">{job.error}</pre>}
      <div className="log" ref={logRef}>
        {events.map((event) => (
          <div key={event.id} className={event.level}>
            {event.message}
          </div>
        ))}
      </div>
    </div>
  )
}
