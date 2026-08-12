import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobsPage } from './JobsPage'
import * as client from '../api/client'

const base = {
  action: 'sync_archives', params: {}, progress: 0.5, message: null,
  error: null, created_at: '2026-08-12T10:00:00Z', started_at: null,
  finished_at: null, run_id: 'r1', resumed_from: null, phase: null,
  items_done: 0, items_total: 0,
}

describe('JobsPage', () => {
  beforeEach(() => {
    vi.spyOn(client, 'listJobs').mockResolvedValue([
      { ...base, id: 'running', status: 'running' },
      { ...base, id: 'failed', status: 'failed' },
      { ...base, id: 'done', status: 'done' },
    ])
  })

  afterEach(() => vi.restoreAllMocks())

  it('offers Cancel only while a job can still be stopped', async () => {
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByText(/running/)).not.toHaveLength(0))
    expect(screen.getAllByRole('button', { name: 'Cancel' })).toHaveLength(1)
  })

  it('offers Resume only for a failed or cancelled job', async () => {
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByText(/failed/)).not.toHaveLength(0))
    expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(1)
  })

  it('calls the API when Resume is clicked', async () => {
    const resume = vi.spyOn(client, 'resumeJob')
      .mockResolvedValue({ ...base, id: 'new', status: 'queued' })
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const button = await screen.findByRole('button', { name: 'Resume' })
    button.click()
    await waitFor(() => expect(resume).toHaveBeenCalledWith('failed'))
  })
})
