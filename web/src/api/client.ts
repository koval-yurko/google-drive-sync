import type {
  ActionSpec,
  DriveFolder,
  FolderRef,
  Job,
  JobEvent,
  ReviewMedia,
  ReviewSummary,
  Settings,
} from './types'

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
