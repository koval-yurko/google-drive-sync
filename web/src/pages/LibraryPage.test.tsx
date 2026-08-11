import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LibraryPage } from './LibraryPage'

const ROWS = [
  {
    drive_id: 'd1', name: 'IMG_1.HEIC', month: '2025-06', mime_type: 'image/heic',
    media_type: 'image', size: 100, md5: 'a', capture_time: 1750000000,
    capture_source: 'photo_taken_time', country: 'Poland',
    duplicate_of: null, duplicate_reason: null, archive_name: 'part-001.zip',
    tags: [{ id: 1, name: 'Family', slug: 'family', color: '#f00' }],
  },
  {
    drive_id: 'd2', name: 'VID_1.MOV', month: '2025-06', mime_type: 'video/quicktime',
    media_type: 'video', size: 200, md5: 'b', capture_time: null,
    capture_source: null, country: null,
    duplicate_of: '2025-06', duplicate_reason: 'name and size match an existing file',
    archive_name: 'part-002.zip', tags: [],
  },
  {
    drive_id: 'd3', name: 'IMG_9.HEIC', month: '2025-05', mime_type: 'image/heic',
    media_type: 'image', size: 300, md5: 'c', capture_time: 1747000000,
    capture_source: 'exif', country: 'Portugal',
    duplicate_of: null, duplicate_reason: null, archive_name: 'part-003.zip',
    tags: [],
  },
]

const listLibraryFiles = vi.fn(async () => ({ total: 3, rows: ROWS }))
const listLibraryIds = vi.fn(async () => ['d1', 'd2', 'd3'])

vi.mock('../api/client', () => ({
  listLibraryFiles: (...args: unknown[]) => listLibraryFiles(...(args as [])),
  listLibraryIds: (...args: unknown[]) => listLibraryIds(...(args as [])),
  getFacets: vi.fn(async () => ({
    total: 3,
    months: [{ value: '2025-06', count: 2 }, { value: '2025-05', count: 1 }],
    countries: [{ value: 'Poland', count: 1 }],
    types: [{ value: 'image', count: 2 }, { value: 'video', count: 1 }],
    duplicates: 1,
  })),
  listTags: vi.fn(async () => [
    { id: 1, name: 'Family', slug: 'family', color: '#f00', file_count: 1 },
  ]),
  thumbUrl: (id: string, size = 400) => `/api/thumb/${id}?size=${size}`,
  getLibraryFile: vi.fn(async () => ROWS[0]),
  addFilesToTag: vi.fn(async () => ({ added: 1 })),
  removeFilesFromTag: vi.fn(async () => ({ removed: 1 })),
  createTag: vi.fn(async () => ({
    id: 2, name: 'New', slug: 'new', color: '#000', file_count: 0,
  })),
}))

afterEach(() => vi.clearAllMocks())

const tile = (name: string) => screen.getByRole('img', { name }).closest('.tile') as HTMLElement

/**
 * user-event's `click` takes setup options, not an event init, so a modifier
 * has to be held down around the click for it to reach React's synthetic
 * event. The held key only survives on a shared instance — each direct-API
 * call builds a fresh one and forgets what was down.
 */
async function shiftClick(
  user: ReturnType<typeof userEvent.setup>,
  element: Element,
) {
  await user.keyboard('{Shift>}')
  await user.click(element)
  await user.keyboard('{/Shift}')
}

describe('LibraryPage', () => {
  it('groups files under their month', async () => {
    render(<LibraryPage />)
    expect(await screen.findByRole('heading', { name: '2025-06' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '2025-05' })).toBeTruthy()
  })

  it('renders a tile per file', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(screen.getAllByRole('img')).toHaveLength(3)
  })

  it('reports how many files are showing', async () => {
    render(<LibraryPage />)
    expect(await screen.findByText(/3 file/i)).toBeTruthy()
  })

  it('marks a flagged duplicate', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'VID_1.MOV' })
    expect(tile('VID_1.MOV').textContent).toMatch(/duplicate/i)
  })

  it('selects a file on click', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(tile('IMG_1.HEIC').getAttribute('aria-selected')).toBe('true')
  })

  it('extends the selection with shift-click', async () => {
    const user = userEvent.setup()
    render(<LibraryPage />)
    await user.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await shiftClick(user, screen.getByRole('img', { name: 'IMG_9.HEIC' }))
    expect(tile('VID_1.MOV').getAttribute('aria-selected')).toBe('true')
  })

  it('selects everything matching the filter, not just what is rendered', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await userEvent.click(screen.getByRole('button', { name: /select all/i }))
    await waitFor(() => expect(listLibraryIds).toHaveBeenCalled())
    expect(await screen.findByText(/3 selected/i)).toBeTruthy()
  })

  it('refetches when a filter changes', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    await waitFor(() =>
      expect(listLibraryFiles).toHaveBeenLastCalledWith(
        expect.objectContaining({ month: '2025-05' }),
        expect.anything(),
      ),
    )
  })

  it('drops the selection when the filter changes', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    await waitFor(() => expect(screen.queryByText(/selected/i)).toBeNull())
  })

  it('offers Load more only while there is more', async () => {
    listLibraryFiles.mockResolvedValueOnce({ total: 500, rows: ROWS })
    render(<LibraryPage />)
    expect(await screen.findByRole('button', { name: /load more/i })).toBeTruthy()
  })

  it('does not offer Load more once everything is shown', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(screen.queryByRole('button', { name: /load more/i })).toBeNull()
  })

  it('says so plainly when a filter matches nothing', async () => {
    listLibraryFiles.mockResolvedValueOnce({ total: 0, rows: [] })
    render(<LibraryPage />)
    expect(await screen.findByText(/no files match/i)).toBeTruthy()
  })

  it('offers bulk tagging once something is selected', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(screen.getByRole('button', { name: /^add tag$/i })).toBeTruthy()
  })

  it('offers no tagging controls with nothing selected', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(screen.queryByRole('button', { name: /^add tag$/i })).toBeNull()
  })

  it('opens the lightbox on double-click', async () => {
    render(<LibraryPage />)
    await userEvent.dblClick(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(await screen.findByRole('dialog')).toBeTruthy()
  })

  it('does not open the lightbox on a single click', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps the selection when the lightbox opens', async () => {
    const user = userEvent.setup()
    render(<LibraryPage />)
    await user.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await shiftClick(user, screen.getByRole('img', { name: 'IMG_9.HEIC' }))
    await user.dblClick(screen.getByRole('img', { name: 'IMG_9.HEIC' }))
    expect(screen.getByText(/3 selected/i)).toBeTruthy()
  })
})
