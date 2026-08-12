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

const JOBS = [
  { ...base, id: 'queued', status: 'queued' },
  { ...base, id: 'running', status: 'running' },
  { ...base, id: 'failed', status: 'failed' },
  { ...base, id: 'cancelled', status: 'cancelled' },
  { ...base, id: 'done', status: 'done' },
] as const

let listJobs: ReturnType<typeof vi.spyOn>

describe('JobsPage', () => {
  beforeEach(() => {
    listJobs = vi.spyOn(client, 'listJobs').mockResolvedValue([...JOBS])
  })

  afterEach(() => vi.restoreAllMocks())

  it('offers Cancel only while a job can still be stopped (queued or running)', async () => {
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByText(/running/)).not.toHaveLength(0))
    expect(screen.getAllByRole('button', { name: 'Cancel' })).toHaveLength(2)
  })

  it('offers Resume only for a failed or cancelled job', async () => {
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByText(/failed/)).not.toHaveLength(0))
    expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(2)
  })

  it('calls the API when Cancel is clicked', async () => {
    const cancel = vi.spyOn(client, 'cancelJob')
      .mockResolvedValue({ ...base, id: 'running', status: 'cancelled' })
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const buttons = await screen.findAllByRole('button', { name: 'Cancel' })
    buttons[0].click()
    await waitFor(() => expect(cancel).toHaveBeenCalled())
  })

  it('calls the API when Resume is clicked', async () => {
    const resume = vi.spyOn(client, 'resumeJob')
      .mockResolvedValue({ ...base, id: 'new', status: 'queued' })
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const buttons = await screen.findAllByRole('button', { name: 'Resume' })
    buttons[0].click()
    await waitFor(() => expect(resume).toHaveBeenCalledWith('failed'))
  })

  it('refreshes the job list once Cancel resolves', async () => {
    vi.spyOn(client, 'cancelJob').mockResolvedValue({ ...base, id: 'running', status: 'cancelled' })
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const before = listJobs.mock.calls.length
    const buttons = await screen.findAllByRole('button', { name: 'Cancel' })
    buttons[0].click()
    await waitFor(() => expect(listJobs.mock.calls.length).toBeGreaterThan(before))
  })

  it('refreshes the job list once Resume resolves', async () => {
    vi.spyOn(client, 'resumeJob').mockResolvedValue({ ...base, id: 'new', status: 'queued' })
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const before = listJobs.mock.calls.length
    const buttons = await screen.findAllByRole('button', { name: 'Resume' })
    buttons[0].click()
    await waitFor(() => expect(listJobs.mock.calls.length).toBeGreaterThan(before))
  })

  it('surfaces a Cancel failure (e.g. the job just finished) instead of silently doing nothing', async () => {
    vi.spyOn(client, 'cancelJob').mockRejectedValue(new Error('409: job is already done'))
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const buttons = await screen.findAllByRole('button', { name: 'Cancel' })
    buttons[0].click()
    expect(await screen.findByText(/409/)).toBeInTheDocument()
  })

  it('surfaces a Resume failure (e.g. the job is still running) instead of silently doing nothing', async () => {
    vi.spyOn(client, 'resumeJob').mockRejectedValue(new Error('409: only failed or cancelled jobs resume'))
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const buttons = await screen.findAllByRole('button', { name: 'Resume' })
    buttons[0].click()
    expect(await screen.findByText(/409/)).toBeInTheDocument()
  })

  it('still refreshes the list after a failed Cancel, so the row reflects reality', async () => {
    vi.spyOn(client, 'cancelJob').mockRejectedValue(new Error('409: job is already done'))
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const before = listJobs.mock.calls.length
    const buttons = await screen.findAllByRole('button', { name: 'Cancel' })
    buttons[0].click()
    await waitFor(() => expect(listJobs.mock.calls.length).toBeGreaterThan(before))
  })
})
