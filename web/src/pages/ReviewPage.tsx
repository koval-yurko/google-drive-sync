import { useEffect, useState } from 'react'
import { getReviewSummary, listReviewMedia } from '../api/client'
import type { ReviewMedia, ReviewSummary } from '../api/types'

const TILES: Array<{ key: keyof ReviewSummary; label: string }> = [
  { key: 'media', label: 'media files' },
  { key: 'planned', label: 'with a destination' },
  { key: 'duplicates', label: 'already in the destination' },
  { key: 'with_place', label: 'with a place' },
]

function when(row: ReviewMedia): string {
  if (row.capture_time === null) return '—'
  return new Date(row.capture_time * 1000).toISOString().slice(0, 10)
}

export function ReviewPage() {
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [rows, setRows] = useState<ReviewMedia[]>([])
  const [total, setTotal] = useState(0)
  const [duplicatesOnly, setDuplicatesOnly] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getReviewSummary().then(setSummary).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    listReviewMedia({ limit: 500, duplicatesOnly })
      .then((result) => {
        setRows(result.rows)
        setTotal(result.total)
      })
      .catch((e) => setError(String(e)))
  }, [duplicatesOnly])

  return (
    <>
      <h2>Review Plan</h2>
      {error && <p className="error">{error}</p>}

      {summary && (
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          {TILES.map((tile) => (
            <div className="card" key={tile.key} style={{ minWidth: '10rem' }}>
              <div style={{ fontSize: '1.6rem', fontVariantNumeric: 'tabular-nums' }}>
                {summary[tile.key]}
              </div>
              <div className="muted">{tile.label}</div>
            </div>
          ))}
        </div>
      )}

      {summary && summary.duplicates > 0 && (
        <p className="warn">
          {summary.duplicates} file(s) already exist in the destination by name. They
          will still be uploaded — deduplication is a separate, later step.
        </p>
      )}

      <p>
        <label>
          <input
            type="checkbox"
            checked={duplicatesOnly}
            onChange={(e) => setDuplicatesOnly(e.target.checked)}
          />{' '}
          Only files already in the destination
        </label>
      </p>

      <p className="muted">
        Showing {rows.length} of {total}.
      </p>

      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Destination</th>
            <th>Date</th>
            <th>Source</th>
            <th>Place</th>
            <th>Already there</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.archive_name}/${row.path}`}>
              <td>{row.name}</td>
              <td>
                {row.target_folder ?? '—'}
                {row.target_name !== row.name && row.target_name
                  ? ` / ${row.target_name}`
                  : ''}
              </td>
              <td>{when(row)}</td>
              <td>{row.capture_source ?? '—'}</td>
              <td>{row.place ?? '—'}</td>
              <td className={row.duplicate_of ? 'warn' : undefined}>
                {row.duplicate_of ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && <p>Nothing planned yet. Run Scan, Pair, then Plan.</p>}
    </>
  )
}
