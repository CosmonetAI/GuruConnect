import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../services/officeApi'
import { useOfficeResource } from './OfficeUi'

const calls = vi.hoisted(() => ({ request: vi.fn(), workspace: 'workspace-a', user: 'user-a' }))
vi.mock('../services/officeApi', async original => ({ ...await original<typeof import('../services/officeApi')>(), request: calls.request }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: calls.user }, workspace: { id: calls.workspace, role: 'owner' } }) }))

beforeEach(() => {
  vi.useFakeTimers()
  calls.request.mockReset()
  calls.workspace = 'workspace-a'; calls.user = 'user-a'
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('private processing resource lifecycle', () => {
  it('coalesces refreshes during an existing read into one subsequent current-status request', async () => {
    let finish: (value: unknown) => void = () => {}
    calls.request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve })).mockResolvedValueOnce({ text: 'Latest committed result' })
    const { result } = renderHook(() => useOfficeResource<{ text: string }>('/private-result'))
    act(() => { result.current.reload(); result.current.reload(); result.current.reload() })
    expect(calls.request).toHaveBeenCalledTimes(1)
    await act(async () => finish({ text: 'Earlier snapshot' }))
    expect(calls.request).toHaveBeenCalledTimes(2)
    expect(result.current.data?.text).toBe('Latest committed result')
  })

  it('retains confirmed data while a refresh is pending or temporarily fails', async () => {
    calls.request.mockResolvedValueOnce({ text: 'Confirmed customer result' })
    const { result } = renderHook(() => useOfficeResource<{ text: string }>('/private-result'))
    await act(async () => {})
    expect(result.current.data?.text).toBe('Confirmed customer result')
    let reject: (error: Error) => void = () => {}
    calls.request.mockImplementationOnce(() => new Promise((_resolve, failure) => { reject = failure }))
    act(() => result.current.reload())
    expect(result.current.data?.text).toBe('Confirmed customer result')
    expect(result.current.refreshing).toBe(true)
    await act(async () => reject(new Error('Temporarily unavailable')))
    expect(result.current.data?.text).toBe('Confirmed customer result')
    expect(result.current.error).toBe('Temporarily unavailable')
  })

  it('aborts an old request and hides all old data before another workspace resolves', async () => {
    calls.request.mockResolvedValueOnce({ text: 'Workspace A private result' })
    let finish: (value: unknown) => void = () => {}
    calls.request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    calls.request.mockImplementationOnce(() => new Promise(() => {}))
    const { result, rerender } = renderHook(() => useOfficeResource<{ text: string }>('/private-result'))
    await act(async () => {})
    act(() => result.current.reload())
    const signal = calls.request.mock.calls[1][2].signal as AbortSignal
    calls.workspace = 'workspace-b'; rerender()
    expect(signal.aborted).toBe(true)
    expect(result.current.data).toBeUndefined()
    await act(async () => finish({ text: 'Late workspace A result' }))
    expect(result.current.data).toBeUndefined()
  })

  it('clears cached private data when access is revoked and stops automatic requests', async () => {
    calls.request.mockResolvedValueOnce({ text: 'Private result' }).mockRejectedValueOnce(new ApiError(403, 'access_denied', 'Access revoked'))
    const { result } = renderHook(() => useOfficeResource<{ text: string }>('/private-result', () => true))
    await act(async () => {})
    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(result.current.data).toBeUndefined()
    expect(result.current.error).toBe('Access revoked')
    await act(async () => { await vi.advanceTimersByTimeAsync(16000) })
    expect(calls.request).toHaveBeenCalledTimes(2)
  })

  it('pauses checks in a hidden or offline tab and resumes when visible and online', async () => {
    calls.request.mockResolvedValue({ pending: true })
    renderHook(() => useOfficeResource('/private-result', () => true))
    await act(async () => {})
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    await act(async () => { await vi.advanceTimersByTimeAsync(8000) })
    expect(calls.request).toHaveBeenCalledTimes(1)
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(calls.request).toHaveBeenCalledTimes(1)
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
    await act(async () => window.dispatchEvent(new Event('online')))
    expect(calls.request).toHaveBeenCalledTimes(2)
  })

  it('bounds repeated checks and resumes only after explicit refresh', async () => {
    calls.request.mockResolvedValue({ pending: true })
    const { result } = renderHook(() => useOfficeResource('/private-result', () => true))
    await act(async () => {})
    await act(async () => { await vi.advanceTimersByTimeAsync(4000 * 181) })
    expect(calls.request).toHaveBeenCalledTimes(181)
    expect(result.current.pollPaused).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
    expect(calls.request).toHaveBeenCalledTimes(181)
    await act(async () => result.current.reload())
    expect(calls.request).toHaveBeenCalledTimes(182)
    expect(result.current.pollPaused).toBe(false)
  })
})
