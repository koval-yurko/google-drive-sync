import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionSpec, Job } from '../api/types'
import { ActionPage } from './ActionPage'

const runAction = vi.fn(async (_id: string, _params: object) => ({
  id: 'job-1',
  status: 'queued',
}))

const getDownloads = vi.fn(async () => ({
  run_dir: 'downloads/2026-08-10_14-32-05',
  files: [
    {
      name: 'IMG_1234.HEIC',
      phase: 'downloading' as const,
      bytes: 1_258_291,
      total: 3_565_158,
      destination: 'Photos/2025-07',
    },
  ],
  stale_runs: [],
}))

const baseJob: Job = {
  id: 'job-1', action: 'verify_library', params: {}, status: 'done',
  progress: 1, message: null, error: null, created_at: '', started_at: null,
  finished_at: null, run_id: 'run-1', resumed_from: null, phase: null,
  items_done: 0, items_total: 0,
}

const getJob = vi.fn(async (_id: string) => baseJob)

vi.mock('../api/client', () => ({
  runAction: (...args: unknown[]) => runAction(...(args as [string, object])),
  getJob: (id: string) => getJob(id),
  getJobEvents: vi.fn(async () => []),
  streamJob: vi.fn(() => () => undefined),
  getDownloads: () => getDownloads(),
}))

const ACTIONS: ActionSpec[] = [
  {
    id: 'verify_library',
    title: 'Verify Library',
    description: 'Verify Drive access.',
    order: 90,
    group: 'tool',
    schema: { type: 'object', properties: {} },
  },
  {
    id: 'organize',
    title: 'Organize Photos',
    description: 'Upload every planned file.',
    order: 40,
    group: 'tool',
    schema: { type: 'object', properties: {} },
  },
  {
    id: 'clear_stale_trees',
    title: 'Clear Stale Trees',
    description: 'Trash a verified extracted tree.',
    order: 50,
    group: 'tool',
    schema: {
      type: 'object',
      properties: {
        tree_folder_id: { type: 'string', title: 'Tree Folder Id', default: '' },
        confirm: { type: 'boolean', title: 'Confirm', default: false },
      },
    },
  },
  {
    id: 'sync_archives',
    title: 'Sync from Archives',
    description: 'Extract every file from the archives.',
    order: 1,
    group: 'flow',
    schema: {
      type: 'object',
      properties: {
        confirm: { type: 'boolean', title: 'Confirm', default: false },
        run_id: { type: 'string', title: 'Run Id', default: '' },
      },
    },
  },
]

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/actions/:actionId" element={<ActionPage actions={ACTIONS} />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => vi.clearAllMocks())

describe('ActionPage', () => {
  it('shows the action title and description', () => {
    renderAt('/actions/verify_library')
    expect(screen.getByText('Verify Library')).toBeTruthy()
    expect(screen.getByText('Verify Drive access.')).toBeTruthy()
  })

  it('reports an unknown action', () => {
    renderAt('/actions/nope')
    expect(screen.getByText(/unknown action/i)).toBeTruthy()
  })

  it('runs the action when the button is clicked', async () => {
    renderAt('/actions/verify_library')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(runAction).toHaveBeenCalledWith('verify_library', {})
  })

  it('sends the filled-in parameters when the action has some', async () => {
    renderAt('/actions/clear_stale_trees')
    await userEvent.type(screen.getByLabelText('Tree Folder Id'), 'abc123')
    await userEvent.click(screen.getByLabelText('Confirm'))
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(runAction).toHaveBeenCalledWith('clear_stale_trees', {
      tree_folder_id: 'abc123',
      confirm: true,
    })
  })

  it('sends empty params when the form is untouched', async () => {
    renderAt('/actions/clear_stale_trees')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(runAction).toHaveBeenCalledWith('clear_stale_trees', {})
  })

  it('shows in-flight files once an organize run starts', async () => {
    renderAt('/actions/organize')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(await screen.findByText('IMG_1234.HEIC')).toBeTruthy()
  })

  it('does not poll for downloads on other actions', async () => {
    renderAt('/actions/verify_library')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(getDownloads).not.toHaveBeenCalled()
  })

  // C1: an unconfirmed flow's dry run must be confirmable from the job it
  // just produced — reposting with confirm=true and *that* job's own
  // run_id, never a fresh one, or the flow refuses ("no plan for this run").
  it('offers to confirm a finished, unconfirmed flow run using its own run_id', async () => {
    getJob.mockResolvedValueOnce({
      ...baseJob, action: 'sync_archives', run_id: 'run-abc',
      params: { workers: 4 },
    })

    renderAt('/actions/sync_archives')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    const confirmButton = await screen.findByRole(
      'button', { name: /confirm this plan/i },
    )
    await userEvent.click(confirmButton)

    expect(runAction).toHaveBeenLastCalledWith('sync_archives', {
      workers: 4,
      confirm: true,
      run_id: 'run-abc',
    })
  })

  it('does not offer to confirm a run that is not a finished flow', async () => {
    // Tool action: getJob's default mock ('verify_library', 'done') applies,
    // but group is 'tool', not 'flow'.
    renderAt('/actions/verify_library')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    await screen.findByText(/status/i)
    expect(
      screen.queryByRole('button', { name: /confirm this plan/i }),
    ).not.toBeInTheDocument()
  })

  it('does not offer to confirm a flow run that is still in progress', async () => {
    getJob.mockResolvedValueOnce({
      ...baseJob, action: 'sync_archives', status: 'running', run_id: 'run-abc',
    })
    renderAt('/actions/sync_archives')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    await screen.findByText(/running/i)
    expect(
      screen.queryByRole('button', { name: /confirm this plan/i }),
    ).not.toBeInTheDocument()
  })

  it('does not offer to confirm a run that was already confirmed', async () => {
    getJob.mockResolvedValueOnce({
      ...baseJob, action: 'sync_archives', run_id: 'run-abc',
      params: { confirm: true },
    })
    renderAt('/actions/sync_archives')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    await screen.findByText(/status/i)
    expect(
      screen.queryByRole('button', { name: /confirm this plan/i }),
    ).not.toBeInTheDocument()
  })
})
