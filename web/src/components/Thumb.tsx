import { useEffect, useState } from 'react'
import { thumbUrl } from '../api/client'

// Drive renders thumbnails a little after upload, so the proxy answers 202 for
// a while. Three widening retries covers that without hammering the backend.
const RETRY_DELAYS = [4000, 15000, 60000]

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot === -1 ? 'FILE' : name.slice(dot + 1).toUpperCase()
}

export function Thumb({
  driveId,
  name,
  size = 400,
  className,
}: {
  driveId: string
  name: string
  size?: 400 | 1600
  className?: string
}) {
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setAttempt(0)
    setFailed(false)
  }, [driveId, size])

  useEffect(() => {
    if (!failed) return
    const delay = RETRY_DELAYS[attempt]
    if (delay === undefined) return          // out of retries; the placeholder stays
    const timer = setTimeout(() => {
      setAttempt((n) => n + 1)
      setFailed(false)
    }, delay)
    return () => clearTimeout(timer)
  }, [failed, attempt])

  if (failed && attempt >= RETRY_DELAYS.length) {
    return (
      <span className={`thumb thumb-missing ${className ?? ''}`} title={name}>
        {extensionOf(name)}
      </span>
    )
  }

  if (failed) {
    return (
      <span className={`thumb thumb-pending ${className ?? ''}`} title={name}>
        {extensionOf(name)}
      </span>
    )
  }

  return (
    <img
      className={`thumb ${className ?? ''}`}
      // `loading="lazy"` is what lets 1,284 tiles render without a
      // virtualisation library: the browser fetches only what is on screen.
      loading="lazy"
      src={attempt === 0 ? thumbUrl(driveId, size) : `${thumbUrl(driveId, size)}&try=${attempt}`}
      alt={name}
      onError={() => setFailed(true)}
    />
  )
}
