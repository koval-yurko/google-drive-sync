import { describe as group, expect, it } from 'vitest'
import { EMPTY_FILTERS, describe, isEmpty, toQuery } from './filters'

group('toQuery', () => {
  it('is empty for no filters', () => {
    expect(toQuery(EMPTY_FILTERS).toString()).toBe('')
  })

  it('maps camelCase to the API snake_case', () => {
    const query = toQuery({ mediaType: 'image', tagId: 3 })
    expect(query.get('media_type')).toBe('image')
    expect(query.get('tag_id')).toBe('3')
  })

  it('sends duplicates only when true', () => {
    expect(toQuery({ duplicates: true }).get('duplicates')).toBe('true')
    expect(toQuery({ duplicates: false }).has('duplicates')).toBe(false)
  })

  it('drops an empty search string', () => {
    expect(toQuery({ search: '' }).has('search')).toBe(false)
    expect(toQuery({ search: 'img' }).get('search')).toBe('img')
  })

  it('keeps a tag id of zero out, since ids start at one', () => {
    expect(toQuery({ tagId: undefined }).has('tag_id')).toBe(false)
  })
})

group('isEmpty', () => {
  it('is true for no filters', () => {
    expect(isEmpty(EMPTY_FILTERS)).toBe(true)
  })

  it('is false once anything is set', () => {
    expect(isEmpty({ month: '2025-05' })).toBe(false)
    expect(isEmpty({ duplicates: true })).toBe(false)
  })

  it('ignores a false duplicates flag', () => {
    expect(isEmpty({ duplicates: false })).toBe(true)
  })
})

group('describe', () => {
  it('names each active filter for the chip row', () => {
    expect(describe({ month: '2025-05', mediaType: 'video', duplicates: true }))
      .toEqual(['2025-05', 'video', 'duplicates'])
  })

  it('is empty when nothing is filtered', () => {
    expect(describe(EMPTY_FILTERS)).toEqual([])
  })
})
