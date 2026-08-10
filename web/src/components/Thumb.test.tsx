import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Thumb } from './Thumb'

vi.mock('../api/client', () => ({
  thumbUrl: (id: string, size = 400) => `/api/thumb/${id}?size=${size}`,
}))

describe('Thumb', () => {
  it('renders an image at the grid size by default', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    const image = screen.getByRole('img') as HTMLImageElement
    expect(image.getAttribute('src')).toContain('/api/thumb/d1?size=400')
  })

  it('asks for the large render when told to', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" size={1600} />)
    expect(screen.getByRole('img').getAttribute('src')).toContain('size=1600')
  })

  it('defers loading, which is what replaces virtualisation', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    expect(screen.getByRole('img').getAttribute('loading')).toBe('lazy')
  })

  it('names the file for screen readers', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    expect(screen.getByRole('img').getAttribute('alt')).toBe('IMG_1.HEIC')
  })

  it('falls back to the extension when Drive has no render yet', async () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    screen.getByRole('img').dispatchEvent(new Event('error'))
    expect(await screen.findByText('HEIC')).toBeTruthy()
  })

  it('retries rather than giving up on the first miss', async () => {
    vi.useFakeTimers()
    try {
      render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
      const first = screen.getByRole('img').getAttribute('src')
      // React commits this through a MessageChannel task, which the fake
      // timers do not drive; act() is what forces it to land.
      act(() => {
        screen.getByRole('img').dispatchEvent(new Event('error'))
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      const image = screen.queryByRole('img')
      expect(image?.getAttribute('src')).not.toBe(first)
    } finally {
      vi.useRealTimers()
    }
  })
})
