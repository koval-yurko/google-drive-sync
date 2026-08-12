import { useEffect, useState } from 'react'
import { getReviewSummary, listReviewMedia, retryUpload } from '../api/client'
import type { ReviewMedia, ReviewSummary } from '../api/types'

const TILES: Array<{ key: keyof ReviewSummary; label: string }> = [
  { key: 'media', label: 'media files' },
  { key: 'planned', label: 'with a destination' },
  { key: 'duplicates', label: 'already in the destination' },
  { key: 'uploaded', label: 'uploaded' },
  { key: 'errors', label: 'failed' },
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
  const [verdictFilter, setVerdictFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)

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
  }, [duplicatesOnly, reloads])

  const filteredRows = verdictFilter
    ? rows.filter((row) => row.plan_verdict === verdictFilter)
    : rows

  async function onRetry(entryId: number) {
    try {
      await retryUpload(entryId)
      setReloads((n) => n + 1)
    } catch (e) {
      setError(String(e))
    }
  }

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

      <p>
        <label>
          Verdict{' '}
          <select
            value={verdictFilter}
            onChange={(e) => setVerdictFilter(e.target.value)}
          >
            <option value="">All</option>
            <option value="skip">Skip</option>
            <option value="verify">Verify</option>
            <option value="upload">Upload</option>
          </select>
        </label>
      </p>

      <p className="muted">
        Showing {filteredRows.length} of {total}.
      </p>

      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Destination</th>
            <th>Date</th>
            <th>Source</th>
            <th>Already there</th>
            <th>Verdict</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {filteredRows.map((row) => (
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
              <td className={row.duplicate_of ? 'warn' : undefined}>
                {row.duplicate_of ?? '—'}
              </td>
              <td>{row.plan_verdict ?? '—'}</td>
              <td className={row.upload_status === 'error' ? 'error' : undefined}>
                {row.upload_status}
                {row.error ? ` — ${row.error}` : ''}
                {row.upload_status === 'error' && (
                  <>
                    {' '}
                    <button type="button" onClick={() => onRetry(row.entry_id)}>
                      Retry
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && <p>Nothing planned yet. Run Scan, Pair, then Plan.</p>}
    </>
  )
}
