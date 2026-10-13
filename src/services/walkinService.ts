import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import type {
  RegisterWalkinInput,
  WalkinAgent,
  WalkinFilters,
  WalkinHistoryEvent,
  WalkinRequirement,
  WalkinRecording,
  WalkinStatus,
  WalkinVisit,
} from '../types/walkin'

const storageKey = 'guru-group:walkin-visits'
const historyStorageKey = 'guru-group:walkin-history'
const recordingStorageKey = 'guru-group:walkin-recordings'
const demoAgentId = 'demo-agent-sneha'
const isDemo = import.meta.env.VITE_DEMO
  ? import.meta.env.VITE_DEMO === 'true'
  : import.meta.env.DEV
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

let supabase: SupabaseClient | null = null

function getSupabase(): SupabaseClient {
  if (!supabaseUrl || !supabaseAnonKey) {
    throw new Error('Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.')
  }
  if (!supabase) supabase = createClient(supabaseUrl, supabaseAnonKey)
  return supabase
}

export function isWalkinDemoMode() {
  return isDemo
}

const demoAgents: WalkinAgent[] = [
  { id: 'demo-agent-sneha', name: 'Sneha Rao', department: 'Sales', specialization: 'Product solutions', availability: 'Available', openVisits: 0 },
  { id: 'demo-agent-rajesh', name: 'Rajesh Kumar', department: 'Projects', specialization: 'Project sales', availability: 'Available', openVisits: 0 },
  { id: 'demo-agent-ananya', name: 'Ananya Shah', department: 'Support', specialization: 'Service and support', availability: 'Busy', openVisits: 0 },
]

function readLocal<T>(key: string, fallback: T): T {
  const value = localStorage.getItem(key)
  if (!value) return fallback
  try {
    return JSON.parse(value) as T
  } catch (error) {
    throw new Error(`Saved walk-in data is invalid (${error instanceof Error ? error.message : 'invalid JSON'}).`)
  }
}

function saveLocal<T>(key: string, value: T) {
  localStorage.setItem(key, JSON.stringify(value))
}

function toVisit(row: Record<string, unknown>): WalkinVisit {
  return {
    id: String(row.id),
    visitNumber: String(row.visit_number),
    customerName: String(row.customer_name),
    mobileNumber: String(row.mobile_number),
    email: String(row.email ?? ''),
    companyName: String(row.company_name ?? ''),
    purpose: row.purpose as WalkinVisit['purpose'],
    purposeDescription: String(row.purpose_description ?? ''),
    productOrService: String(row.product_or_service ?? ''),
    department: String(row.department ?? ''),
    requestedEmployee: String(row.requested_employee ?? ''),
    appointmentStatus: row.appointment_status as WalkinVisit['appointmentStatus'],
    appointmentReference: String(row.appointment_reference ?? ''),
    registeredBy: String(row.registered_by ?? ''),
    assignedAgentId: row.assigned_agent_id ? String(row.assigned_agent_id) : null,
    assignedAgentName: row.assigned_agent_name ? String(row.assigned_agent_name) : null,
    assignmentNote: String(row.assignment_note ?? ''),
    status: row.status as WalkinStatus,
    checkInAt: String(row.check_in_at),
    assignedAt: row.assigned_at ? String(row.assigned_at) : null,
    internalNotes: String(row.internal_notes ?? ''),
    convertedEnquiryReference: row.converted_enquiry_reference ? String(row.converted_enquiry_reference) : null,
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
  }
}

function toRequirement(row?: Record<string, unknown> | null): WalkinRequirement {
  return {
    detailedRequirement: String(row?.detailed_requirement ?? ''),
    productOrService: String(row?.product_or_service ?? ''),
    quantity: String(row?.quantity ?? ''),
    budgetRange: String(row?.budget_range ?? ''),
    expectedPurchaseTimeline: String(row?.expected_purchase_timeline ?? ''),
    preferredSolution: String(row?.preferred_solution ?? ''),
    competitorOrAlternative: String(row?.competitor_or_alternative ?? ''),
    specialRequests: String(row?.special_requests ?? ''),
    followUpAt: row?.follow_up_at ? String(row.follow_up_at).slice(0, 10) : '',
    agentNotes: String(row?.agent_notes ?? ''),
    nextAction: String(row?.next_action ?? ''),
    updatedAt: row?.updated_at ? String(row.updated_at) : null,
  }
}

function toRecording(row: Record<string, unknown>): WalkinRecording {
  return {
    id: String(row.id),
    visitId: String(row.walkin_visit_id),
    transcript: String(row.transcript),
    startedAt: String(row.started_at),
    endedAt: String(row.ended_at),
    durationSeconds: Number(row.duration_seconds),
    status: 'saved',
    audioStoragePath: row.audio_storage_path ? String(row.audio_storage_path) : null,
    consentAt: String(row.consent_at),
    createdBy: String(row.created_by ?? ''),
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
  }
}

function mapRequirement(requirement: WalkinRequirement, visitId: string) {
  return {
    walkin_visit_id: visitId,
    detailed_requirement: requirement.detailedRequirement,
    product_or_service: requirement.productOrService,
    quantity: requirement.quantity || null,
    budget_range: requirement.budgetRange || null,
    expected_purchase_timeline: requirement.expectedPurchaseTimeline || null,
    preferred_solution: requirement.preferredSolution || null,
    competitor_or_alternative: requirement.competitorOrAlternative || null,
    special_requests: requirement.specialRequests || null,
    follow_up_at: requirement.followUpAt || null,
    agent_notes: requirement.agentNotes || null,
    next_action: requirement.nextAction || null,
  }
}

function addLocalHistory(
  visit: WalkinVisit,
  eventType: string,
  oldValue: string | null,
  newValue: string | null,
  notes: string,
) {
  const events = readLocal<WalkinHistoryEvent[]>(historyStorageKey, [])
  events.push({
    id: crypto.randomUUID(),
    visitId: visit.id,
    eventType,
    oldValue,
    newValue,
    notes,
    performedBy: 'Reception / Sales team',
    createdAt: new Date().toISOString(),
  })
  saveLocal(historyStorageKey, events)
}

function filterVisits(visits: WalkinVisit[], filters: WalkinFilters) {
  const query = filters.query?.trim().toLocaleLowerCase()
  return visits.filter((visit) => {
    if (filters.assignedAgentId && visit.assignedAgentId !== filters.assignedAgentId) return false
    if (filters.purpose && visit.purpose !== filters.purpose) return false
    if (filters.status && visit.status !== filters.status) return false
    if (filters.from && visit.checkInAt.slice(0, 10) < filters.from) return false
    if (filters.to && visit.checkInAt.slice(0, 10) > filters.to) return false
    return !query || [
      visit.customerName,
      visit.mobileNumber,
      visit.visitNumber,
      visit.purpose,
    ].some((value) => value.toLocaleLowerCase().includes(query))
  })
}

export async function listWalkinAgents(): Promise<WalkinAgent[]> {
  if (isDemo) {
    const visits = readLocal<WalkinVisit[]>(storageKey, [])
    return demoAgents.map((agent) => ({
      ...agent,
      openVisits: visits.filter((visit) =>
        visit.assignedAgentId === agent.id && !['Completed', 'Cancelled', 'Converted to Enquiry'].includes(visit.status),
      ).length,
    }))
  }
  const { data, error } = await getSupabase()
    .from('walkin_sales_agents')
    .select('user_id, display_name, department, specialization, availability, active')
    .eq('active', true)
    .order('display_name')
  if (error) throw new Error(`Sales agents could not be loaded: ${error.message}`)
  const agents = (data ?? []) as Array<Record<string, unknown>>
  const client = getSupabase()
  const { data: userData, error: userError } = await client.auth.getUser()
  if (userError) throw new Error(`Your signed-in user could not be loaded: ${userError.message}`)
  if (!userData.user) throw new Error('Sign in before loading sales agents.')
  const role = String(userData.user.app_metadata.role ?? '')
  if (!['reception', 'sales_agent', 'manager', 'admin'].includes(role)) {
    throw new Error('Your account is missing an authorized walk-in role. Ask an administrator to configure it.')
  }
  const canSeeAllCounts = ['reception', 'manager', 'admin'].includes(role)
  return Promise.all(agents.map(async (agent) => {
    if (!canSeeAllCounts && String(agent.user_id) !== userData.user.id) {
      return {
        id: String(agent.user_id),
        name: String(agent.display_name),
        department: String(agent.department ?? ''),
        specialization: String(agent.specialization ?? ''),
        availability: agent.availability as WalkinAgent['availability'],
        openVisits: null,
      }
    }
    const { count, error: countError } = await client
      .from('walkin_visits')
      .select('id', { count: 'exact', head: true })
      .eq('assigned_agent_id', String(agent.user_id))
      .not('status', 'in', '("Completed","Cancelled","Converted to Enquiry")')
    if (countError) throw new Error(`Open visit counts could not be loaded: ${countError.message}`)
    return {
      id: String(agent.user_id),
      name: String(agent.display_name),
      department: String(agent.department ?? ''),
      specialization: String(agent.specialization ?? ''),
      availability: agent.availability as WalkinAgent['availability'],
      openVisits: count ?? 0,
    }
  }))
}

export async function getCurrentWalkinAgentId(): Promise<string> {
  if (isDemo) return localStorage.getItem('guru-group:walkin-demo-agent') || demoAgentId
  const { data, error } = await getSupabase().auth.getUser()
  if (error) throw new Error(`Your signed-in user could not be loaded: ${error.message}`)
  if (!data.user) throw new Error('Sign in before viewing assigned walk-in visits.')
  return data.user.id
}

export async function canManageWalkinAgents(): Promise<boolean> {
  if (isDemo) return true
  const { data, error } = await getSupabase().auth.getUser()
  if (error) throw new Error(`Your signed-in user could not be loaded: ${error.message}`)
  if (!data.user) throw new Error('Sign in before viewing sales agent permissions.')
  return ['manager', 'admin'].includes(String(data.user.app_metadata.role ?? ''))
}

export function setCurrentDemoWalkinAgentId(agentId: string) {
  if (!isDemo) throw new Error('A demo sales identity can only be selected in demo mode.')
  if (!demoAgents.some((agent) => agent.id === agentId)) throw new Error('Choose a configured demo sales agent.')
  localStorage.setItem('guru-group:walkin-demo-agent', agentId)
}

export async function listWalkinVisits(filters: WalkinFilters = {}): Promise<WalkinVisit[]> {
  if (isDemo) {
    return filterVisits(readLocal<WalkinVisit[]>(storageKey, []), filters)
      .sort((a, b) => b.checkInAt.localeCompare(a.checkInAt))
  }
  let query = getSupabase()
    .from('walkin_visits')
    .select('*, walkin_sales_agents!walkin_visits_assigned_agent_id_fkey(display_name)')
    .order('check_in_at', { ascending: false })
  if (filters.assignedAgentId) query = query.eq('assigned_agent_id', filters.assignedAgentId)
  if (filters.purpose) query = query.eq('purpose', filters.purpose)
  if (filters.status) query = query.eq('status', filters.status)
  if (filters.from) query = query.gte('check_in_at', `${filters.from}T00:00:00`)
  if (filters.to) query = query.lte('check_in_at', `${filters.to}T23:59:59.999`)
  if (filters.query?.trim()) {
    const safeQuery = filters.query.trim().replace(/[(),]/g, ' ')
    query = query.or(`customer_name.ilike.%${safeQuery}%,mobile_number.ilike.%${safeQuery}%,visit_number.ilike.%${safeQuery}%`)
  }
  const { data, error } = await query
  if (error) throw new Error(`Walk-in visits could not be loaded: ${error.message}`)
  return ((data ?? []) as Array<Record<string, unknown>>).map((row) => toVisit({
    ...row,
    assigned_agent_name: (row.walkin_sales_agents as Record<string, unknown> | null)?.display_name,
  }))
}

export async function getWalkinVisit(id: string): Promise<WalkinVisit | null> {
  if (isDemo) return readLocal<WalkinVisit[]>(storageKey, []).find((visit) => visit.id === id) ?? null
  const { data, error } = await getSupabase()
    .from('walkin_visits')
    .select('*, walkin_sales_agents!walkin_visits_assigned_agent_id_fkey(display_name)')
    .eq('id', id)
    .maybeSingle()
  if (error) throw new Error(`Walk-in visit could not be loaded: ${error.message}`)
  if (!data) return null
  const row = data as Record<string, unknown>
  return toVisit({
    ...row,
    assigned_agent_name: (row.walkin_sales_agents as Record<string, unknown> | null)?.display_name,
  })
}

export async function registerWalkin(input: RegisterWalkinInput): Promise<WalkinVisit> {
  if (isDemo) {
    const visits = readLocal<WalkinVisit[]>(storageKey, [])
    const now = new Date().toISOString()
    const id = crypto.randomUUID()
    const visit: WalkinVisit = {
      ...input,
      id,
      visitNumber: `WI-${now.slice(0, 10).replaceAll('-', '')}-${id.slice(0, 6).toUpperCase()}`,
      registeredBy: 'Reception',
      assignedAgentId: null,
      assignedAgentName: null,
      assignmentNote: '',
      status: input.appointmentStatus === 'Walk-in' ? 'Waiting for Assignment' : 'Registered',
      checkInAt: now,
      assignedAt: null,
      convertedEnquiryReference: null,
      createdAt: now,
      updatedAt: now,
    }
    visits.push(visit)
    saveLocal(storageKey, visits)
    addLocalHistory(visit, 'Registered', null, visit.status, 'Visitor registered at reception.')
    return visit
  }
  const { data, error } = await getSupabase()
    .from('walkin_visits')
    .insert({
      customer_name: input.customerName,
      mobile_number: input.mobileNumber,
      email: input.email || null,
      company_name: input.companyName || null,
      purpose: input.purpose,
      purpose_description: input.purposeDescription || null,
      product_or_service: input.productOrService || null,
      department: input.department || null,
      requested_employee: input.requestedEmployee || null,
      appointment_status: input.appointmentStatus,
      appointment_reference: input.appointmentReference || null,
      status: input.appointmentStatus === 'Walk-in' ? 'Waiting for Assignment' : 'Registered',
      internal_notes: input.internalNotes || null,
    })
    .select()
    .single()
  if (error) throw new Error(`Walk-in could not be registered: ${error.message}`)
  return toVisit(data as Record<string, unknown>)
}

export async function assignWalkin(id: string, agentId: string | null, note: string) {
  if (isDemo) {
    const visits = readLocal<WalkinVisit[]>(storageKey, [])
    const visit = visits.find((item) => item.id === id)
    if (!visit) throw new Error('Walk-in visit was not found.')
    if (['Completed', 'Cancelled', 'Converted to Enquiry'].includes(visit.status)) {
      throw new Error('A closed visit cannot be assigned or reassigned.')
    }
    const previous = visit.assignedAgentName
    const agent = demoAgents.find((item) => item.id === agentId)
    visit.assignedAgentId = agentId
    visit.assignedAgentName = agent?.name ?? null
    visit.assignmentNote = note
    visit.assignedAt = agentId ? new Date().toISOString() : null
    visit.status = agentId ? 'Assigned' : 'Waiting for Assignment'
    visit.updatedAt = new Date().toISOString()
    saveLocal(storageKey, visits)
    addLocalHistory(visit, previous ? 'Reassigned' : 'Assigned', previous, agent?.name ?? 'Unassigned', note)
    return visit
  }
  const { data, error } = await getSupabase().rpc('assign_walkin_visit', {
    p_visit_id: id,
    p_agent_id: agentId,
    p_note: note || null,
  })
  if (error) throw new Error(`Walk-in assignment could not be saved: ${error.message}`)
  return toVisit(data as Record<string, unknown>)
}

const statusTransitions: Record<WalkinStatus, WalkinStatus[]> = {
  Registered: ['Waiting for Assignment', 'Assigned', 'Cancelled'],
  'Waiting for Assignment': ['Assigned', 'Cancelled'],
  Assigned: ['Waiting for Sales Agent', 'In Discussion', 'Cancelled'],
  'Waiting for Sales Agent': ['In Discussion', 'Cancelled'],
  'In Discussion': ['Requirements Captured', 'Follow-up Required', 'Completed', 'Cancelled'],
  'Requirements Captured': ['Follow-up Required', 'Converted to Enquiry', 'Completed'],
  'Follow-up Required': ['In Discussion', 'Requirements Captured', 'Converted to Enquiry', 'Completed', 'Cancelled'],
  'Converted to Enquiry': ['Completed'],
  Completed: [],
  Cancelled: [],
}

export function getAllowedWalkinTransitions(status: WalkinStatus) {
  return statusTransitions[status]
}

export async function updateWalkinStatus(id: string, status: WalkinStatus) {
  const visit = await getWalkinVisit(id)
  if (!visit) throw new Error('Walk-in visit was not found.')
  if (!statusTransitions[visit.status].includes(status)) {
    throw new Error(`Cannot change visit status from ${visit.status} to ${status}.`)
  }
  if (isDemo) {
    const visits = readLocal<WalkinVisit[]>(storageKey, [])
    const found = visits.find((item) => item.id === id)
    if (!found) throw new Error('Walk-in visit was not found.')
    found.status = status
    found.updatedAt = new Date().toISOString()
    saveLocal(storageKey, visits)
    addLocalHistory(found, 'Status changed', visit.status, status, '')
    return found
  }
  const { data, error } = await getSupabase().rpc('update_walkin_visit_status', {
    p_visit_id: id,
    p_status: status,
  })
  if (error) throw new Error(`Visit status could not be updated: ${error.message}`)
  return toVisit(data as Record<string, unknown>)
}

export async function getWalkinRequirements(id: string): Promise<WalkinRequirement> {
  if (isDemo) {
    const all = readLocal<Record<string, WalkinRequirement>>('guru-group:walkin-requirements', {})
    return all[id] ?? toRequirement()
  }
  const { data, error } = await getSupabase()
    .from('walkin_requirements')
    .select('*')
    .eq('walkin_visit_id', id)
    .maybeSingle()
  if (error) throw new Error(`Visit requirements could not be loaded: ${error.message}`)
  return toRequirement(data as Record<string, unknown> | null)
}

export async function saveWalkinRequirements(id: string, requirement: WalkinRequirement) {
  const visit = await getWalkinVisit(id)
  if (!visit) throw new Error('Walk-in visit was not found.')
  if (['Completed', 'Cancelled', 'Converted to Enquiry'].includes(visit.status)) {
    throw new Error('Requirements cannot be changed for a closed visit.')
  }
  if (isDemo) {
    const all = readLocal<Record<string, WalkinRequirement>>('guru-group:walkin-requirements', {})
    const saved = { ...requirement, updatedAt: new Date().toISOString() }
    all[id] = saved
    saveLocal('guru-group:walkin-requirements', all)
    const visits = readLocal<WalkinVisit[]>(storageKey, [])
    const found = visits.find((item) => item.id === id)
    if (!found) throw new Error('Walk-in visit was not found.')
    if (statusTransitions[found.status].includes('Requirements Captured')) {
      const previous = found.status
      found.status = 'Requirements Captured'
      found.updatedAt = saved.updatedAt ?? new Date().toISOString()
      saveLocal(storageKey, visits)
      addLocalHistory(found, 'Status changed', previous, found.status, '')
    }
    addLocalHistory(found, 'Requirements updated', null, null, 'Sales requirement draft saved.')
    return saved
  }
  const { data, error } = await getSupabase()
    .from('walkin_requirements')
    .upsert(mapRequirement(requirement, id), { onConflict: 'walkin_visit_id' })
    .select()
    .single()
  if (error) throw new Error(`Requirements could not be saved: ${error.message}`)
  if (statusTransitions[visit.status].includes('Requirements Captured')) {
    const { error: statusError } = await getSupabase()
      .rpc('update_walkin_visit_status', { p_visit_id: id, p_status: 'Requirements Captured' })
    if (statusError) throw new Error(`Requirements were saved, but visit status could not be updated: ${statusError.message}`)
  }
  return toRequirement(data as Record<string, unknown>)
}

export async function listWalkinRecordings(visitId: string): Promise<WalkinRecording[]> {
  if (isDemo) {
    const recordings = readLocal<WalkinRecording[]>(recordingStorageKey, [])
    return recordings
      .filter((recording) => recording.visitId === visitId)
      .sort((a, b) => b.startedAt.localeCompare(a.startedAt))
  }
  const { data, error } = await getSupabase()
    .from('walkin_recordings')
    .select('*')
    .eq('walkin_visit_id', visitId)
    .order('started_at', { ascending: false })
  if (error) throw new Error(`Visit recordings could not be loaded: ${error.message}`)
  return ((data ?? []) as Array<Record<string, unknown>>).map(toRecording)
}

export async function saveWalkinRecording(recording: WalkinRecording): Promise<WalkinRecording> {
  const visit = await getWalkinVisit(recording.visitId)
  if (!visit) throw new Error('Walk-in visit was not found.')
  if (['Completed', 'Cancelled', 'Converted to Enquiry'].includes(visit.status)) {
    throw new Error('A recording cannot be saved for a closed visit.')
  }
  if (!recording.transcript.trim()) throw new Error('Add or review the transcript before saving.')

  if (isDemo) {
    const recordings = readLocal<WalkinRecording[]>(recordingStorageKey, [])
    const now = new Date().toISOString()
    const saved: WalkinRecording = {
      ...recording,
      transcript: recording.transcript.trim(),
      status: 'saved',
      audioStoragePath: null,
      createdBy: recordings.find((item) => item.id === recording.id)?.createdBy ?? 'Reception / Sales team',
      createdAt: recordings.find((item) => item.id === recording.id)?.createdAt ?? now,
      updatedAt: now,
    }
    const index = recordings.findIndex((item) => item.id === saved.id)
    if (index < 0) recordings.push(saved)
    else recordings[index] = saved
    saveLocal(recordingStorageKey, recordings)
    return saved
  }

  const { data, error } = await getSupabase()
    .from('walkin_recordings')
    .upsert({
      id: recording.id,
      walkin_visit_id: recording.visitId,
      transcript: recording.transcript.trim(),
      started_at: recording.startedAt,
      ended_at: recording.endedAt,
      duration_seconds: recording.durationSeconds,
      recording_status: 'saved',
      audio_storage_path: null,
      consent_at: recording.consentAt,
    }, { onConflict: 'id' })
    .select()
    .single()
  if (error) throw new Error(`Recording transcript could not be saved: ${error.message}`)
  return toRecording(data as Record<string, unknown>)
}

export async function listWalkinHistory(visitId?: string): Promise<WalkinHistoryEvent[]> {
  if (isDemo) {
    return readLocal<WalkinHistoryEvent[]>(historyStorageKey, [])
      .filter((event) => !visitId || event.visitId === visitId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  }
  let query = getSupabase().from('walkin_visit_history').select('*').order('created_at', { ascending: false })
  if (visitId) query = query.eq('walkin_visit_id', visitId)
  const { data, error } = await query
  if (error) throw new Error(`Visit history could not be loaded: ${error.message}`)
  return ((data ?? []) as Array<Record<string, unknown>>).map((row) => ({
    id: String(row.id),
    visitId: String(row.walkin_visit_id),
    eventType: String(row.event_type),
    oldValue: row.old_value ? String(row.old_value) : null,
    newValue: row.new_value ? String(row.new_value) : null,
    notes: String(row.notes ?? ''),
    performedBy: String(row.performed_by ?? ''),
    createdAt: String(row.created_at),
  }))
}

export async function markWalkinConverted(id: string, enquiryReference: string) {
  const visit = await getWalkinVisit(id)
  if (!visit) throw new Error('Walk-in visit was not found.')
  if (visit.convertedEnquiryReference) return visit
  if (!['Requirements Captured', 'Follow-up Required'].includes(visit.status)) {
    throw new Error('Capture and review the visit requirements before conversion.')
  }
  if (isDemo) {
    const visits = readLocal<WalkinVisit[]>(storageKey, [])
    const found = visits.find((item) => item.id === id)
    if (!found) throw new Error('Walk-in visit was not found.')
    if (found.convertedEnquiryReference) return found
    found.convertedEnquiryReference = enquiryReference
    found.status = 'Converted to Enquiry'
    found.updatedAt = new Date().toISOString()
    saveLocal(storageKey, visits)
    addLocalHistory(found, 'Converted to enquiry', null, enquiryReference, 'Visit linked to its sales enquiry.')
    return found
  }
  const { data, error } = await getSupabase().rpc('convert_walkin_visit', {
    p_visit_id: id,
    p_enquiry_reference: enquiryReference,
  })
  if (error) throw new Error(`Visit could not be marked as converted: ${error.message}`)
  return toVisit(data as Record<string, unknown>)
}

export async function updateWalkinAgentAvailability(agentId: string, availability: WalkinAgent['availability']) {
  if (isDemo) {
    const agent = demoAgents.find((item) => item.id === agentId)
    if (!agent) throw new Error('Sales agent was not found.')
    agent.availability = availability
    return
  }
  const { error } = await getSupabase()
    .from('walkin_sales_agents')
    .update({ availability })
    .eq('user_id', agentId)
  if (error) throw new Error(`Agent availability could not be updated: ${error.message}`)
}

export function emptyWalkinRequirement(): WalkinRequirement {
  return {
    detailedRequirement: '',
    productOrService: '',
    quantity: '',
    budgetRange: '',
    expectedPurchaseTimeline: '',
    preferredSolution: '',
    competitorOrAlternative: '',
    specialRequests: '',
    followUpAt: '',
    agentNotes: '',
    nextAction: '',
    updatedAt: null,
  }
}
