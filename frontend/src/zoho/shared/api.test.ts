import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('../../services/officeApi', () => ({ supabase: null }))
import { call } from './api'

describe('sales API request deadlines', () => {
  beforeEach(() => { localStorage.clear(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })) })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
  it('allows a bounded longer document request without passing timeoutMs to fetch', async () => {
    const deadline = vi.spyOn(AbortSignal, 'timeout')
    await call('/voice-sessions/session/sales-documents', { method: 'POST', timeoutMs: 90000 })
    expect(deadline).toHaveBeenCalledWith(90000)
    expect(fetch).toHaveBeenCalledWith(expect.any(String), expect.not.objectContaining({ timeoutMs: expect.anything() }))
  })
  it('keeps normal calls at 30 seconds and caps explicit requests at 120 seconds', async () => {
    const deadline = vi.spyOn(AbortSignal, 'timeout')
    await call('/chats')
    expect(deadline).toHaveBeenLastCalledWith(30000)
    await call('/voice-sessions/session/sales-documents', { timeoutMs: 500000 })
    expect(deadline).toHaveBeenLastCalledWith(120000)
  })
  it('preserves structured workflow status so a lost creation can be retried safely', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({ detail: { code: 'document_creation_uncertain', message: 'Review the created document.', document_started: true } }) } as Response)
    await expect(call('/voice-sessions/session/sales-documents')).rejects.toMatchObject({ status: 502, code: 'document_creation_uncertain', message: 'Review the created document.', details: { document_started: true } })
  })
})
