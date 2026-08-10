import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InflightTable } from './InflightTable'
import type { Downloads } from '../api/types'

const EMPTY: Downloads = { run_dir: null, files: [], stale_runs: [] }

const BUSY: Downloads = {
  run_dir: 'downloads/2026-08-10_14-32-05',
  files: [
    {
      name: 'IMG_1234.HEIC',
      phase: 'downloading',
      bytes: 1_258_291,
      total: 3_565_158,
      destination: 'Photos/2025-07',
    },
    {
      name: 'IMG_1240.MOV',
      phase: 'uploading',
      bytes: 18_874_368,
      total: 44_145_213,
      destination: 'Photos/2025-08',
    },
  ],
  stale_runs: [],
}

describe('InflightTable', () => {
  it('renders nothing when no file is moving', () => {
    const { container } = render(<InflightTable downloads={EMPTY} />)
    expect(container.textContent).toBe('')
  })

  it('shows each file, its phase, and where it is going', () => {
    render(<InflightTable downloads={BUSY} />)
    expect(screen.getByText('IMG_1234.HEIC')).toBeTruthy()
    expect(screen.getByText('downloading')).toBeTruthy()
    expect(screen.getByText('IMG_1240.MOV')).toBeTruthy()
    expect(screen.getByText('uploading')).toBeTruthy()
    expect(screen.getByText('Photos/2025-08')).toBeTruthy()
  })

  it('shows how far each file has got', () => {
    render(<InflightTable downloads={BUSY} />)
    expect(screen.getByText('1.3 / 3.6 MB')).toBeTruthy()
    expect(screen.getByText('18.9 / 44.1 MB')).toBeTruthy()
  })

  it('warns about bytes an earlier run left behind', () => {
    render(
      <InflightTable
        downloads={{
          ...EMPTY,
          stale_runs: [{ dir: '2026-08-09_22-14-01', files: 3, bytes: 1_503_238_553 }],
        }}
      />,
    )
    expect(screen.getByText(/2026-08-09_22-14-01/)).toBeTruthy()
    expect(screen.getByText(/1\.50 GB/)).toBeTruthy()
  })
})
