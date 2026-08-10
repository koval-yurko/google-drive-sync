import type { MediaType } from '../api/types'

/**
 * What the Library is currently showing. Kept as plain data with no React in
 * sight, so the URL builder can be tested on its own — the backend reads
 * snake_case and the UI writes camelCase, and that seam is easy to get wrong.
 */
export interface LibraryFilters {
  month?: string
  place?: string
  country?: string
  mediaType?: MediaType
  tagId?: number
  duplicates?: boolean
  search?: string
}

export const EMPTY_FILTERS: LibraryFilters = {}

export function toQuery(filters: LibraryFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.place) params.set('place', filters.place)
  if (filters.country) params.set('country', filters.country)
  if (filters.mediaType) params.set('media_type', filters.mediaType)
  if (filters.tagId !== undefined) params.set('tag_id', String(filters.tagId))
  if (filters.duplicates) params.set('duplicates', 'true')
  if (filters.search) params.set('search', filters.search)
  return params
}

export function isEmpty(filters: LibraryFilters): boolean {
  return toQuery(filters).toString() === ''
}

/** One short label per active filter, for the chip row above the grid. */
export function describe(filters: LibraryFilters): string[] {
  const labels: string[] = []
  if (filters.month) labels.push(filters.month)
  if (filters.place) labels.push(filters.place)
  if (filters.country) labels.push(filters.country)
  if (filters.mediaType) labels.push(filters.mediaType)
  if (filters.duplicates) labels.push('duplicates')
  if (filters.search) labels.push(`"${filters.search}"`)
  return labels
}
