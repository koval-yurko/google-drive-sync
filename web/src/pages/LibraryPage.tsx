import { useEffect, useMemo, useState } from 'react'
import { getFacets, listLibraryFiles, listLibraryIds, listTags } from '../api/client'
import type { Facets, LibraryFile, TagWithCount } from '../api/types'
import { FilterSidebar } from '../components/FilterSidebar'
import { TagPicker } from '../components/TagPicker'
import { Thumb } from '../components/Thumb'
import type { LibraryFilters } from '../lib/filters'
import { NO_SELECTION, click, clear, isSelected, selectAll } from '../lib/selection'
import type { Selection } from '../lib/selection'

const PAGE = 200

/** Rows arrive newest month first, so grouping is one pass, not a sort. */
function byMonth(rows: LibraryFile[]): Array<[string, LibraryFile[]]> {
  const groups: Array<[string, LibraryFile[]]> = []
  for (const row of rows) {
    const last = groups[groups.length - 1]
    if (last && last[0] === row.month) last[1].push(row)
    else groups.push([row.month || 'Unfiled', [row]])
  }
  return groups
}

export function LibraryPage() {
  const [filters, setFilters] = useState<LibraryFilters>({})
  const [rows, setRows] = useState<LibraryFile[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [tags, setTags] = useState<TagWithCount[]>([])
  const [selection, setSelection] = useState<Selection>(NO_SELECTION)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getFacets().then(setFacets).catch((e) => setError(String(e)))
    listTags().then(setTags).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // A selection made under one filter means nothing under the next.
    setSelection(clear())
    listLibraryFiles(filters, { limit: PAGE, offset: 0 })
      .then((result) => {
        if (cancelled) return
        setRows(result.rows)
        setTotal(result.total)
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [filters])

  const ordered = useMemo(() => rows.map((row) => row.drive_id), [rows])

  async function loadMore() {
    const result = await listLibraryFiles(filters, { limit: PAGE, offset: rows.length })
    setRows((current) => [...current, ...result.rows])
    setTotal(result.total)
  }

  async function onSelectAll() {
    try {
      setSelection(selectAll(await listLibraryIds(filters)))
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="library">
      <FilterSidebar
        facets={facets}
        tags={tags}
        filters={filters}
        onChange={setFilters}
      />

      <section className="grid-pane">
        <header className="grid-header">
          <h2>Library</h2>
          <p className="muted">
            {total} file{total === 1 ? '' : 's'}
            {rows.length < total ? ` — showing ${rows.length}` : ''}
          </p>
          {selection.ids.size > 0 && (
            <p className="selection-count">{selection.ids.size} selected</p>
          )}
          <button type="button" onClick={onSelectAll}>
            Select all matching this filter
          </button>
          {selection.ids.size > 0 && (
            <button type="button" onClick={() => setSelection(clear())}>
              Clear selection
            </button>
          )}
          <TagPicker
            tags={tags}
            driveIds={[...selection.ids]}
            onApplied={() => {
              listTags().then(setTags).catch((e) => setError(String(e)))
              listLibraryFiles(filters, { limit: rows.length || PAGE, offset: 0 })
                .then((result) => {
                  setRows(result.rows)
                  setTotal(result.total)
                })
                .catch((e) => setError(String(e)))
            }}
          />
        </header>

        {error && <p className="error">{error}</p>}
        {!loading && rows.length === 0 && (
          <p className="muted">No files match these filters.</p>
        )}

        {byMonth(rows).map(([month, files]) => (
          <section key={month}>
            <h3>{month}</h3>
            <div className="grid">
              {files.map((file) => (
                <div
                  key={file.drive_id}
                  className={isSelected(selection, file.drive_id) ? 'tile selected' : 'tile'}
                  aria-selected={isSelected(selection, file.drive_id)}
                  onClick={(event) =>
                    setSelection((current) =>
                      click(current, file.drive_id, ordered, {
                        shift: event.shiftKey,
                        meta: event.metaKey || event.ctrlKey,
                      }),
                    )
                  }
                >
                  <Thumb driveId={file.drive_id} name={file.name} />
                  <span className="tile-name">{file.name}</span>
                  {file.duplicate_of && <span className="badge">duplicate</span>}
                  {file.tags.map((tag) => (
                    <span key={tag.id} className="swatch" style={{ background: tag.color }} />
                  ))}
                </div>
              ))}
            </div>
          </section>
        ))}

        {rows.length < total && (
          <button type="button" onClick={loadMore}>
            Load more
          </button>
        )}
      </section>
      {/* Task 15 mounts the lightbox here. */}
    </div>
  )
}
