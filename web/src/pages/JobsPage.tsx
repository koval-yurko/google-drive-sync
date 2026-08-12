import { useEffect, useState } from 'react'
import { cancelJob, listJobs, resumeJob } from '../api/client'
import type { Job } from '../api/types'
import { JobProgress } from '../components/JobProgress'

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => listJobs().then(setJobs).catch((e) => setError(String(e)))

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [])

  async function handleCancel(id: string) {
    try {
      await cancelJob(id)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      refresh()
    }
  }

  async function handleResume(id: string) {
    try {
      await resumeJob(id)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      refresh()
    }
  }

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
                <button onClick={() => setSelected(job.id)}>Details</button>{' '}
                {(job.status === 'queued' || job.status === 'running') && (
                  <button onClick={() => handleCancel(job.id)}>Cancel</button>
                )}
                {(job.status === 'failed' || job.status === 'cancelled') && (
                  <button onClick={() => handleResume(job.id)}>Resume</button>
                )}
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
