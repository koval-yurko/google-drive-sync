/**
 * Grid selection maths, kept away from React.
 *
 * The rules are the ones every file manager uses: plain click replaces, meta
 * toggles, shift extends from the anchor. The anchor is what makes repeated
 * shift-clicks feel right — it stays put so the range can be re-aimed, rather
 * than walking forward with each click.
 */

export interface Selection {
  anchor: string | null
  ids: ReadonlySet<string>
}

export const NO_SELECTION: Selection = { anchor: null, ids: new Set() }

export interface ClickModifiers {
  shift: boolean
  meta: boolean
}

export function click(
  state: Selection,
  id: string,
  ordered: string[],
  mods: ClickModifiers,
): Selection {
  if (mods.meta) {
    const ids = new Set(state.ids)
    if (ids.has(id)) ids.delete(id)
    else ids.add(id)
    return { anchor: id, ids }
  }

  if (mods.shift && state.anchor !== null) {
    const from = ordered.indexOf(state.anchor)
    const to = ordered.indexOf(id)
    // The anchor can be filtered out from under us; fall back to a plain click
    // rather than selecting a nonsensical range.
    if (from !== -1 && to !== -1) {
      const [start, end] = from <= to ? [from, to] : [to, from]
      const ids = new Set(state.ids)
      for (const each of ordered.slice(start, end + 1)) ids.add(each)
      return { anchor: state.anchor, ids }
    }
  }

  return { anchor: id, ids: new Set([id]) }
}

/** Everything matching the current filter, not just the rendered rows. */
export function selectAll(ids: string[]): Selection {
  return { anchor: ids[0] ?? null, ids: new Set(ids) }
}

export function clear(): Selection {
  return { anchor: null, ids: new Set() }
}

export function isSelected(state: Selection, id: string): boolean {
  return state.ids.has(id)
}
