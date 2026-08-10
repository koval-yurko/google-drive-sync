import { useEffect, useState } from 'react'
import { getLibraryFile, thumbUrl } from '../api/client'
import type { LibraryFile, TagWithCount } from '../api/types'
import { TagPicker } from './TagPicker'

function when(seconds: number | null): string {
  if (seconds === null) return 'unknown'
  return new Date(seconds * 1000).toISOString().replace('T', ' ').slice(0, 16)
}

function megabytes(bytes: number | null): string {
  return bytes === null ? '—' : `${(bytes / 1e6).toFixed(1)} MB`
}

export function Lightbox({
  driveId,
  tags,
  onClose,
  onChanged,
}: {
  driveId: string
  tags: TagWithCount[]
  onClose: () => void
  onChanged: () => void
}) {
  const [file, setFile] = useState<LibraryFile | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getLibraryFile(driveId).then(setFile).catch((e) => setError(String(e)))
  }, [driveId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="lightbox" role="dialog" aria-modal="true">
      <button type="button" className="close" onClick={onClose}>
        Close
      </button>
      {error && <p className="error">{error}</p>}
      {file && (
        <>
          <h2>{file.name}</h2>

          {file.media_type === 'video' ? (
            // 468 of these are HEVC .MOV, which no browser plays. Drive's own
            // player does, so the lightbox embeds it rather than pretending.
            <iframe
              title={file.name}
              src={`https://drive.google.com/file/d/${file.drive_id}/preview`}
              allow="autoplay"
            />
          ) : (
            <img src={thumbUrl(file.drive_id, 1600)} alt={file.name} />
          )}

          <dl className="meta">
            <dt>Taken</dt>
            <dd>
              {when(file.capture_time)}
              {file.capture_source && <span className="muted"> ({file.capture_source})</span>}
            </dd>
            <dt>Month</dt>
            <dd>{file.month || 'Unfiled'}</dd>
            <dt>Place</dt>
            <dd>
              {file.place ?? '—'}
              {file.country ? `, ${file.country}` : ''}
            </dd>
            <dt>Size</dt>
            <dd>{megabytes(file.size)}</dd>
            <dt>From archive</dt>
            <dd>{file.archive_name ?? 'not from an archive'}</dd>
          </dl>

          {file.duplicate_of && (
            <p className="warn">
              Flagged as a duplicate of something in {file.duplicate_of}:{' '}
              {file.duplicate_reason}. It was uploaded anyway.
            </p>
          )}

          <div className="tag-list">
            {file.tags.length === 0 && <span className="muted">No tags</span>}
            {file.tags.map((tag) => (
              <span key={tag.id} className="chip" style={{ borderColor: tag.color }}>
                {tag.name}
              </span>
            ))}
          </div>

          <TagPicker
            tags={tags}
            driveIds={[file.drive_id]}
            tagCount={file.tags.length}
            onApplied={() => {
              getLibraryFile(driveId).then(setFile).catch((e) => setError(String(e)))
              onChanged()
            }}
          />
        </>
      )}
    </div>
  )
}
