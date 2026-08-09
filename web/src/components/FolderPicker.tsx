import { useEffect, useState } from 'react'
import { listFolders } from '../api/client'
import type { DriveFolder, FolderRef } from '../api/types'

interface Props {
  title: string
  onSelect: (folder: FolderRef) => void
  onCancel: () => void
}

export function FolderPicker({ title, onSelect, onCancel }: Props) {
  const [current, setCurrent] = useState<FolderRef>({ id: 'root', name: 'My Drive' })
  const [folders, setFolders] = useState<DriveFolder[]>([])
  const [trail, setTrail] = useState<FolderRef[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listFolders(current.id)
      .then((result) => {
        if (cancelled) return
        setFolders(result.folders)
        setCurrent(result.parent)
        setError(null)
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.id])

  const enter = (folder: DriveFolder) => {
    setTrail([...trail, current])
    setCurrent({ id: folder.id, name: folder.name })
  }

  const goBack = () => {
    const previous = trail[trail.length - 1]
    if (!previous) return
    setTrail(trail.slice(0, -1))
    setCurrent(previous)
  }

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3>{title}</h3>
        <p>
          <strong>{current.name}</strong>
          {trail.length > 0 && (
            <button onClick={goBack} style={{ marginLeft: '0.5rem' }}>
              Back
            </button>
          )}
        </p>
        {error && <p className="error">{error}</p>}
        {loading && <p>Loading…</p>}
        <ul className="picker-list">
          {folders.map((folder) => (
            <li key={folder.id}>
              <button onClick={() => enter(folder)}>{folder.name}</button>
            </li>
          ))}
          {!loading && folders.length === 0 && <li>No subfolders</li>}
        </ul>
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          <button onClick={onCancel}>Cancel</button>
          <button onClick={() => onSelect(current)} disabled={current.id === 'root'}>
            Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}
