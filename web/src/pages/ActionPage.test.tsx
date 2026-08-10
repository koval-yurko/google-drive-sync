import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

vi.mock('../api/client', () => ({
  runAction: (...args: unknown[]) => runAction(...(args as [string, object])),
  getJob: vi.fn(async () => ({ id: 'job-1', status: 'done', progress: 1, message: null })),
  getJobEvents: vi.fn(async () => []),
  streamJob: vi.fn(() => () => undefined),
  getDownloads: () => getDownloads(),
}))

const ACTIONS = [
  {
    id: 'check_connection',
    title: 'Check Connection',
    description: 'Verify Drive access.',
    order: 0,
    schema: { type: 'object', properties: {} },
  },
  {
    id: 'organize',
    title: 'Organize Photos',
    description: 'Upload every planned file.',
    order: 40,
    schema: { type: 'object', properties: {} },
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
    renderAt('/actions/check_connection')
    expect(screen.getByText('Check Connection')).toBeTruthy()
    expect(screen.getByText('Verify Drive access.')).toBeTruthy()
  })

  it('reports an unknown action', () => {
    renderAt('/actions/nope')
    expect(screen.getByText(/unknown action/i)).toBeTruthy()
  })

  it('runs the action when the button is clicked', async () => {
    renderAt('/actions/check_connection')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(runAction).toHaveBeenCalledWith('check_connection', {})
  })

  it('shows in-flight files once an organize run starts', async () => {
    renderAt('/actions/organize')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(await screen.findByText('IMG_1234.HEIC')).toBeTruthy()
  })

  it('does not poll for downloads on other actions', async () => {
    renderAt('/actions/check_connection')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(getDownloads).not.toHaveBeenCalled()
  })
})
