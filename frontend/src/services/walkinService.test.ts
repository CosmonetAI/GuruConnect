import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RegisterWalkinInput } from '../types/walkin'

const mocks = vi.hoisted(() => ({ localMode: false, request: vi.fn() }))
vi.mock('./officeApi', () => ({
  get localMode() { return mocks.localMode }, request: mocks.request,
  json: (method: string, value?: unknown, key?: string) => ({ method, body: JSON.stringify(value), headers: key ? { 'Idempotency-Key': key } : undefined }),
}))
const load = () => import('./walkinService')
const registration: RegisterWalkinInput = { customerName: 'Synthetic visitor', mobileNumber: '+919876543210', email: '', companyName: '', purpose: 'Other', purposeDescription: '', productOrService: '', department: '', requestedEmployee: '', appointmentStatus: 'Walk-in', appointmentReference: '', internalNotes: '' }
const key = 'walkin-save-key-001'
beforeEach(() => { vi.resetModules(); vi.clearAllMocks(); mocks.localMode = false; localStorage.clear(); mocks.request.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 100 }) })
afterEach(() => { vi.restoreAllMocks() })

describe('hosted walk-in boundaries', () => {
  it('always uses the authenticated workspace API and keeps registration retries stable', async () => {
    const service = await load()
    const read = vi.spyOn(Storage.prototype, 'getItem'), write = vi.spyOn(Storage.prototype, 'setItem')
    await service.registerWalkin('workspace-one', registration, key)
    await service.registerWalkin('workspace-one', registration, key)
    expect(mocks.request).toHaveBeenCalledWith('/walkins', 'workspace-one', { method: 'POST', body: JSON.stringify(registration), headers: { 'Idempotency-Key': key } })
    expect(read).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled()
  })
  it('surfaces backend schema and authorization failures without local or demo fallback', async () => {
    localStorage.setItem('guru-group:walkin-visits', JSON.stringify([{ customerName: 'Stale browser private record' }]))
    mocks.request.mockRejectedValue(new Error('database_schema_missing'))
    const service = await load(), read = vi.spyOn(Storage.prototype, 'getItem')
    await expect(service.listWalkinVisits('workspace-one')).rejects.toThrow('database_schema_missing')
    await expect(service.listWalkinAgents('workspace-one')).rejects.toThrow('database_schema_missing')
    expect(read).not.toHaveBeenCalled()
    expect(service.isWalkinDemoMode()).toBe(false)
  })
  it('requires a workspace before every private read and never globally fetches customer data', async () => {
    const service = await load()
    await expect(service.listWalkinVisits('')).rejects.toThrow('Choose a workspace')
    await expect(service.getWalkinVisit('', 'visit')).rejects.toThrow('Choose a workspace')
    await expect(service.listWalkinHistory('')).rejects.toThrow('Choose a workspace')
    expect(mocks.request).not.toHaveBeenCalled()
  })
  it('escapes search values and forwards pagination/filter arguments without Supabase filter expressions', async () => {
    const service = await load()
    await service.listWalkinVisitPage('workspace-two', { query: '50%_, OR=true', assignedAgentId: 'agent-id', from: '2026-10-14' }, 3)
    const [path, workspace] = mocks.request.mock.calls[0]
    const params = new URL(path, 'https://example.test').searchParams
    expect(workspace).toBe('workspace-two')
    expect(params.get('query')).toBe('50%_, OR=true'); expect(params.get('fromDate')).toBe('2026-10-14')
    expect(params.get('page')).toBe('3'); expect(params.get('pageSize')).toBe('100')
  })
  it('passes optimistic concurrency and discards client requirement timestamps', async () => {
    const service = await load(), expectedUpdatedAt = '2026-10-14T10:00:00Z'
    const requirements = { ...service.emptyWalkinRequirement(), detailedRequirement: 'Synthetic requirement', updatedAt: 'forged-time' }
    await service.saveWalkinRequirements('workspace-one', 'visit-id', requirements, expectedUpdatedAt)
    const [path, workspace, init] = mocks.request.mock.calls[0]
    expect(path).toBe('/walkins/visit-id/requirements'); expect(workspace).toBe('workspace-one'); expect(init.method).toBe('PUT')
    const body = JSON.parse(init.body)
    expect(body.expectedUpdatedAt).toBe(expectedUpdatedAt); expect(body.requirement.updatedAt).toBeUndefined()
  })
  it('uses durable recording links and leaves hosted conversion to the backend transaction', async () => {
    const service = await load()
    expect(service.walkinRecordingsPath('visit-id')).toBe('/walkins/visit-id/recordings?page=1&pageSize=100')
    await service.attachWalkinRecording('workspace-one', 'visit-id', 'conversation-id')
    expect(mocks.request).toHaveBeenCalledWith('/walkins/visit-id/recordings', 'workspace-one', expect.objectContaining({ method: 'POST', body: '{"conversationId":"conversation-id"}' }))
    await expect(service.markWalkinConverted('workspace-one', 'visit-id', 'enquiry-id')).rejects.toThrow('atomically')
    expect(mocks.request).toHaveBeenCalledTimes(1)
  })
})

describe('isolated standalone storage', () => {
  beforeEach(() => { mocks.localMode = true })
  it('separates workspaces and preserves one registration for a repeated save', async () => {
    const service = await load()
    const a = await service.registerWalkin('workspace-one', registration, key)
    const replay = await service.registerWalkin('workspace-one', registration, key)
    const b = await service.registerWalkin('workspace-two', { ...registration, customerName: 'Other synthetic visitor' }, key)
    expect(a.id).toBe(replay.id); expect(b.id).not.toBe(a.id)
    expect((await service.listWalkinVisits('workspace-one')).map(visit => visit.customerName)).toEqual(['Synthetic visitor'])
    expect((await service.listWalkinVisits('workspace-two')).map(visit => visit.customerName)).toEqual(['Other synthetic visitor'])
    expect(mocks.request).not.toHaveBeenCalled()
  })
  it('limits legacy browser imports to the original local workspace', async () => {
    const service = await load()
    expect(service.walkinRecordingsPath('legacy-id')).toBe('/conversations?q=legacy-id&limit=100&audio_bucket=walkin-audio')
    const legacy = [{ ...registration, id: 'legacy-id', visitNumber: 'WI-legacy', checkInAt: '2026-10-14T10:00:00Z', status: 'Registered' }]
    localStorage.setItem('guru-group:walkin-visits', JSON.stringify(legacy))
    expect(await service.listWalkinVisits('different-workspace')).toEqual([])
    expect((await service.listWalkinVisits('00000000-0000-0000-0000-000000000002'))[0].id).toBe('legacy-id')
  })
  it('keeps terminal and converted statuses out of the regular transition menu', async () => {
    const service = await load()
    expect(service.getAllowedWalkinTransitions('Requirements Captured')).not.toContain('Converted to Enquiry')
    expect(service.getAllowedWalkinTransitions('Waiting for Assignment')).not.toContain('Assigned')
    expect(service.getAllowedWalkinTransitions('Completed')).toEqual([])
  })
})
