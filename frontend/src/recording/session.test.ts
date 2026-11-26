import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { prepareMicrophone, RecorderSession, type RecordingHooks } from './session'

vi.mock('../services/officeApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/officeApi')>()
  return { ...actual, localMode: true }
})

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
afterEach(() => vi.unstubAllGlobals())
const hooks = (): RecordingHooks => ({ authorize: vi.fn(), confirm: vi.fn(), heartbeat: vi.fn().mockResolvedValue(undefined), event: vi.fn().mockResolvedValue(undefined), finalize: vi.fn().mockResolvedValue(undefined) })

describe('microphone lifecycle', () => {
  it('releases a granted microphone when device enumeration fails', async () => {
    const stop = vi.fn()
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }), enumerateDevices: vi.fn().mockRejectedValue(new Error('device failure')) } })
    await expect(prepareMicrophone()).rejects.toThrow('device failure')
    expect(stop).toHaveBeenCalledOnce()
  })
  it('releases permission that arrives after the recorder has been disposed', async () => {
    const stop = vi.fn()
    let grant!: (stream: unknown) => void
    const getUserMedia = vi.fn(() => new Promise(resolve => { grant = resolve }))
    vi.stubGlobal('navigator', {
      locks: { request: vi.fn((_name, _options, callback) => callback({})) },
      mediaDevices: { getUserMedia, enumerateDevices: vi.fn().mockResolvedValue([]) },
    })
    const audioContext = vi.fn()
    vi.stubGlobal('AudioContext', audioContext)
    const recorder = new RecorderSession({ scope: { userId: 'alice', workspaceId: 'office' }, conversationId: 'late-permission', partId: 'part-1', leaseToken: 'lease-1', hooks: hooks(), onState: vi.fn() })
    const starting = recorder.start()
    const rejection = expect(starting).rejects.toThrow('recording page was closed')
    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce())
    await recorder.dispose()
    grant({ getTracks: () => [{ stop }] })
    await rejection
    expect(stop).toHaveBeenCalledOnce()
    expect(audioContext).not.toHaveBeenCalled()
  })
})

describe('private audio upload destinations', () => {
  async function staged(id: string) {
    const { putChunk, scopeKey } = await import('./outbox')
    const scope = { userId: 'alice', workspaceId: 'office' }
    const key = `${scopeKey(scope)}/${id}`
    const recovery = { key, scope: scopeKey(scope), conversationId: id, partId: 'part-1', leaseToken: 'lease-1', totalSamples: 1, chunkCount: 1, stopped: true, updatedAt: new Date().toISOString() }
    const chunk = { key: `${key}/0`, scope: recovery.scope, audio: new ArrayBuffer(46), meta: { conversation_id: id, part_id: 'part-1', lease_token: 'lease-1', sequence: 0, start_sample: 0, sample_count: 1, sample_rate: 16000 as const, bytes: 46, sha256: 'a'.repeat(64), mime: 'audio/wav' as const } }
    await putChunk(scope, chunk, recovery)
    return scope
  }
  it('uploads only to the configured loopback API audio route in local mode', async () => {
    const { apiBase } = await import('../services/officeApi')
    const { uploadPending } = await import('./session')
    const scope = await staged('local-transfer')
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 })
    vi.stubGlobal('fetch', fetch)
    const control = hooks()
    control.authorize = vi.fn().mockResolvedValue({ upload_url: `${apiBase}/api/v1/local-audio/local-transfer/part-1/0?token=synthetic`, method: 'PUT' })
    await uploadPending(scope, 'local-transfer', control)
    expect(fetch).toHaveBeenCalledOnce()
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'PUT', credentials: 'omit', redirect: 'error' })
  })
  it.each(['https://foreign.example/api/v1/local-audio/conversation/part/0', 'http://127.0.0.1:8010/private/other', 'http://127.0.0.1:9000/api/v1/local-audio/conversation/part/0', 'http://user:password@127.0.0.1:8010/api/v1/local-audio/conversation/part/0'])('rejects foreign origins, wrong paths and embedded credentials (%s)', async url => {
    const { uploadPending } = await import('./session')
    const scope = await staged('invalid-target')
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const control = hooks(); control.authorize = vi.fn().mockResolvedValue({ upload_url: url })
    await expect(uploadPending(scope, 'invalid-target', control)).rejects.toThrow('invalid storage destination')
    expect(fetch).not.toHaveBeenCalled()
    const { pending } = await import('./outbox')
    expect(await pending(scope, 'invalid-target')).toHaveLength(1)
  })
})
