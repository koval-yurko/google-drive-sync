import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { ActionSpec } from '../api/types'
import { Nav } from './Nav'

const ACTIONS = [
  { id: 'scan_archives', title: 'Scan Archives', description: '', order: 10, group: 'advanced', schema: { type: 'object' } },
] as ActionSpec[]

describe('Nav', () => {
  it('links to the Library and Tags pages', () => {
    render(
      <MemoryRouter>
        <Nav actions={ACTIONS} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /library/i }).getAttribute('href')).toBe('/library')
    expect(screen.getByRole('link', { name: /tags/i }).getAttribute('href')).toBe('/tags')
  })

  it('still lists the actions it is given', () => {
    render(
      <MemoryRouter>
        <Nav actions={ACTIONS} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Scan Archives' })).toBeTruthy()
  })

  it('separates flows from advanced actions', () => {
    const actions = [
      { id: 'sync_archives', title: 'Sync from Archives', description: '', order: 1, group: 'flow', schema: { type: 'object' } },
      { id: 'scan_archives', title: 'Scan Archives', description: '', order: 10, group: 'advanced', schema: { type: 'object' } },
    ] as ActionSpec[]
    render(<MemoryRouter><Nav actions={actions} /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Flows' })).toBeInTheDocument()
    expect(screen.getByText('Advanced')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sync from Archives' })).toBeInTheDocument()
  })
})
