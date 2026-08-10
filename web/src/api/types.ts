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

export interface ReviewSummary {
  media: number
  planned: number
  unplanned: number
  duplicates: number
  with_place: number
  with_sidecar: number
  pending: number
  uploaded: number
  errors: number
  archives: number
  entries: number
  drive_files: number
}

export interface ReviewMedia {
  entry_id: number
  name: string
  path: string
  archive_name: string
  target_folder: string | null
  target_name: string | null
  capture_time: number | null
  capture_source: string | null
  place: string | null
  country: string | null
  duplicate_of: string | null
  duplicate_reason: string | null
  upload_status: string
  error: string | null
  drive_file_id: string | null
  size: number
}
