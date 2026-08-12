import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReviewPage } from './ReviewPage'
import type { ReviewMedia } from '../api/types'

const reviewRow: ReviewMedia = {
  entry_id: 0, name: '', path: '', archive_name: '',
  target_folder: null, target_name: null,
  capture_time: null, capture_source: null, country: null,
  duplicate_of: null, duplicate_reason: null,
  upload_status: '', error: null, drive_file_id: null, size: 0,
  plan_verdict: null, plan_match: null,
}

const listReviewMedia = vi.fn(async (_opts?: object): Promise<{ total: number; rows: ReviewMedia[] }> => ({
  total: 2,
  rows: [
    {
      ...reviewRow,
      entry_id: 1,
      name: 'IMG_1.HEIC', path: 'd/IMG_1.HEIC', archive_name: 'a.zip',
      target_folder: '2023-11', target_name: 'IMG_1.HEIC',
      capture_time: 1700000000, capture_source: 'photo_taken_time',
      country: 'Poland',
      duplicate_of: null, duplicate_reason: null,
      upload_status: 'done', error: null, drive_file_id: 'f1', size: 100,
    },
    {
      ...reviewRow,
      entry_id: 2,
      name: 'IMG_2.MOV', path: 'd/IMG_2.MOV', archive_name: 'a.zip',
      target_folder: '2019-01', target_name: 'IMG_2.MOV',
      capture_time: null, capture_source: 'year_folder',
      country: null,
      duplicate_of: 'back_2024_01', duplicate_reason: 'name match',
      upload_status: 'error', error: 'crc: CRC mismatch',
      drive_file_id: null, size: 200,
    },
  ],
}))

const retryUpload = vi.fn(async (_id: number) => ({
  entry_id: 2, upload_status: 'pending',
}))

vi.mock('../api/client', () => ({
  getReviewSummary: vi.fn(async () => ({
    media: 2, planned: 2, unplanned: 0, duplicates: 1,
    with_sidecar: 1, pending: 0, uploaded: 1, errors: 1,
    archives: 1, entries: 3, drive_files: 5,
  })),
  listReviewMedia: (opts?: object) => listReviewMedia(opts),
  retryUpload: (id: number) => retryUpload(id),
}))

afterEach(() => vi.clearAllMocks())

describe('ReviewPage', () => {
  it('shows the summary totals', async () => {
    render(<ReviewPage />)
    // Several tiles legitimately show the same number — media and planned are
    // both 2 once everything is planned — so assert on the tile as a unit
    // rather than on a bare value that matches more than one element.
    const label = await screen.findByText(/media files/i)
    expect(label.closest('.card')?.textContent).toContain('2')
  })

  it('lists every planned file with its destination', async () => {
    render(<ReviewPage />)
    expect(await screen.findByText('IMG_1.HEIC')).toBeTruthy()
    expect(screen.getByText('2023-11')).toBeTruthy()
    expect(screen.getByText('2019-01')).toBeTruthy()
  })

  it('flags a file that already exists in the destination', async () => {
    render(<ReviewPage />)
    await screen.findByText('IMG_2.MOV')
    expect(screen.getByText(/back_2024_01/)).toBeTruthy()
  })

  it('says plainly that duplicates still upload', async () => {
    render(<ReviewPage />)
    expect(await screen.findByText(/still be uploaded/i)).toBeTruthy()
  })

  it('requests only duplicates when the toggle is used', async () => {
    render(<ReviewPage />)
    await screen.findByText('IMG_1.HEIC')
    await userEvent.click(screen.getByLabelText(/only files already in the destination/i))
    await waitFor(() =>
      expect(listReviewMedia).toHaveBeenLastCalledWith(
        expect.objectContaining({ duplicatesOnly: true }),
      ),
    )
  })

  it('shows each file its upload status', async () => {
    render(<ReviewPage />)
    expect(await screen.findByText('done')).toBeTruthy()
    expect(screen.getByText(/CRC mismatch/)).toBeTruthy()
  })

  it('offers Retry only for a failed file', async () => {
    render(<ReviewPage />)
    await screen.findByText('IMG_2.MOV')
    expect(screen.getAllByRole('button', { name: /retry/i })).toHaveLength(1)
  })

  it('retries the failed file and reloads', async () => {
    render(<ReviewPage />)
    await screen.findByText('IMG_2.MOV')
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    await waitFor(() => expect(retryUpload).toHaveBeenCalledWith(2))
    await waitFor(() => expect(listReviewMedia).toHaveBeenCalledTimes(2))
  })

  it('shows the plan verdict for each file', async () => {
    listReviewMedia.mockResolvedValueOnce({
      total: 3,
      rows: [
        { ...reviewRow, entry_id: 1, name: 'a.heic', plan_verdict: 'skip' },
        { ...reviewRow, entry_id: 2, name: 'b.heic', plan_verdict: 'verify' },
        { ...reviewRow, entry_id: 3, name: 'c.heic', plan_verdict: 'upload' },
      ],
    })
    render(<ReviewPage />)
    for (const verdict of ['skip', 'verify', 'upload']) {
      expect(await screen.findByText(verdict)).toBeInTheDocument()
    }
  })
})
