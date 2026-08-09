import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSettings, listFolders, runAction } from './client'

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
