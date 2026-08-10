import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addFilesToTag,
  getSettings,
  listFolders,
  listLibraryFiles,
  listLibraryIds,
  removeFilesFromTag,
  runAction,
  thumbUrl,
} from './client'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(payload: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('api client', () => {
  it('fetches settings from /api/settings', async () => {
    const fetchMock = stubFetch({ photos_root: null, zip_source: null, credentials_configured: true })
    const settings = await getSettings()
    expect(fetchMock).toHaveBeenCalledWith('/api/settings', expect.anything())
    expect(settings.credentials_configured).toBe(true)
  })

  it('passes the parent folder as a query parameter', async () => {
    const fetchMock = stubFetch({ parent: { id: 'zips', name: 'Z' }, folders: [] })
    await listFolders('zips')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/drive/folders?parent=zips')
  })

  it('defaults the parent to root', async () => {
    const fetchMock = stubFetch({ parent: { id: 'root', name: 'My Drive' }, folders: [] })
    await listFolders()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/drive/folders?parent=root')
  })

  it('posts params when running an action', async () => {
    const fetchMock = stubFetch({ id: 'j1', action: 'check_connection', status: 'queued' })
    await runAction('check_connection', {})
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/actions/check_connection/run')
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{}')
  })

  it('throws on a failed response', async () => {
    stubFetch({ detail: 'boom' }, false, 500)
    await expect(getSettings()).rejects.toThrow(/boom/)
  })
})

/** Like stubFetch, but hands back a real Response so `init` keeps its type. */
function stubResponse(payload: unknown) {
  const fetchMock = vi.fn(
    async (_url: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(payload)),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('library client', () => {
  it('builds a filtered files URL', async () => {
    const fetchMock = stubResponse({ total: 0, rows: [] })

    await listLibraryFiles({ month: '2025-05', mediaType: 'video' }, { limit: 50 })

    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('/api/library/files?')
    expect(url).toContain('month=2025-05')
    expect(url).toContain('media_type=video')
    expect(url).toContain('limit=50')
  })

  it('omits filters that are not set', async () => {
    const fetchMock = stubResponse({ total: 0, rows: [] })

    await listLibraryFiles({})

    expect(String(fetchMock.mock.calls[0][0])).not.toContain('month=')
  })

  it('unwraps the ids envelope', async () => {
    stubResponse({ ids: ['a', 'b'] })
    expect(await listLibraryIds({})).toEqual(['a', 'b'])
  })

  it('builds a thumbnail URL at the grid size by default', () => {
    expect(thumbUrl('d1')).toBe('/api/thumb/d1?size=400')
    expect(thumbUrl('d1', 1600)).toBe('/api/thumb/d1?size=1600')
  })

  it('escapes a drive id in the thumbnail URL', () => {
    expect(thumbUrl('a/b')).toBe('/api/thumb/a%2Fb?size=400')
  })

  it('posts drive ids when tagging in bulk', async () => {
    const fetchMock = stubResponse({ added: 2 })

    await addFilesToTag(7, ['d1', 'd2'])

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/tags/7/files')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ drive_ids: ['d1', 'd2'] })
  })

  it('removes through a POST, because a DELETE body is not dependable', async () => {
    const fetchMock = stubResponse({ removed: 1 })

    await removeFilesFromTag(7, ['d1'])

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/tags/7/files/remove')
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('POST')
  })
})
