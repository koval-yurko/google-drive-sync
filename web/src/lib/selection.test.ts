import { describe, expect, it } from 'vitest'
import { NO_SELECTION, click, clear, isSelected, selectAll } from './selection'

const ORDER = ['a', 'b', 'c', 'd', 'e']
const plain = { shift: false, meta: false }
const shift = { shift: true, meta: false }
const meta = { shift: false, meta: true }

describe('click', () => {
  it('selects one file and anchors there', () => {
    const state = click(NO_SELECTION, 'b', ORDER, plain)
    expect([...state.ids]).toEqual(['b'])
    expect(state.anchor).toBe('b')
  })

  it('replaces the previous selection', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, plain)
    expect([...state.ids]).toEqual(['d'])
  })
})

describe('meta-click', () => {
  it('adds without clearing', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, meta)
    expect([...state.ids].sort()).toEqual(['b', 'd'])
  })

  it('toggles a selected file off', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'b', ORDER, meta)
    expect([...state.ids]).toEqual([])
  })

  it('moves the anchor', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, meta)
    expect(state.anchor).toBe('d')
  })
})

describe('shift-click', () => {
  it('selects the inclusive range forwards', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, shift)
    expect([...state.ids].sort()).toEqual(['b', 'c', 'd'])
  })

  it('selects the inclusive range backwards', () => {
    let state = click(NO_SELECTION, 'd', ORDER, plain)
    state = click(state, 'b', ORDER, shift)
    expect([...state.ids].sort()).toEqual(['b', 'c', 'd'])
  })

  it('leaves the anchor where it was, so the range can be re-aimed', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, shift)
    state = click(state, 'c', ORDER, shift)
    expect(state.anchor).toBe('b')
    expect([...state.ids].sort()).toEqual(['b', 'c', 'd'])
  })

  it('keeps what was already selected', () => {
    let state = click(NO_SELECTION, 'a', ORDER, plain)
    state = click(state, 'c', ORDER, meta)
    state = click(state, 'e', ORDER, shift)
    expect([...state.ids].sort()).toEqual(['a', 'c', 'd', 'e'])
  })

  it('behaves like a plain click when there is no anchor', () => {
    const state = click(NO_SELECTION, 'c', ORDER, shift)
    expect([...state.ids]).toEqual(['c'])
    expect(state.anchor).toBe('c')
  })

  it('behaves like a plain click when the anchor has been filtered away', () => {
    const orphaned = { anchor: 'zzz', ids: new Set(['zzz']) }
    const state = click(orphaned, 'c', ORDER, shift)
    expect([...state.ids]).toEqual(['c'])
  })
})

describe('selectAll and clear', () => {
  it('selects every id given', () => {
    expect([...selectAll(ORDER).ids]).toEqual(ORDER)
  })

  it('anchors on the first', () => {
    expect(selectAll(ORDER).anchor).toBe('a')
  })

  it('handles an empty result set', () => {
    expect(selectAll([]).anchor).toBe(null)
  })

  it('clears', () => {
    expect([...clear().ids]).toEqual([])
  })
})

describe('isSelected', () => {
  it('answers for a member and a stranger', () => {
    const state = click(NO_SELECTION, 'b', ORDER, plain)
    expect(isSelected(state, 'b')).toBe(true)
    expect(isSelected(state, 'a')).toBe(false)
  })
})
