import type { Downloads } from '../api/types'

const mb = (bytes: number) => (bytes / 1e6).toFixed(1)
const gb = (bytes: number) => (bytes / 1e9).toFixed(2)

export function InflightTable({ downloads }: { downloads: Downloads }) {
  const { files, stale_runs: stale } = downloads
  if (files.length === 0 && stale.length === 0) return null

  return (
    <div className="card">
      {stale.map((run) => (
        <p key={run.dir} className="error">
          An earlier run left {run.files} unfinished file(s), {gb(run.bytes)} GB,
          in downloads/{run.dir}/.
        </p>
      ))}
      {files.map((file) => (
        <div key={file.name}>
          <strong>{file.name}</strong> <span>{file.phase}</span>{' '}
          <progress value={file.bytes} max={file.total} />{' '}
          <span>
            {mb(file.bytes)} / {mb(file.total)} MB
          </span>{' '}
          <span>{file.destination}</span>
        </div>
      ))}
    </div>
  )
}
