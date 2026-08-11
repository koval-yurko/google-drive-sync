import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { LibraryFilters } from '../lib/filters'
import { FilterSidebar } from './FilterSidebar'

const FACETS = {
  total: 12,
  months: [
    { value: '2025-06', count: 7 },
    { value: '2025-05', count: 5 },
  ],
  countries: [{ value: 'Poland', count: 4 }],
  types: [
    { value: 'image', count: 9 },
    { value: 'video', count: 3 },
  ],
  duplicates: 2,
}

const TAGS = [
  { id: 1, name: 'Family', slug: 'family', color: '#f00', file_count: 3 },
]

/**
 * The sidebar's search box is controlled by `filters`, so the harness has to
 * feed changes back the way LibraryPage does — otherwise every keystroke
 * starts from an empty box and only the last character survives.
 */
function setup(initial: LibraryFilters = {}) {
  const onChange = vi.fn()

  function Harness() {
    const [filters, setFilters] = useState<LibraryFilters>(initial)
    return (
      <FilterSidebar
        facets={FACETS}
        tags={TAGS}
        filters={filters}
        onChange={(next) => {
          onChange(next)
          setFilters(next)
        }}
      />
    )
  }

  render(<Harness />)
  return onChange
}

describe('FilterSidebar', () => {
  it('lists months with their counts', () => {
    setup()
    expect(screen.getByRole('button', { name: /2025-06/ }).textContent).toContain('7')
  })

  it('lists countries, types, and tags', () => {
    setup()
    expect(screen.getByRole('button', { name: /Poland/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /video/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Family/ })).toBeTruthy()
  })

  it('offers duplicates as one more filter, not a separate page', () => {
    setup()
    expect(screen.getByRole('button', { name: /duplicates/i }).textContent).toContain('2')
  })

  it('applies a month', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    expect(onChange).toHaveBeenCalledWith({ month: '2025-05' })
  })

  it('applies a tag by id', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByRole('button', { name: /Family/ }))
    expect(onChange).toHaveBeenCalledWith({ tagId: 1 })
  })

  it('clears a filter when its active value is clicked again', async () => {
    const onChange = setup({ month: '2025-05' })
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    expect(onChange).toHaveBeenCalledWith({ month: undefined })
  })

  it('keeps other filters when one changes', async () => {
    const onChange = setup({ month: '2025-05' })
    await userEvent.click(screen.getByRole('button', { name: /video/ }))
    expect(onChange).toHaveBeenCalledWith({ month: '2025-05', mediaType: 'video' })
  })

  it('marks the active filter', () => {
    setup({ month: '2025-05' })
    expect(
      screen.getByRole('button', { name: /2025-05/ }).getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('searches by name', async () => {
    const onChange = setup()
    await userEvent.type(screen.getByLabelText(/search/i), 'IMG')
    expect(onChange).toHaveBeenLastCalledWith({ search: 'IMG' })
  })

  it('offers a way back to everything', async () => {
    const onChange = setup({ month: '2025-05', duplicates: true })
    await userEvent.click(screen.getByRole('button', { name: /clear filters/i }))
    expect(onChange).toHaveBeenCalledWith({})
  })

  it('says so plainly before the first scan', () => {
    render(
      <FilterSidebar facets={null} tags={[]} filters={{}} onChange={vi.fn()} />,
    )
    expect(screen.getByText(/run scan/i)).toBeTruthy()
  })
})
