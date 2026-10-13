import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ getSession: vi.fn(), createClient: vi.fn() }))
vi.mock('@supabase/supabase-js', () => ({ createClient: mocks.createClient }))
const load = () => import('./officeApi')

beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks()
  vi.stubEnv('VITE_SUPABASE_URL', ''); vi.stubEnv('VITE_SUPABASE_PUBLISHABLE_KEY', ''); vi.stubEnv('VITE_SUPABASE_ANON_KEY', '')
  vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8010'); vi.stubEnv('VITE_LOCAL_MODE', ''); vi.stubEnv('DEV', true)
  vi.stubGlobal('location', { hostname: 'localhost' })
  mocks.getSession.mockResolvedValue({ data: { session: { access_token: 'fixture-access-token' } } })
  mocks.createClient.mockReturnValue({ auth: { getSession: mocks.getSession } })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200 })))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); vi.unstubAllGlobals() })
function cloud() {
  vi.stubEnv('VITE_SUPABASE_URL', 'https://fixture.supabase.co'); vi.stubEnv('VITE_SUPABASE_PUBLISHABLE_KEY', 'fixture-public-key')
  vi.stubEnv('VITE_API_BASE_URL', 'https://fixture-api.example.test')
}

describe('local and authenticated request boundaries', () => {
  it('supports explicit loopback development without cloud sessions or bearer credentials', async () => {
    const api = await load()
    expect(api.localMode).toBe(true); expect(api.configured).toBe(false)
    await api.request('/enquiries', 'workspace-a')
    expect(mocks.createClient).not.toHaveBeenCalled(); expect(mocks.getSession).not.toHaveBeenCalled()
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('http://127.0.0.1:8010/api/v1/enquiries')
    expect(new Headers(init?.headers).get('Authorization')).toBeNull()
    expect(new Headers(init?.headers).get('X-Workspace-ID')).toBe('workspace-a')
    expect(init?.credentials).toBe('omit')
  })
  it('fails closed on a public deployment with missing configuration', async () => {
    vi.stubGlobal('location', { hostname: 'public.example.test' })
    const api = await load()
    expect(api.localMode).toBe(false)
    await expect(api.request('/enquiries')).rejects.toMatchObject({ status: 503, code: 'configuration_missing' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('prevents local requests to an external API address', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://untrusted.example.test')
    const api = await load()
    await expect(api.request('/connections')).rejects.toMatchObject({ status: 503, code: 'configuration_missing' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('does not enable a no-login fallback for a production build unless explicitly enabled on loopback', async () => {
    vi.stubEnv('DEV', false)
    const api = await load()
    expect(api.localMode).toBe(false)
    await expect(api.request('/enquiries')).rejects.toMatchObject({ code: 'configuration_missing' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('adds only the current cloud session, workspace and stable save key to an authenticated request', async () => {
    cloud(); const api = await load()
    await api.request('/enquiries', 'workspace-a', api.json('POST', { customerName: 'Synthetic customer' }, 'stable-key-123'))
    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer fixture-access-token')
    expect(headers.get('X-Workspace-ID')).toBe('workspace-a')
    expect(headers.get('Idempotency-Key')).toBe('stable-key-123')
    expect(headers.get('Content-Type')).toBe('application/json')
  })
  it('rejects a missing cloud session before fetching private records', async () => {
    cloud(); mocks.getSession.mockResolvedValue({ data: { session: null } }); const api = await load()
    await expect(api.request('/enquiries', 'workspace-a')).rejects.toMatchObject({ status: 401, code: 'session_expired' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('uses safe provider error messages without displaying submitted credential text', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'invalid_credentials', message: 'fixture-private-provider-key' } }), { status: 401 }))
    const api = await load()
    await expect(api.request('/connections')).rejects.toThrow('The provider rejected the saved API key')
    await expect(api.request('/connections')).rejects.not.toThrow('fixture-private-provider-key')
  })
  it('maps backend network failure to an actionable local message', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    const api = await load()
    await expect(api.request('/enquiries')).rejects.toMatchObject({ status: 503, code: 'api_unreachable' })
    await expect(api.request('/enquiries')).rejects.toThrow('Start GuruConnect with npm run dev')
  })
  it('honours an already-aborted request without retrieving a session or fetching', async () => {
    cloud(); const api = await load(); const controller = new AbortController(); controller.abort(new DOMException('cancelled', 'AbortError'))
    await expect(api.request('/enquiries', null, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(mocks.getSession).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled()
  })
})

describe('request deadlines cover auth, fetch and response bodies', () => {
  it('bounds a read stalled on the auth client and does not fetch after a late session resolves', async () => {
    vi.useFakeTimers(); cloud()
    let resolveSession: (value: object) => void = () => undefined
    mocks.getSession.mockImplementation(() => new Promise(resolve => { resolveSession = resolve }))
    const api = await load(); const pending = api.request('/enquiries')
    const rejected = expect(pending).rejects.toMatchObject({ status: 504, code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(10000); await rejected
    resolveSession({ data: { session: { access_token: 'late-session' } } }); await Promise.resolve(); await Promise.resolve()
    expect(fetch).not.toHaveBeenCalled()
  })
  it('bounds a read even when response JSON never completes', async () => {
    vi.useFakeTimers()
    vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => undefined) } as Response)
    const api = await load(); const pending = api.request('/enquiries')
    const rejected = expect(pending).rejects.toMatchObject({ status: 504, code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(10000); await rejected
    expect(fetch).toHaveBeenCalledOnce()
  })
  it('gives a write thirty seconds without automatically resubmitting it', async () => {
    vi.useFakeTimers(); vi.mocked(fetch).mockImplementation(() => new Promise(() => undefined))
    const api = await load(); const pending = api.request('/enquiries', null, api.json('POST', { customerName: 'Synthetic customer' }, 'fixed-save-key'))
    const rejected = expect(pending).rejects.toMatchObject({ status: 504, code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(10000); expect(fetch).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(20000); await rejected
    expect(fetch).toHaveBeenCalledOnce()
    expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).get('Idempotency-Key')).toBe('fixed-save-key')
  })
  it('preserves caller cancellation instead of reporting a timeout or unreachable API', async () => {
    vi.mocked(fetch).mockImplementation(() => new Promise(() => undefined))
    const api = await load(); const controller = new AbortController(); const pending = api.request('/enquiries', null, { signal: controller.signal })
    const cancelled = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort(new DOMException('cancelled', 'AbortError')); await cancelled
  })
})
