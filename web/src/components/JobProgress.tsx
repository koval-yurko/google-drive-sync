import { useEffect, useRef, useState } from 'react'
import { getJob, getJobEvents, streamJob } from '../api/client'
import type { Job, JobEvent } from '../api/types'

export function JobProgress({ jobId }: { jobId: string }) {
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
    }

    refresh()
    const close = streamJob(jobId, refresh, refresh)
    return () => {
      stopped = true
      close()
    }
  }, [jobId])

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
      <progress value={job.progress} max={1} />
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
