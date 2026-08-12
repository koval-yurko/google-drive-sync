import type {
  ActionSpec,
  Downloads,
  DriveFolder,
  Facets,
  FolderRef,
  Job,
  JobEvent,
  LibraryFile,
  ReviewMedia,
  ReviewSummary,
  Settings,
  TagWithCount,
} from './types'
import { toQuery, type LibraryFilters } from '../lib/filters'

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${response.status}: ${body}`)
  }
  return (await response.json()) as T
}

export const getSettings = () => request<Settings>('/api/settings')

export const putSetting = (key: string, folder: FolderRef) =>
  request<FolderRef>(`/api/settings/${key}`, {
    method: 'PUT',
    body: JSON.stringify(folder),
  })

export const listFolders = (parent = 'root') =>
  request<{ parent: FolderRef; folders: DriveFolder[] }>(
    `/api/drive/folders?parent=${encodeURIComponent(parent)}`,
  )

export const listActions = () => request<ActionSpec[]>('/api/actions')

export const runAction = (id: string, params: Record<string, unknown>) =>
  request<Job>(`/api/actions/${id}/run`, {
    method: 'POST',
    body: JSON.stringify(params),
  })

export const listJobs = () => request<Job[]>('/api/jobs')

export const getJob = (id: string) => request<Job>(`/api/jobs/${id}`)

export const cancelJob = (id: string) =>
  request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' })

export const resumeJob = (id: string) =>
  request<Job>(`/api/jobs/${id}/resume`, { method: 'POST' })

export const getDownloads = () => request<Downloads>('/api/downloads')

export const getJobEvents = (id: string, after = 0) =>
  request<JobEvent[]>(`/api/jobs/${id}/events?after=${after}`)

export function streamJob(
  id: string,
  onMessage: (payload: Record<string, unknown>) => void,
  onEnd: () => void,
): () => void {
  const source = new EventSource(`/api/jobs/${id}/stream`)
  source.addEventListener('message', (event) => onMessage(JSON.parse(event.data)))
  source.addEventListener('end', () => {
    source.close()
    onEnd()
  })
  source.onerror = () => {
    source.close()
    onEnd()
  }
  return () => source.close()
}

export const getReviewSummary = () => request<ReviewSummary>('/api/review/summary')

export const listReviewMedia = (opts: {
  limit?: number
  offset?: number
  folder?: string
  duplicatesOnly?: boolean
} = {}) => {
  const params = new URLSearchParams()
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts.offset !== undefined) params.set('offset', String(opts.offset))
  if (opts.folder) params.set('folder', opts.folder)
  if (opts.duplicatesOnly) params.set('duplicates_only', 'true')
  return request<{ total: number; rows: ReviewMedia[] }>(
    `/api/review/media?${params.toString()}`,
  )
}

export const retryUpload = (entryId: number) =>
  request<{ entry_id: number; upload_status: string }>(
    `/api/review/retry/${entryId}`,
    { method: 'POST' },
  )

export const listLibraryFiles = (
  filters: LibraryFilters,
  page: { limit?: number; offset?: number } = {},
) => {
  const params = toQuery(filters)
  if (page.limit !== undefined) params.set('limit', String(page.limit))
  if (page.offset !== undefined) params.set('offset', String(page.offset))
  return request<{ total: number; rows: LibraryFile[] }>(
    `/api/library/files?${params.toString()}`,
  )
}

export const listLibraryIds = (filters: LibraryFilters) =>
  request<{ ids: string[] }>(`/api/library/ids?${toQuery(filters).toString()}`)
    .then((body) => body.ids)

export const getFacets = () => request<Facets>('/api/library/facets')

export const getLibraryFile = (driveId: string) =>
  request<LibraryFile>(`/api/library/file/${encodeURIComponent(driveId)}`)

/** The backend proxies Drive's render; Chrome cannot decode HEIC itself. */
export const thumbUrl = (driveId: string, size: 400 | 1600 = 400) =>
  `/api/thumb/${encodeURIComponent(driveId)}?size=${size}`

export const listTags = () => request<TagWithCount[]>('/api/tags')

export const createTag = (name: string, color?: string) =>
  request<TagWithCount>('/api/tags', {
    method: 'POST',
    body: JSON.stringify(color ? { name, color } : { name }),
  })

export const patchTag = (id: number, patch: { name?: string; color?: string }) =>
  request<TagWithCount>(`/api/tags/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })

export const deleteTag = (id: number) =>
  request<{ deleted: number }>(`/api/tags/${id}`, { method: 'DELETE' })

export const mergeTags = (sourceId: number, targetId: number) =>
  request<{ moved: number; target: TagWithCount }>('/api/tags/merge', {
    method: 'POST',
    body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
  })

export const addFilesToTag = (id: number, driveIds: string[]) =>
  request<{ added: number }>(`/api/tags/${id}/files`, {
    method: 'POST',
    body: JSON.stringify({ drive_ids: driveIds }),
  })

/** A POST, not a DELETE with a body: 1,284 ids do not fit in a URL. */
export const removeFilesFromTag = (id: number, driveIds: string[]) =>
  request<{ removed: number }>(`/api/tags/${id}/files/remove`, {
    method: 'POST',
    body: JSON.stringify({ drive_ids: driveIds }),
  })
