import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { ActionSpec } from '../api/types'
import { Nav } from './Nav'

const ACTIONS = [
  { id: 'verify_library', title: 'Verify Library', description: '', order: 90, group: 'tool', schema: { type: 'object' } },
] as ActionSpec[]

describe('Nav', () => {
  it('links to the Library and Tags pages', () => {
    render(
      <MemoryRouter>
        <Nav actions={ACTIONS} />
      </MemoryRouter>,
    )
    // Exact names, not /library/i — a tool titled "Verify Library" is also
    // a link whose name contains "library".
    expect(screen.getByRole('link', { name: 'Library' }).getAttribute('href')).toBe('/library')
    expect(screen.getByRole('link', { name: 'Tags' }).getAttribute('href')).toBe('/tags')
  })

  it('still lists the actions it is given', () => {
    render(
      <MemoryRouter>
        <Nav actions={ACTIONS} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Verify Library' })).toBeTruthy()
  })

  it('puts flows and tools in their own sections', () => {
    const actions = [
      { id: 'sync_archives', title: 'Sync from Archives', description: '', order: 1, group: 'flow', schema: { type: 'object' } },
      { id: 'verify_library', title: 'Verify Library', description: '', order: 90, group: 'tool', schema: { type: 'object' } },
    ] as ActionSpec[]
    render(<MemoryRouter><Nav actions={actions} /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Flows' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Tools' })).toBeInTheDocument()
    expect(screen.queryByText('Advanced')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sync from Archives' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Verify Library' })).toBeInTheDocument()
  })
})
