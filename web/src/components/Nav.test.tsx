import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Nav } from './Nav'

const ACTIONS = [
  { id: 'scan_archives', title: 'Scan Archives', description: '', order: 10, schema: { type: 'object' } },
]

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
})
