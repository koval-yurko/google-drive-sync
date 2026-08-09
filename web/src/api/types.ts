export interface FolderRef {
  id: string
  name: string
}

export interface Settings {
  photos_root: FolderRef | null
  zip_source: FolderRef | null
  credentials_configured: boolean
}

export interface DriveFolder {
  id: string
  name: string
  mimeType: string
}

export interface ActionSpec {
  id: string
  title: string
  description: string
  order: number
  schema: { type: string; properties?: Record<string, unknown> }
}

export interface Job {
  id: string
  action: string
  params: Record<string, unknown>
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  message: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobEvent {
  id: number
  job_id: string
  ts: number
  level: string
  message: string
}
