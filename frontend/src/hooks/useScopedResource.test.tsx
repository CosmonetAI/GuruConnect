import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ request: vi.fn(), office: { user: { id: 'alice' } as { id: string } | null, workspace: { id: 'workspace-a' } as { id: string; member_id?: string } | null } }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => mocks.office }))
vi.mock('../services/officeApi', () => ({ request: mocks.request, message: (error: unknown) => error instanceof Error ? error.message : 'Request failed', ApiError: class extends Error { constructor(public status: number, public code: string, message: string) { super(message) } } }))
import { ApiError } from '../services/officeApi'
import { useScopedResource } from './useScopedResource'

beforeEach(() => { vi.clearAllMocks(); mocks.office = { user: { id: 'alice' }, workspace: { id: 'workspace-a' } }; mocks.request.mockResolvedValue({ customer: 'Private Alice customer' }) })
describe('private scoped data reads', () => {
  it('does not request data without a user, workspace and resource path', () => {
    mocks.office.user = null
    const hook = renderHook(() => useScopedResource('/enquiries'))
    expect(hook.result.current.data).toBeUndefined(); expect(hook.result.current.loading).toBe(false)
    expect(mocks.request).not.toHaveBeenCalled()
  })
  it('aborts the old account/workspace read and ignores an out-of-order private result', async () => {
    let oldResolve: (value: object) => void = () => undefined
    mocks.request.mockImplementationOnce(() => new Promise(resolve => { oldResolve = resolve })).mockResolvedValueOnce({ customer: 'Private Bob customer' })
    const hook = renderHook(() => useScopedResource<{ customer: string }>('/enquiries'))
    const oldSignal = mocks.request.mock.calls[0][2].signal as AbortSignal
    mocks.office = { user: { id: 'bob' }, workspace: { id: 'workspace-b' } }; hook.rerender()
    expect(oldSignal.aborted).toBe(true)
    expect(hook.result.current.data).toBeUndefined()
    await waitFor(() => expect(hook.result.current.data?.customer).toBe('Private Bob customer'))
    await act(async () => { oldResolve({ customer: 'Private Alice customer' }) })
    expect(hook.result.current.data?.customer).toBe('Private Bob customer')
  })
  it('clears displayed private records immediately on sign-out', async () => {
    const hook = renderHook(() => useScopedResource<{ customer: string }>('/enquiries'))
    await waitFor(() => expect(hook.result.current.data?.customer).toBe('Private Alice customer'))
    mocks.office = { user: null, workspace: null }; hook.rerender()
    expect(hook.result.current.data).toBeUndefined(); expect(hook.result.current.error).toBe(''); expect(hook.result.current.loading).toBe(false)
  })
  it.each([401, 403, 404])('clears previously displayed private data when access is lost (%i)', async status => {
    const hook = renderHook(() => useScopedResource<{ customer: string }>('/enquiries'))
    await waitFor(() => expect(hook.result.current.data?.customer).toBe('Private Alice customer'))
    mocks.request.mockRejectedValueOnce(new ApiError(status, 'access_denied', 'Access unavailable'))
    await act(async () => { await hook.result.current.reload() })
    expect(hook.result.current.data).toBeUndefined(); expect(hook.result.current.error).toBe('Access unavailable')
  })
  it('retains same-scope data during temporary refresh failure and coalesces simultaneous reloads', async () => {
    const hook = renderHook(() => useScopedResource<{ customer: string }>('/enquiries'))
    await waitFor(() => expect(hook.result.current.data?.customer).toBe('Private Alice customer'))
    let rejectRefresh: (error: Error) => void = () => undefined
    mocks.request.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRefresh = reject }))
    let refresh: Promise<void> = Promise.resolve()
    act(() => { refresh = hook.result.current.reload(); void hook.result.current.reload(); void hook.result.current.reload() })
    expect(mocks.request).toHaveBeenCalledTimes(2)
    expect(hook.result.current.data?.customer).toBe('Private Alice customer')
    await act(async () => { rejectRefresh(new ApiError(503, 'database_busy', 'Please retry')); await refresh })
    expect(hook.result.current.data?.customer).toBe('Private Alice customer'); expect(hook.result.current.error).toBe('Please retry')
  })
  it('removes a resource and cancels reads when its path changes or the component unmounts', async () => {
    const hook = renderHook(({ path }) => useScopedResource<{ customer: string }>(path), { initialProps: { path: '/enquiries' as string | null } })
    await waitFor(() => expect(hook.result.current.data?.customer).toBe('Private Alice customer'))
    const signal = mocks.request.mock.calls[0][2].signal as AbortSignal
    hook.rerender({ path: null })
    expect(signal.aborted).toBe(true); expect(hook.result.current.data).toBeUndefined()
    hook.rerender({ path: '/another-private-resource' })
    const latest = mocks.request.mock.calls.at(-1)?.[2].signal as AbortSignal
    hook.unmount(); expect(latest.aborted).toBe(true)
  })
})


describe('member view boundaries within one workspace', () => {
  it('clears and cancels an owner view when the selected member changes', async () => {
    mocks.office.workspace = { id: 'workspace-a', member_id: 'first-member' }
    let finish!: (value: object) => void
    mocks.request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve })).mockResolvedValueOnce({ customer: 'Second member customer' })
    const hook = renderHook(() => useScopedResource<{ customer: string }>('/conversations'))
    const signal = mocks.request.mock.calls[0][2].signal as AbortSignal
    mocks.office.workspace = { id: 'workspace-a', member_id: 'second-member' }; hook.rerender()
    expect(signal.aborted).toBe(true); expect(hook.result.current.data).toBeUndefined()
    await waitFor(() => expect(hook.result.current.data?.customer).toBe('Second member customer'))
    await act(async () => finish({ customer: 'First member private customer' }))
    expect(hook.result.current.data?.customer).toBe('Second member customer')
    expect(mocks.request.mock.calls.at(-1)?.[3]).toBe('second-member')
  })
})
