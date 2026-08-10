import { useState } from 'react'
import { addFilesToTag, createTag, removeFilesFromTag } from '../api/client'
import type { TagWithCount } from '../api/types'

// Drive allows 30 appProperties per file and Organize already writes about
// five. Warning here turns a later opaque API failure into a visible limit.
const MAX_TAGS = 25

export function TagPicker({
  tags,
  driveIds,
  onApplied,
  tagCount,
}: {
  tags: TagWithCount[]
  driveIds: string[]
  onApplied: () => void
  tagCount?: number
}) {
  const [tagId, setTagId] = useState<number | ''>('')
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (driveIds.length === 0) return null

  async function apply(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onApplied()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="tag-picker">
      <span>
        {driveIds.length} file{driveIds.length === 1 ? '' : 's'}
      </span>

      <label>
        Tag
        <select
          value={tagId}
          onChange={(event) => setTagId(event.target.value ? Number(event.target.value) : '')}
        >
          <option value="">Choose…</option>
          {tags.map((tag) => (
            <option key={tag.id} value={tag.id}>
              {tag.name}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        disabled={busy || tagId === ''}
        onClick={() => apply(() => addFilesToTag(Number(tagId), driveIds))}
      >
        Add tag
      </button>
      <button
        type="button"
        disabled={busy || tagId === ''}
        onClick={() => apply(() => removeFilesFromTag(Number(tagId), driveIds))}
      >
        Remove tag
      </button>

      <label>
        New tag
        <input
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder="greece-2025"
        />
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          const name = newName.trim()
          if (!name) return
          return apply(async () => {
            const tag = await createTag(name)
            await addFilesToTag(tag.id, driveIds)
            setNewName('')
          })
        }}
      >
        Create and apply
      </button>

      {tagCount !== undefined && tagCount > MAX_TAGS && (
        <p className="warn">
          This file carries {tagCount} tags. Only {MAX_TAGS} fit in Drive's
          appProperties, so Sync Tags will skip it until you remove some.
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  )
}
