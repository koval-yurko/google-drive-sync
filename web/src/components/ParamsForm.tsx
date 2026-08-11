import type { ActionSchema, ParamProperty } from '../api/types'

/** Raw form state: what the user typed, before any coercion. */
export type ParamValues = Record<string, string | boolean>

const kindOf = (prop: ParamProperty): string =>
  prop.type ?? prop.anyOf?.find((p) => p.type !== 'null')?.type ?? 'string'

const isNullable = (prop: ParamProperty): boolean =>
  prop.anyOf?.some((p) => p.type === 'null') ?? false

/** Coerce the raw form state into the payload the action expects.
 *  Untouched fields are omitted so the server's defaults stay in charge. */
export function toPayload(
  schema: ActionSchema,
  values: ParamValues,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (!(key in values)) continue
    const raw = values[key]
    const kind = kindOf(prop)
    if (kind === 'boolean') {
      payload[key] = raw === true
    } else if (kind === 'integer' || kind === 'number') {
      const text = String(raw).trim()
      if (text === '') {
        if (isNullable(prop)) payload[key] = null
        continue
      }
      const parsed = Number(text)
      if (Number.isFinite(parsed)) payload[key] = parsed
    } else {
      payload[key] = raw
    }
  }
  return payload
}

interface Props {
  schema: ActionSchema
  values: ParamValues
  onChange: (values: ParamValues) => void
}

export function ParamsForm({ schema, values, onChange }: Props) {
  const entries = Object.entries(schema.properties ?? {})
  if (entries.length === 0) return null

  return (
    <div className="params">
      {entries.map(([key, prop]) => {
        const label = prop.title ?? key
        const kind = kindOf(prop)
        if (kind === 'boolean') {
          const checked = (values[key] ?? prop.default ?? false) === true
          return (
            <label key={key} className="params-check">
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => onChange({ ...values, [key]: e.target.checked })}
              />
              {label}
            </label>
          )
        }
        const fallback = prop.default == null ? '' : String(prop.default)
        const value = String(values[key] ?? fallback)
        return (
          <label key={key}>
            {label}
            <input
              type={kind === 'integer' || kind === 'number' ? 'number' : 'text'}
              value={value}
              onChange={(e) => onChange({ ...values, [key]: e.target.value })}
            />
          </label>
        )
      })}
    </div>
  )
}
