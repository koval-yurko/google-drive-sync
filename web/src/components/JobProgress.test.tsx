import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { JobProgress } from './JobProgress'
import type { Job } from '../api/types'

const job: Job = {
  id: 'j1', action: 'sync_archives', params: {}, status: 'running',
  progress: 0.5, message: 'uploading', error: null,
  created_at: '', started_at: null, finished_at: null,
  run_id: 'r1', resumed_from: null,
  phase: 'Upload (5/5)', items_done: 412, items_total: 842,
}

const getJob = vi.fn(async (_id: string) => job)
const getJobEvents = vi.fn(async (_id: string) => [])
const streamJob = vi.fn(() => () => undefined)

vi.mock('../api/client', () => ({
  getJob: (id: string) => getJob(id),
  getJobEvents: (id: string) => getJobEvents(id),
  streamJob: (...args: unknown[]) => streamJob(...(args as [])),
}))

afterEach(() => vi.clearAllMocks())

describe('JobProgress', () => {
  it('shows the phase and item counts', async () => {
    getJob.mockResolvedValue(job)
    render(<JobProgress jobId="j1" />)
    expect(await screen.findByText(/Upload \(5\/5\)/)).toBeInTheDocument()
    expect(screen.getByText(/412 \/ 842/)).toBeInTheDocument()
  })

  it('omits item counts when nothing was enumerated', async () => {
    getJob.mockResolvedValue({ ...job, items_total: 0 })
    render(<JobProgress jobId="j1" />)
    await screen.findByText(/Upload \(5\/5\)/)
    expect(screen.queryByText(/\d+ \/ \d+/)).not.toBeInTheDocument()
  })

  it('shows the run_id, so a dry run can be confirmed against exactly it', async () => {
    getJob.mockResolvedValue(job)
    render(<JobProgress jobId="j1" />)
    expect(await screen.findByText('r1')).toBeInTheDocument()
  })

  it('reports every fetched job to onUpdate', async () => {
    getJob.mockResolvedValue(job)
    const onUpdate = vi.fn()
    render(<JobProgress jobId="j1" onUpdate={onUpdate} />)
    await screen.findByText(/Upload \(5\/5\)/)
    expect(onUpdate).toHaveBeenCalledWith(job)
  })
})
