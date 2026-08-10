import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TagsPage } from './TagsPage'

const TAGS = [
  { id: 1, name: 'Family', slug: 'family', color: '#ff0000', file_count: 12 },
  { id: 2, name: 'Familly', slug: 'familly', color: '#00ff00', file_count: 3 },
]

const listTags = vi.fn(async () => TAGS)
const createTag = vi.fn(async (name: string) => ({
  id: 3, name, slug: 'x', color: '#000', file_count: 0,
}))
const patchTag = vi.fn(async () => TAGS[0])
const deleteTag = vi.fn(async () => ({ deleted: 2 }))
const mergeTags = vi.fn(async () => ({ moved: 3, target: TAGS[0] }))

vi.mock('../api/client', () => ({
  listTags: () => listTags(),
  createTag: (...args: unknown[]) => createTag(...(args as [string])),
  patchTag: (...args: unknown[]) => patchTag(...(args as [])),
  deleteTag: (...args: unknown[]) => deleteTag(...(args as [])),
  mergeTags: (...args: unknown[]) => mergeTags(...(args as [])),
}))

afterEach(() => vi.clearAllMocks())

/**
 * Scoped to the table on purpose: every tag name also appears twice in the
 * merge dropdowns, so an unscoped getByText finds three of each.
 */
const row = (name: string) =>
  within(screen.getByRole('table')).getByText(name).closest('tr') as HTMLElement

const loaded = () => screen.findByRole('table')

describe('TagsPage', () => {
  it('lists every tag with its file count', async () => {
    render(<TagsPage />)
    await loaded()
    expect(row('Family').textContent).toContain('12')
  })

  it('creates a tag', async () => {
    render(<TagsPage />)
    await loaded()
    await userEvent.type(screen.getByLabelText(/new tag/i), 'Greece 2025')
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    await waitFor(() => expect(createTag).toHaveBeenCalledWith('Greece 2025'))
  })

  it('reloads after creating, so the count is real', async () => {
    render(<TagsPage />)
    await loaded()
    await userEvent.type(screen.getByLabelText(/new tag/i), 'Greece')
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    await waitFor(() => expect(listTags).toHaveBeenCalledTimes(2))
  })

  it('renames a tag', async () => {
    render(<TagsPage />)
    await loaded()
    const input = within(row('Familly')).getByLabelText(/name/i)
    await userEvent.clear(input)
    await userEvent.type(input, 'Friends')
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /save/i }))
    await waitFor(() => expect(patchTag).toHaveBeenCalledWith(2, { name: 'Friends' }))
  })

  it('recolours a tag', async () => {
    render(<TagsPage />)
    await loaded()
    const picker = within(row('Family')).getByLabelText(/colou?r/i)
    await userEvent.clear(picker)
    await userEvent.type(picker, '#0000ff')
    await userEvent.click(within(row('Family')).getByRole('button', { name: /save/i }))
    await waitFor(() =>
      expect(patchTag).toHaveBeenCalledWith(1, expect.objectContaining({ color: '#0000ff' })),
    )
  })

  it('asks before deleting', async () => {
    render(<TagsPage />)
    await loaded()
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /^delete$/i }))
    expect(deleteTag).not.toHaveBeenCalled()
    expect(within(row('Familly')).getByRole('button', { name: /really/i })).toBeTruthy()
  })

  it('deletes on the second click', async () => {
    render(<TagsPage />)
    await loaded()
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /^delete$/i }))
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /really/i }))
    await waitFor(() => expect(deleteTag).toHaveBeenCalledWith(2))
  })

  it('says that deleting a tag keeps the files', async () => {
    render(<TagsPage />)
    await loaded()
    expect(screen.getByText(/no files are deleted/i)).toBeTruthy()
  })

  it('merges one tag into another', async () => {
    render(<TagsPage />)
    await loaded()
    await userEvent.selectOptions(screen.getByLabelText(/merge/i), '2')
    await userEvent.selectOptions(screen.getByLabelText(/into/i), '1')
    await userEvent.click(screen.getByRole('button', { name: /^merge$/i }))
    await waitFor(() => expect(mergeTags).toHaveBeenCalledWith(2, 1))
  })

  it('refuses to merge a tag into itself', async () => {
    render(<TagsPage />)
    await loaded()
    await userEvent.selectOptions(screen.getByLabelText(/merge/i), '1')
    await userEvent.selectOptions(screen.getByLabelText(/into/i), '1')
    await userEvent.click(screen.getByRole('button', { name: /^merge$/i }))
    expect(mergeTags).not.toHaveBeenCalled()
  })

  it('surfaces a duplicate-name failure', async () => {
    createTag.mockRejectedValueOnce(new Error('409: a tag named that exists'))
    render(<TagsPage />)
    await loaded()
    await userEvent.type(screen.getByLabelText(/new tag/i), 'Family')
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    expect(await screen.findByText(/409/)).toBeTruthy()
  })

  it('points at the Library when there are no tags yet', async () => {
    listTags.mockResolvedValueOnce([])
    render(<TagsPage />)
    expect(await screen.findByText(/no tags yet/i)).toBeTruthy()
  })

  it('reminds you that Drive learns about tags only on sync', async () => {
    render(<TagsPage />)
    expect(await screen.findByText(/sync tags/i)).toBeTruthy()
  })
})
