import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Lightbox } from './Lightbox'

const IMAGE = {
  drive_id: 'd1', name: 'IMG_1.HEIC', month: '2025-06', mime_type: 'image/heic',
  media_type: 'image', size: 1048576, md5: 'abc', capture_time: 1750000000,
  capture_source: 'photo_taken_time', country: 'Poland',
  duplicate_of: null, duplicate_reason: null, archive_name: 'part-001.zip',
  tags: [{ id: 1, name: 'Family', slug: 'family', color: '#f00' }],
}

const VIDEO = {
  ...IMAGE, drive_id: 'd2', name: 'VID_1.MOV', media_type: 'video',
  mime_type: 'video/quicktime', country: null, tags: [],
  duplicate_of: '2025-06', duplicate_reason: 'name and size match an existing file',
}

const getLibraryFile = vi.fn(async (id: string) => (id === 'd2' ? VIDEO : IMAGE))

vi.mock('../api/client', () => ({
  getLibraryFile: (id: string) => getLibraryFile(id),
  thumbUrl: (id: string, size = 400) => `/api/thumb/${id}?size=${size}`,
  addFilesToTag: vi.fn(async () => ({ added: 1 })),
  removeFilesFromTag: vi.fn(async () => ({ removed: 1 })),
  createTag: vi.fn(async () => ({ id: 9, name: 'n', slug: 'n', color: '#0', file_count: 0 })),
}))

afterEach(() => vi.clearAllMocks())

const props = { tags: [], onClose: vi.fn(), onChanged: vi.fn() }

describe('Lightbox', () => {
  it('shows the file name', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByRole('heading', { name: 'IMG_1.HEIC' })).toBeTruthy()
  })

  it('renders an image at the large size', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    const image = await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(image.getAttribute('src')).toContain('size=1600')
  })

  it('plays a video in Drive’s own preview, which browsers cannot do natively', async () => {
    const { container } = render(<Lightbox driveId="d2" {...props} />)
    await screen.findByRole('heading', { name: 'VID_1.MOV' })
    const frame = container.querySelector('iframe')
    expect(frame?.getAttribute('src')).toBe('https://drive.google.com/file/d/d2/preview')
  })

  it('shows capture date, country, and source archive', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByText(/Poland/)).toBeTruthy()
    expect(screen.getByText(/part-001.zip/)).toBeTruthy()
  })

  it('says where a date came from, so a fallback is visible', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByText(/photo_taken_time/)).toBeTruthy()
  })

  it('explains a duplicate flag', async () => {
    render(<Lightbox driveId="d2" {...props} />)
    expect(await screen.findByText(/name and size match/i)).toBeTruthy()
  })

  it('lists the tags on the file', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByText('Family')).toBeTruthy()
  })

  it('closes on the button', async () => {
    const onClose = vi.fn()
    render(<Lightbox driveId="d1" {...props} onClose={onClose} />)
    await userEvent.click(await screen.findByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('closes on Escape', async () => {
    const onClose = vi.fn()
    render(<Lightbox driveId="d1" {...props} onClose={onClose} />)
    await screen.findByRole('heading', { name: 'IMG_1.HEIC' })
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })
})
