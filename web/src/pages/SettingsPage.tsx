import { useEffect, useState } from 'react'
import { getSettings, putSetting } from '../api/client'
import type { FolderRef, Settings } from '../api/types'
import { FolderPicker } from '../components/FolderPicker'

const FIELDS = [
  {
    key: 'photos_root',
    label: 'Global Photos folder',
    help: 'Where organised photos will be placed, in date-range subfolders of ~100 files.',
  },
  {
    key: 'zip_source',
    label: 'ZIP source folder',
    help: 'The Drive folder holding your Takeout archives.',
  },
] as const

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [picking, setPicking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = () => getSettings().then(setSettings).catch((e) => setError(String(e)))

  useEffect(() => {
    reload()
  }, [])

  const choose = async (key: string, folder: FolderRef) => {
    setPicking(null)
    try {
      await putSetting(key, folder)
      await reload()
    } catch (e) {
      setError(String(e))
    }
  }

  if (!settings) return <p>Loading…</p>

  return (
    <>
      <h2>Settings</h2>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <strong>Drive credentials</strong>
        <p>
          {settings.credentials_configured
            ? 'Authorised — token.json found.'
            : 'Not authorised — token.json is missing from the project root.'}
        </p>
      </div>

      {FIELDS.map((field) => {
        const value = settings[field.key]
        return (
          <div className="card" key={field.key}>
            <strong>{field.label}</strong>
            <p>{field.help}</p>
            <p>{value ? `${value.name} (${value.id})` : <em>Not configured</em>}</p>
            <button onClick={() => setPicking(field.key)}>
              {value ? 'Change' : 'Choose folder'}
            </button>
          </div>
        )
      })}

      {picking && (
        <FolderPicker
          title={FIELDS.find((f) => f.key === picking)!.label}
          onSelect={(folder) => choose(picking, folder)}
          onCancel={() => setPicking(null)}
        />
      )}
    </>
  )
}
