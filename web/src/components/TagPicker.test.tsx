import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TagPicker } from './TagPicker'

const addFilesToTag = vi.fn(async () => ({ added: 2 }))
const removeFilesFromTag = vi.fn(async () => ({ removed: 2 }))
const createTag = vi.fn(async (name: string) => ({
  id: 9, name, slug: name.toLowerCase(), color: '#000', file_count: 0,
}))

vi.mock('../api/client', () => ({
  addFilesToTag: (...args: unknown[]) => addFilesToTag(...(args as [])),
  removeFilesFromTag: (...args: unknown[]) => removeFilesFromTag(...(args as [])),
  createTag: (...args: unknown[]) => createTag(...(args as [string])),
}))

const TAGS = [
  { id: 1, name: 'Family', slug: 'family', color: '#f00', file_count: 3 },
  { id: 2, name: 'Print These', slug: 'print-these', color: '#0f0', file_count: 1 },
]

afterEach(() => vi.clearAllMocks())

// Exact, not /tag/i: "New tag" would match that too and the query would be
// ambiguous.
const tagSelect = () => screen.getByLabelText('Tag')

describe('TagPicker', () => {
  it('renders nothing with no selection', () => {
    const { container } = render(
      <TagPicker tags={TAGS} driveIds={[]} onApplied={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('says how many files it will affect', () => {
    render(<TagPicker tags={TAGS} driveIds={['d1', 'd2']} onApplied={vi.fn()} />)
    expect(screen.getByText(/2 file/i)).toBeTruthy()
  })

  it('adds an existing tag to the selection', async () => {
    const onApplied = vi.fn()
    render(<TagPicker tags={TAGS} driveIds={['d1', 'd2']} onApplied={onApplied} />)

    await userEvent.selectOptions(tagSelect(), '1')
    await userEvent.click(screen.getByRole('button', { name: /^add tag$/i }))

    expect(addFilesToTag).toHaveBeenCalledWith(1, ['d1', 'd2'])
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
  })

  it('removes a tag from the selection', async () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)

    await userEvent.selectOptions(tagSelect(), '2')
    await userEvent.click(screen.getByRole('button', { name: /remove tag/i }))

    expect(removeFilesFromTag).toHaveBeenCalledWith(2, ['d1'])
  })

  it('creates a new tag and applies it in one action', async () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)

    await userEvent.type(screen.getByLabelText(/new tag/i), 'Greece 2025')
    await userEvent.click(screen.getByRole('button', { name: /create and apply/i }))

    await waitFor(() => expect(createTag).toHaveBeenCalledWith('Greece 2025'))
    await waitFor(() => expect(addFilesToTag).toHaveBeenCalledWith(9, ['d1']))
  })

  it('will not create a tag with a blank name', async () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /create and apply/i }))
    expect(createTag).not.toHaveBeenCalled()
  })

  it('surfaces a failure instead of pretending it worked', async () => {
    addFilesToTag.mockRejectedValueOnce(new Error('409: a tag named that exists'))
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)

    await userEvent.selectOptions(tagSelect(), '1')
    await userEvent.click(screen.getByRole('button', { name: /^add tag$/i }))

    expect(await screen.findByText(/409/)).toBeTruthy()
  })

  it('warns before the appProperties ceiling rather than after', () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} tagCount={26} />)
    expect(screen.getByText(/25/)).toBeTruthy()
  })
})
