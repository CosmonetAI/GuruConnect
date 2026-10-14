import { json, localMode, request } from './officeApi'
import type { RegisterWalkinInput, WalkinAgent, WalkinFilters, WalkinHistoryEvent, WalkinRequirement, WalkinStatus, WalkinVisit } from '../types/walkin'

export interface WalkinPage<T> { items: T[]; total: number; page: number; pageSize: number }
export interface WalkinVisitPage extends WalkinPage<WalkinVisit> { summary: { waiting: number; assigned: number; discussion: number; requirements: number; converted: number } }
const demoAgentId = 'demo-agent-sneha'
const demoAgents: WalkinAgent[] = [
  { id: demoAgentId, name: 'Sneha Rao', department: 'Sales', specialization: 'Product solutions', availability: 'Available', openVisits: 0 },
  { id: 'demo-agent-rajesh', name: 'Rajesh Kumar', department: 'Projects', specialization: 'Project sales', availability: 'Available', openVisits: 0 },
  { id: 'demo-agent-ananya', name: 'Ananya Shah', department: 'Support', specialization: 'Service and support', availability: 'Busy', openVisits: 0 },
]
const closedStatuses = ['Completed', 'Cancelled', 'Converted to Enquiry']
export function isWalkinDemoMode() { return localMode }
function scope(workspaceId: string) {
  if (!workspaceId) throw new Error('Choose a workspace before loading walk-in visits.')
  return workspaceId
}
function readLocal<T>(workspaceId: string, section: string, fallback: T): T {
  if (!localMode) throw new Error('Browser walk-in storage is only available in local mode.')
  const key = `guru-group:walkin:${scope(workspaceId)}:${section}`
  let value = localStorage.getItem(key)
  // Preserve pre-workspace standalone data only in its original local workspace.
  if (!value && workspaceId === '00000000-0000-0000-0000-000000000002' && ['visits', 'history', 'requirements'].includes(section)) {
    value = localStorage.getItem(`guru-group:walkin-${section}`)
    if (value) localStorage.setItem(key, value)
  }
  if (!value) return fallback
  try { return JSON.parse(value) as T } catch { throw new Error('Saved local walk-in data is invalid. Restore a valid local backup.') }
}
function saveLocal<T>(workspaceId: string, section: string, value: T) {
  if (!localMode) throw new Error('Browser walk-in storage is only available in local mode.')
  localStorage.setItem(`guru-group:walkin:${scope(workspaceId)}:${section}`, JSON.stringify(value))
}
function addLocalHistory(workspaceId: string, visit: WalkinVisit, eventType: string, oldValue: string | null = null, newValue: string | null = null, notes = '') {
  const events = readLocal<WalkinHistoryEvent[]>(workspaceId, 'history', [])
  events.push({ id: crypto.randomUUID(), visitId: visit.id, eventType, oldValue, newValue, notes, performedBy: 'Local team', createdAt: new Date().toISOString() })
  saveLocal(workspaceId, 'history', events)
}
function filtersPath(filters: WalkinFilters, page = 1) {
  const params = new URLSearchParams({ page: String(page), pageSize: '100' })
  for (const key of ['query', 'purpose', 'status', 'assignedAgentId'] as const) if (filters[key]) params.set(key, filters[key]!)
  if (filters.from) params.set('fromDate', filters.from)
  if (filters.to) params.set('toDate', filters.to)
  return `/walkins?${params}`
}
function filterVisits(visits: WalkinVisit[], filters: WalkinFilters) {
  const query = filters.query?.trim().toLocaleLowerCase()
  return visits.filter(visit => (!filters.assignedAgentId || visit.assignedAgentId === filters.assignedAgentId)
    && (!filters.purpose || visit.purpose === filters.purpose) && (!filters.status || visit.status === filters.status)
    && (!filters.from || visit.checkInAt.slice(0, 10) >= filters.from) && (!filters.to || visit.checkInAt.slice(0, 10) <= filters.to)
    && (!query || [visit.customerName, visit.mobileNumber, visit.visitNumber, visit.purpose].some(value => value.toLocaleLowerCase().includes(query))))
}
export async function listWalkinVisitPage(workspaceId: string, filters: WalkinFilters = {}, page = 1): Promise<WalkinVisitPage> {
  scope(workspaceId)
  if (!localMode) return request(filtersPath(filters, page), workspaceId)
  const visits = filterVisits(readLocal<WalkinVisit[]>(workspaceId, 'visits', []), filters).sort((a, b) => b.checkInAt.localeCompare(a.checkInAt))
  return { items: visits.slice((page - 1) * 100, page * 100), total: visits.length, page, pageSize: 100, summary: {
    waiting: visits.filter(visit => ['Registered', 'Waiting for Assignment'].includes(visit.status)).length,
    assigned: visits.filter(visit => ['Assigned', 'Waiting for Sales Agent'].includes(visit.status)).length,
    discussion: visits.filter(visit => visit.status === 'In Discussion').length,
    requirements: visits.filter(visit => visit.status === 'Requirements Captured').length,
    converted: visits.filter(visit => !!visit.convertedEnquiryReference).length,
  } }
}
export async function listWalkinVisits(workspaceId: string, filters: WalkinFilters = {}): Promise<WalkinVisit[]> {
  return (await listWalkinVisitPage(workspaceId, filters)).items
}
export async function listWalkinAgents(workspaceId: string): Promise<WalkinAgent[]> {
  scope(workspaceId)
  if (!localMode) return (await request<{ items: WalkinAgent[] }>('/walkins/agents', workspaceId)).items
  const visits = readLocal<WalkinVisit[]>(workspaceId, 'visits', [])
  const availability = readLocal<Record<string, WalkinAgent['availability']>>(workspaceId, 'availability', {})
  return demoAgents.map(agent => ({ ...agent, availability: availability[agent.id] ?? agent.availability, openVisits: visits.filter(visit => visit.assignedAgentId === agent.id && !closedStatuses.includes(visit.status)).length }))
}
export async function getCurrentWalkinAgentId(workspaceId: string): Promise<string> {
  scope(workspaceId)
  if (localMode) return readLocal<string>(workspaceId, 'demo-agent', demoAgentId)
  return (await request<{ currentAgentId: string }>('/walkins/agents', workspaceId)).currentAgentId
}
export async function canManageWalkinAgents(workspaceId: string): Promise<boolean> {
  scope(workspaceId)
  if (localMode) return true
  return (await request<{ canManage: boolean }>('/walkins/agents', workspaceId)).canManage
}
export function setCurrentDemoWalkinAgentId(workspaceId: string, agentId: string) {
  if (!localMode || !demoAgents.some(agent => agent.id === agentId)) throw new Error('Choose a configured local demo agent.')
  saveLocal(workspaceId, 'demo-agent', agentId)
}
export async function getWalkinVisit(workspaceId: string, id: string): Promise<WalkinVisit | null> {
  scope(workspaceId)
  if (localMode) return readLocal<WalkinVisit[]>(workspaceId, 'visits', []).find(visit => visit.id === id) ?? null
  return request(`/walkins/${encodeURIComponent(id)}`, workspaceId)
}
export async function registerWalkin(workspaceId: string, input: RegisterWalkinInput, idempotencyKey: string): Promise<WalkinVisit> {
  scope(workspaceId)
  if (!localMode) return request('/walkins', workspaceId, json('POST', input, idempotencyKey))
  const visits = readLocal<WalkinVisit[]>(workspaceId, 'visits', [])
  const keys = readLocal<Record<string, { id: string; payload: string }>>(workspaceId, 'requests', {})
  const prior = keys[idempotencyKey]
  if (prior) {
    if (prior.payload !== JSON.stringify(input)) throw new Error('This save key was already used for different visitor details. Reload before registering again.')
    const saved = visits.find(visit => visit.id === prior.id)
    if (!saved) throw new Error('The previously registered visit could not be found.')
    return saved
  }
  const now = new Date().toISOString(), id = crypto.randomUUID()
  const visit: WalkinVisit = { ...input, id, visitNumber: `WI-${now.slice(0, 10).replaceAll('-', '')}-${id.slice(0, 6).toUpperCase()}`, registeredBy: 'Local reception', assignedAgentId: null, assignedAgentName: null, assignmentNote: '', status: input.appointmentStatus === 'Walk-in' ? 'Waiting for Assignment' : 'Registered', checkInAt: now, assignedAt: null, convertedEnquiryReference: null, createdAt: now, updatedAt: now }
  visits.push(visit)
  saveLocal(workspaceId, 'visits', visits)
  keys[idempotencyKey] = { id, payload: JSON.stringify(input) }
  saveLocal(workspaceId, 'requests', keys)
  addLocalHistory(workspaceId, visit, 'Registered', null, visit.status)
  return visit
}
function localEditable(workspaceId: string, id: string, expectedUpdatedAt: string) {
  const visits = readLocal<WalkinVisit[]>(workspaceId, 'visits', [])
  const visit = visits.find(item => item.id === id)
  if (!visit) throw new Error('Walk-in visit was not found.')
  if (visit.updatedAt !== expectedUpdatedAt) throw new Error('This visit changed while you were editing. Refresh before saving again.')
  return { visits, visit }
}
export async function assignWalkin(workspaceId: string, id: string, agentId: string | null, note: string, expectedUpdatedAt: string) {
  scope(workspaceId)
  if (!localMode) return request<WalkinVisit>(`/walkins/${encodeURIComponent(id)}/assignment`, workspaceId, json('PATCH', { agentId, note, expectedUpdatedAt }))
  const { visits, visit } = localEditable(workspaceId, id, expectedUpdatedAt)
  if (closedStatuses.includes(visit.status)) throw new Error('A closed visit cannot be reassigned.')
  const agent = demoAgents.find(item => item.id === agentId)
  if (agentId && !agent) throw new Error('Choose a configured local demo agent.')
  const previous = visit.assignedAgentName
  if (visit.assignedAgentId !== agentId) {
    visit.assignedAt = agentId ? new Date().toISOString() : null
    visit.status = agentId ? 'Assigned' : 'Waiting for Assignment'
  }
  visit.assignedAgentId = agentId; visit.assignedAgentName = agent?.name ?? null; visit.assignmentNote = note; visit.updatedAt = new Date().toISOString()
  saveLocal(workspaceId, 'visits', visits)
  addLocalHistory(workspaceId, visit, 'Assignment changed', previous, visit.assignedAgentName, note)
  return visit
}
const statusTransitions: Record<WalkinStatus, WalkinStatus[]> = {
  Registered: ['Waiting for Assignment', 'Cancelled'], 'Waiting for Assignment': ['Cancelled'],
  Assigned: ['Waiting for Sales Agent', 'In Discussion', 'Cancelled'], 'Waiting for Sales Agent': ['In Discussion', 'Cancelled'],
  'In Discussion': ['Requirements Captured', 'Follow-up Required', 'Completed', 'Cancelled'],
  'Requirements Captured': ['Follow-up Required', 'Completed'],
  'Follow-up Required': ['In Discussion', 'Requirements Captured', 'Completed', 'Cancelled'],
  'Converted to Enquiry': ['Completed'], Completed: [], Cancelled: [],
}
export function getAllowedWalkinTransitions(status: WalkinStatus) { return statusTransitions[status] ?? [] }
export async function updateWalkinStatus(workspaceId: string, id: string, status: WalkinStatus, expectedUpdatedAt: string) {
  scope(workspaceId)
  if (!localMode) return request<WalkinVisit>(`/walkins/${encodeURIComponent(id)}/status`, workspaceId, json('PATCH', { status, expectedUpdatedAt }))
  const { visits, visit } = localEditable(workspaceId, id, expectedUpdatedAt)
  if (!getAllowedWalkinTransitions(visit.status).includes(status)) throw new Error(`Cannot change visit status from ${visit.status} to ${status}.`)
  const requirement = await getWalkinRequirements(workspaceId, id)
  if (status === 'Requirements Captured' && !requirement.updatedAt) throw new Error('Save reviewed requirements before advancing the visit.')
  const previous = visit.status; visit.status = status; visit.updatedAt = new Date().toISOString()
  saveLocal(workspaceId, 'visits', visits)
  addLocalHistory(workspaceId, visit, 'Status changed', previous, status)
  return visit
}
export async function getWalkinRequirements(workspaceId: string, id: string): Promise<WalkinRequirement> {
  scope(workspaceId)
  if (!localMode) return request(`/walkins/${encodeURIComponent(id)}/requirements`, workspaceId)
  return readLocal<Record<string, WalkinRequirement>>(workspaceId, 'requirements', {})[id] ?? emptyWalkinRequirement()
}
export async function saveWalkinRequirements(workspaceId: string, id: string, requirement: WalkinRequirement, expectedUpdatedAt: string): Promise<WalkinRequirement> {
  scope(workspaceId)
  const { updatedAt: _updatedAt, ...input } = requirement
  if (!localMode) return request(`/walkins/${encodeURIComponent(id)}/requirements`, workspaceId, json('PUT', { requirement: input, expectedUpdatedAt }))
  const { visits, visit } = localEditable(workspaceId, id, expectedUpdatedAt)
  if (closedStatuses.includes(visit.status)) throw new Error('A closed visit cannot be changed.')
  if (!visit.assignedAgentId) throw new Error('Assign the visit before saving requirements.')
  if (!input.detailedRequirement.trim() && !input.productOrService.trim()) throw new Error('Add a customer requirement or product before saving.')
  const all = readLocal<Record<string, WalkinRequirement>>(workspaceId, 'requirements', {})
  const saved = { ...input, updatedAt: new Date().toISOString() }
  all[id] = saved; saveLocal(workspaceId, 'requirements', all)
  if (getAllowedWalkinTransitions(visit.status).includes('Requirements Captured')) visit.status = 'Requirements Captured'
  visit.updatedAt = saved.updatedAt; saveLocal(workspaceId, 'visits', visits)
  addLocalHistory(workspaceId, visit, 'Requirements updated')
  return saved
}
export async function listWalkinHistoryPage(workspaceId: string, visitId?: string, page = 1): Promise<WalkinPage<WalkinHistoryEvent>> {
  scope(workspaceId)
  if (!localMode) {
    const params = new URLSearchParams({ page: String(page), pageSize: '100' })
    if (visitId) params.set('visitId', visitId)
    return request(`/walkins/history?${params}`, workspaceId)
  }
  const events = readLocal<WalkinHistoryEvent[]>(workspaceId, 'history', []).filter(event => !visitId || event.visitId === visitId).sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  return { items: events.slice((page - 1) * 100, page * 100), total: events.length, page, pageSize: 100 }
}
export async function listWalkinHistory(workspaceId: string, visitId?: string): Promise<WalkinHistoryEvent[]> { return (await listWalkinHistoryPage(workspaceId, visitId)).items }
export function walkinRecordingsPath(visitId: string, page = 1) {
  return localMode ? `/conversations?q=${encodeURIComponent(visitId)}&limit=100` : `/walkins/${encodeURIComponent(visitId)}/recordings?page=${page}&pageSize=100`
}
export async function attachWalkinRecording(workspaceId: string, visitId: string, conversationId: string) {
  scope(workspaceId)
  if (localMode) return
  return request(`/walkins/${encodeURIComponent(visitId)}/recordings`, workspaceId, json('POST', { conversationId }))
}
export async function markWalkinConverted(workspaceId: string, id: string, enquiryReference: string) {
  if (!localMode) throw new Error('Hosted visits are converted atomically when the sales enquiry is saved.')
  const visits = readLocal<WalkinVisit[]>(workspaceId, 'visits', []), visit = visits.find(item => item.id === id)
  if (!visit) throw new Error('Walk-in visit was not found.')
  if (visit.convertedEnquiryReference) return visit
  if (!['Requirements Captured', 'Follow-up Required'].includes(visit.status)) throw new Error('Capture and review requirements before conversion.')
  visit.convertedEnquiryReference = enquiryReference; visit.status = 'Converted to Enquiry'; visit.updatedAt = new Date().toISOString()
  saveLocal(workspaceId, 'visits', visits); addLocalHistory(workspaceId, visit, 'Converted to enquiry', null, enquiryReference)
  return visit
}
export async function updateWalkinAgentAvailability(workspaceId: string, agentId: string, availability: WalkinAgent['availability']) {
  scope(workspaceId)
  if (!localMode) return request(`/walkins/agents/${encodeURIComponent(agentId)}`, workspaceId, json('PATCH', { availability }))
  if (!demoAgents.some(agent => agent.id === agentId)) throw new Error('Choose a configured local demo agent.')
  const all = readLocal<Record<string, WalkinAgent['availability']>>(workspaceId, 'availability', {})
  all[agentId] = availability; saveLocal(workspaceId, 'availability', all)
}
export function emptyWalkinRequirement(): WalkinRequirement {
  return { detailedRequirement: '', productOrService: '', quantity: '', budgetRange: '', expectedPurchaseTimeline: '', preferredSolution: '', competitorOrAlternative: '', specialRequests: '', followUpAt: '', agentNotes: '', nextAction: '', updatedAt: null }
}
