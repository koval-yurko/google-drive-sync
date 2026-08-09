import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FolderPicker } from './FolderPicker'

vi.mock('../api/client', () => ({
  listFolders: vi.fn(async (parent: string) => {
    if (parent === 'root') {
      return {
        parent: { id: 'root', name: 'My Drive' },
        folders: [{ id: 'zips', name: 'zip-3-22-26', mimeType: 'folder' }],
      }
    }
    return {
      parent: { id: 'zips', name: 'zip-3-22-26' },
      folders: [{ id: 'nested', name: 'Takeout', mimeType: 'folder' }],
    }
  }),
}))

afterEach(() => vi.clearAllMocks())

describe('FolderPicker', () => {
  it('lists folders from the Drive root', async () => {
    render(<FolderPicker title="Pick" onSelect={vi.fn()} onCancel={vi.fn()} />)
    expect(await screen.findByText('zip-3-22-26')).toBeTruthy()
  })

  it('drills into a folder when clicked', async () => {
    render(<FolderPicker title="Pick" onSelect={vi.fn()} onCancel={vi.fn()} />)
    await userEvent.click(await screen.findByText('zip-3-22-26'))
    expect(await screen.findByText('Takeout')).toBeTruthy()
  })

  it('selects the folder currently being viewed', async () => {
    const onSelect = vi.fn()
    render(<FolderPicker title="Pick" onSelect={onSelect} onCancel={vi.fn()} />)
    await userEvent.click(await screen.findByText('zip-3-22-26'))
    await waitFor(() => screen.getByText('Takeout'))
    await userEvent.click(screen.getByRole('button', { name: /use this folder/i }))
    expect(onSelect).toHaveBeenCalledWith({ id: 'zips', name: 'zip-3-22-26' })
  })

  it('cancels', async () => {
    const onCancel = vi.fn()
    render(<FolderPicker title="Pick" onSelect={vi.fn()} onCancel={onCancel} />)
    await userEvent.click(await screen.findByRole('button', { name: /cancel/i }))
    expect(onCancel).toHaveBeenCalled()
  })
})
