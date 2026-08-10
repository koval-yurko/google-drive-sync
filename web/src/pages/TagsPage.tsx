import { useEffect, useState } from 'react'
import { createTag, deleteTag, listTags, mergeTags, patchTag } from '../api/client'
import type { TagWithCount } from '../api/types'

function TagRow({
  tag,
  onSaved,
  onDeleted,
}: {
  tag: TagWithCount
  onSaved: (id: number, patch: { name?: string; color?: string }) => void
  onDeleted: (id: number) => void
}) {
  const [name, setName] = useState(tag.name)
  const [color, setColor] = useState(tag.color)
  const [confirming, setConfirming] = useState(false)

  return (
    <tr>
      <td>
        <span className="swatch" style={{ background: tag.color }} />
        {tag.name}
      </td>
      <td>
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
      </td>
      <td>
        <label>
          Colour
          <input value={color} onChange={(event) => setColor(event.target.value)} />
        </label>
      </td>
      <td>{tag.file_count}</td>
      <td>
        <button
          type="button"
          onClick={() => {
            const patch: { name?: string; color?: string } = {}
            if (name !== tag.name) patch.name = name
            if (color !== tag.color) patch.color = color
            if (Object.keys(patch).length > 0) onSaved(tag.id, patch)
          }}
        >
          Save
        </button>
        {confirming ? (
          <button type="button" className="danger" onClick={() => onDeleted(tag.id)}>
            Really delete?
          </button>
        ) : (
          // An inline second click rather than window.confirm, which blocks
          // the event loop and cannot be tested.
          <button type="button" onClick={() => setConfirming(true)}>
            Delete
          </button>
        )}
      </td>
    </tr>
  )
}

export function TagsPage() {
  const [tags, setTags] = useState<TagWithCount[]>([])
  const [newName, setNewName] = useState('')
  const [source, setSource] = useState<number | ''>('')
  const [target, setTarget] = useState<number | ''>('')
  const [error, setError] = useState<string | null>(null)

  function reload() {
    listTags().then(setTags).catch((e) => setError(String(e)))
  }

  useEffect(reload, [])

  async function guard(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      reload()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <>
      <h2>Tags</h2>
      <p className="muted">
        Tags live in the catalog. Run <strong>Sync Tags to Drive</strong> to
        mirror them onto the files themselves.
      </p>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <label>
          New tag
          <input value={newName} onChange={(event) => setNewName(event.target.value)} />
        </label>
        <button
          type="button"
          onClick={() => {
            const name = newName.trim()
            if (!name) return
            return guard(async () => {
              await createTag(name)
              setNewName('')
            })
          }}
        >
          Create
        </button>
      </div>

      {tags.length === 0 ? (
        <p className="muted">
          No tags yet. Select some files on the Library page and create one there.
        </p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Tag</th>
                <th>Name</th>
                <th>Colour</th>
                <th>Files</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tags.map((tag) => (
                <TagRow
                  key={tag.id}
                  tag={tag}
                  onSaved={(id, patch) => guard(() => patchTag(id, patch))}
                  onDeleted={(id) => guard(() => deleteTag(id))}
                />
              ))}
            </tbody>
          </table>
          <p className="muted">
            Deleting a tag removes it from every file. No files are deleted.
          </p>

          <div className="card">
            <label>
              Merge
              <select
                value={source}
                onChange={(event) => setSource(event.target.value ? Number(event.target.value) : '')}
              >
                <option value="">Choose…</option>
                {tags.map((tag) => (
                  <option key={tag.id} value={tag.id}>
                    {tag.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              into
              <select
                value={target}
                onChange={(event) => setTarget(event.target.value ? Number(event.target.value) : '')}
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
              onClick={() => {
                if (source === '' || target === '' || source === target) return
                return guard(async () => {
                  await mergeTags(Number(source), Number(target))
                  setSource('')
                  setTarget('')
                })
              }}
            >
              Merge
            </button>
          </div>
        </>
      )}
    </>
  )
}
