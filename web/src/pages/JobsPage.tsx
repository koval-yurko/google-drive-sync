import { useEffect, useState } from 'react'
import { listJobs } from '../api/client'
import type { Job } from '../api/types'
import { JobProgress } from '../components/JobProgress'

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = () => listJobs().then(setJobs).catch((e) => setError(String(e)))
    load()
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [])

  return (
    <>
      <h2>Jobs</h2>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Action</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Started</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>{job.action}</td>
              <td>{job.status}</td>
              <td>{Math.round(job.progress * 100)}%</td>
              <td>{job.started_at ?? '—'}</td>
              <td>
                <button onClick={() => setSelected(job.id)}>Details</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {jobs.length === 0 && <p>No jobs have been run yet.</p>}
      {selected && <JobProgress jobId={selected} />}
    </>
  )
}
