import type { Facet, Facets, TagWithCount } from '../api/types'
import type { LibraryFilters } from '../lib/filters'
import { isEmpty } from '../lib/filters'

function FacetList({
  title,
  facets,
  active,
  onPick,
}: {
  title: string
  facets: Facet[]
  active: string | undefined
  onPick: (value: string | undefined) => void
}) {
  if (facets.length === 0) return null
  return (
    <section className="facet">
      <h3>{title}</h3>
      {facets.map((facet) => (
        <button
          key={facet.value}
          type="button"
          aria-pressed={active === facet.value}
          className={active === facet.value ? 'facet-item active' : 'facet-item'}
          // Clicking the active value again is how you get back to everything.
          onClick={() => onPick(active === facet.value ? undefined : facet.value)}
        >
          <span>{facet.value}</span>
          <span className="count">{facet.count}</span>
        </button>
      ))}
    </section>
  )
}

export function FilterSidebar({
  facets,
  tags,
  filters,
  onChange,
}: {
  facets: Facets | null
  tags: TagWithCount[]
  filters: LibraryFilters
  onChange: (next: LibraryFilters) => void
}) {
  if (facets === null || facets.total === 0) {
    return (
      <aside className="filters">
        <p className="muted">
          Nothing here yet. Run Scan Archives to index the destination, then
          Organize Photos to fill it.
        </p>
      </aside>
    )
  }

  const set = (patch: Partial<LibraryFilters>) => onChange({ ...filters, ...patch })

  return (
    <aside className="filters">
      <label>
        Search
        <input
          type="search"
          value={filters.search ?? ''}
          onChange={(event) => set({ search: event.target.value || undefined })}
        />
      </label>

      <FacetList
        title="Month"
        facets={facets.months}
        active={filters.month}
        onPick={(month) => set({ month })}
      />
      <FacetList
        title="Type"
        facets={facets.types}
        active={filters.mediaType}
        onPick={(value) => set({ mediaType: value as LibraryFilters['mediaType'] })}
      />
      <FacetList
        title="Place"
        facets={facets.places}
        active={filters.place}
        onPick={(place) => set({ place })}
      />
      <FacetList
        title="Country"
        facets={facets.countries}
        active={filters.country}
        onPick={(country) => set({ country })}
      />

      {tags.length > 0 && (
        <section className="facet">
          <h3>Tag</h3>
          {tags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              aria-pressed={filters.tagId === tag.id}
              className={filters.tagId === tag.id ? 'facet-item active' : 'facet-item'}
              onClick={() =>
                set({ tagId: filters.tagId === tag.id ? undefined : tag.id })
              }
            >
              <span className="swatch" style={{ background: tag.color }} />
              <span>{tag.name}</span>
              <span className="count">{tag.file_count}</span>
            </button>
          ))}
        </section>
      )}

      {facets.duplicates > 0 && (
        <section className="facet">
          <h3>Flagged</h3>
          <button
            type="button"
            aria-pressed={filters.duplicates === true}
            className={filters.duplicates ? 'facet-item active' : 'facet-item'}
            onClick={() => set({ duplicates: filters.duplicates ? undefined : true })}
          >
            <span>duplicates</span>
            <span className="count">{facets.duplicates}</span>
          </button>
        </section>
      )}

      {!isEmpty(filters) && (
        <button type="button" className="link" onClick={() => onChange({})}>
          Clear filters
        </button>
      )}
    </aside>
  )
}
