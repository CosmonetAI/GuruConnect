import type { Assignment, Turn } from '../../services/officeApi'
import { walkinPurposes } from '../../types/walkin'

export const VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD = 0.78

export type VisitorField =
  | 'customerName'
  | 'mobileNumber'
  | 'companyName'
  | 'email'
  | 'purpose'
  | 'purposeDescription'
  | 'department'
  | 'requestedEmployee'

export type VisitorFieldSuggestion = {
  value: string
  confidence: number
  sourceTurnId: string
  sourceText: string
  conflict: boolean
  alternatives: string[]
}

type Candidate = Omit<VisitorFieldSuggestion, 'conflict' | 'alternatives'>

function clean(value: string) {
  return value.trim().replace(/^[\s"'.,:;-]+|[\s"'.,:;-]+$/g, '').replace(/\s+/g, ' ')
}

function captures(text: string, expression: RegExp) {
  const match = expression.exec(text)
  return match?.[1] ? clean(match[1]) : ''
}

function getCandidates(text: string, turn: Turn): Partial<Record<VisitorField, Candidate>> {
  const candidates: Partial<Record<VisitorField, Candidate>> = {}
  const add = (field: VisitorField, value: string, confidence: number) => {
    if (value) candidates[field] = { value, confidence, sourceTurnId: turn.id, sourceText: text }
  }

  const name = captures(text, /\b(?:my name is|myself is|i am(?!\s+(?:here|visiting|from|with)\b)|i'm(?!\s+(?:here|visiting|from|with)\b)|this is)\s+(.+?)(?=[,.!?;]|\s+(?:and|my|i have|you can|from|with)\b|$)/i)
  if (name && name.length <= 150 && !/\d/.test(name)) add('customerName', name, 0.93)

  const email = text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0]?.toLowerCase()
  if (email && email.length <= 254) add('email', email, 0.99)

  const phoneCandidates = text.match(/(?:\+\s*\d{1,3}[\s().-]*)?(?:\d[\s().-]*){7,15}/g) ?? []
  const phone = phoneCandidates.map(clean).find(value => {
    const digits = value.replace(/\D/g, '')
    return digits.length >= 7 && digits.length <= 15
  })
  if (phone) add('mobileNumber', phone, 0.96)

  const company = captures(text, /\b(?:i work (?:at|for)|i am with|i'm with|company is|i am from|i'm from|we are from)\s+(?:the\s+)?(.+?)(?=[,.!?;]|\s+(?:and|my|our|i have)\b|$)/i)
  if (company && company.length <= 150) add('companyName', company, 0.84)

  const employee = captures(text, /\b(?:meet|meeting|see|speak to|talk to)\s+(?:(?:mr|mrs|ms|miss|dr)\.?\s+)?([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})/i)
  if (employee && employee.length <= 150) add('requestedEmployee', employee, 0.83)

  const department = captures(text, /\b(?:department|team)\s+(?:is|of)\s+(.+?)(?=[,.!?;]|$)/i)
  if (department && department.length <= 150) add('department', department, 0.84)

  const reason = captures(text, /\b(?:i am here to|i'm here to|i have come to|the purpose of my visit is to|i am visiting to)\s+(.+?)(?=[,.!?;]|$)/i)
  if (reason && reason.length <= 2000) {
    add('purposeDescription', reason, 0.86)
    const purpose = walkinPurposes.find(option => {
      const normalized = reason.toLocaleLowerCase()
      if (option === 'Product demonstration') return /\bdemo(?:nstration)?\b/.test(normalized)
      if (option === 'Pricing or quotation request') return /\b(price|pricing|quote|quotation)\b/.test(normalized)
      if (option === 'Service or support request') return /\b(support|service|issue|help)\b/.test(normalized)
      if (option === 'Follow-up meeting') return /\bfollow[\s-]?up\b/.test(normalized)
      if (option === 'Meeting with a specific employee') return !!employee
      if (option === 'Existing order enquiry') return /\b(order|delivery|shipment)\b/.test(normalized)
      if (option === 'New business enquiry') return /\b(new business|partnership|proposal)\b/.test(normalized)
      if (option === 'Product enquiry') return /\b(product|enquir|information)\b/.test(normalized)
      return false
    })
    if (purpose) add('purpose', purpose, 0.82)
  }

  return candidates
}

export function extractVisitorFields(turns: Turn[], assignments: Assignment[]): Partial<Record<VisitorField, VisitorFieldSuggestion>> {
  // If a 'customer' assignment exists use that speaker; otherwise attempt to infer the most likely visitor speaker
  const assignedVisitorSpeakers = new Set(assignments.filter(item => item.role === 'customer').map(item => item.speaker_id))
  const grouped: Partial<Record<VisitorField, Candidate[]>> = {}

  // If no visitor assignment, score speakers by the quantity and confidence of detected candidates to pick the likeliest visitor.
  let visitorSpeakers = assignedVisitorSpeakers
  if (visitorSpeakers.size === 0) {
    const perSpeaker: Record<string, {sumConfidence: number; candidates: Candidate[]}> = {}
    for (const turn of turns) {
      if (!turn.speaker_id) continue
      const text = turn.english_text?.trim() || turn.source_text?.trim() || ''
      if (!text) continue
      const candidates = Object.entries(getCandidates(text, turn)) as [VisitorField, Candidate][]
      if (!candidates.length) continue
      const entry = perSpeaker[turn.speaker_id] ??= { sumConfidence: 0, candidates: [] }
      for (const [, candidate] of candidates) {
        entry.sumConfidence += candidate.confidence
        entry.candidates.push(candidate)
      }
    }
    const best = Object.entries(perSpeaker).sort((a, b) => b[1].sumConfidence - a[1].sumConfidence)[0]
    if (best) visitorSpeakers = new Set([best[0]])
  }

  for (const turn of turns) {
    if (!turn.speaker_id || !visitorSpeakers.has(turn.speaker_id)) continue
    const text = turn.english_text?.trim() || turn.source_text?.trim() || ''
    if (!text) continue
    for (const [field, candidate] of Object.entries(getCandidates(text, turn)) as [VisitorField, Candidate][]) {
      ;(grouped[field] ??= []).push(candidate)
    }
  }

  return Object.fromEntries(Object.entries(grouped).map(([field, values]) => {
    const unique = [...new Map(values.map(candidate => [candidate.value.toLocaleLowerCase(), candidate])).values()]
    const selected = unique.at(-1)!
    return [field, {
      ...selected,
      conflict: unique.length > 1,
      alternatives: unique.map(candidate => candidate.value),
    }]
  })) as Partial<Record<VisitorField, VisitorFieldSuggestion>>
}
