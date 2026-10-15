import type { LiveSegment } from './liveSpeech'

export type VisitorField = 'customerName' | 'mobileNumber' | 'email' | 'companyName'
export interface FieldSuggestion { field: VisitorField; value: string; score: number; speaker: LiveSegment['speaker']; evidence: string; segmentId: string; conflict?: boolean }

export const confidenceOptions = [70, 75, 80, 85, 90, 95] as const
export const defaultConfidencePercent = 85
// Rejects anything outside the supported range so a bad setting can never lower the bar silently.
export const normalizeConfidencePercent = (value: number) => (confidenceOptions as readonly number[]).includes(value) ? value : defaultConfidencePercent
// Auto-fill needs a Visitor-labelled segment: unlabelled or Receptionist speech may be someone else's details.
export const canAutoFill = (item: FieldSuggestion, thresholdPercent: number) => item.speaker === 'Visitor' && !item.conflict && item.score * 100 >= normalizeConfidencePercent(thresholdPercent)

const digitWords: Record<string, string> = { zero: '0', oh: '0', o: '0', one: '1', two: '2', three: '3', four: '4', five: '5', six: '6', seven: '7', eight: '8', nine: '9' }
const nameStop = new Set(['and', 'from', 'i', 'my', 'calling', 'here', 'is', 'am', 'looking', 'want', 'wanted', 'need', 'working', 'work', 'at', 'for', 'the', 'a', 'an', 'to', 'in', 'of', 'email', 'phone', 'mobile', 'number', 'company'])
const companyStop = new Set(['and', 'my', 'i', 'email', 'phone', 'mobile', 'number', 'contact', 'we', 'is', 'looking', 'want', 'wanted', 'need', 'because', 'but', 'so'])
const companySuffix = /^(pvt|private|ltd|limited|llp|inc|corp|corporation|technologies|technology|solutions|systems|industries|enterprises|traders|services|group|labs|associates|consulting)\.?$/i

const titleCase = (words: string[]) => words.map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
const words = (text: string) => text.replace(/[,.;!?]+/g, ' ').split(/\s+/).filter(Boolean)

function takeWords(source: string, stop: Set<string>, max: number) {
  const out: string[] = []
  for (const word of words(source)) {
    if (stop.has(word.toLowerCase()) || !/^[\p{L}][\p{L}'&.-]*$/u.test(word)) break
    out.push(word)
    if (out.length === max) break
  }
  return out
}

export function spokenDigits(source: string): string {
  let out = ''
  const tokens = words(source.replace(/[()-]/g, ' '))
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index].toLowerCase()
    if (/^\+?\d+$/.test(token)) out += token
    else if ((token === 'double' || token === 'triple') && digitWords[tokens[index + 1]?.toLowerCase()]) { out += digitWords[tokens[++index].toLowerCase()].repeat(token === 'double' ? 2 : 3) }
    else if (digitWords[token] !== undefined) out += digitWords[token]
    else break
  }
  return out
}

function spokenEmail(source: string) {
  const local = source.toLowerCase()
    .replace(/\b(at the rate( of)?|at sign|at)\b/g, '@').replace(/\b(dot|period)\b/g, '.')
    .replace(/\b(underscore)\b/g, '_').replace(/\b(dash|hyphen)\b/g, '-')
  const match = local.replace(/\s*([@._-])\s*/g, '$1').match(/[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+/)
  return match?.[0].replace(/[.]+$/, '') ?? null
}

// Conservative, cue-based extraction. Nothing is returned without an explicit phrase such as "my name is".
export function extractFromSegment(segment: LiveSegment): FieldSuggestion[] {
  const text = segment.text.trim()
  if (!segment.final || segment.speaker === 'Receptionist' || !text) return []
  // Self-introductions (name, company) are ambiguous when the speaker is unknown, so they stay medium until a Visitor label exists.
  const score = (base: number) => Math.round((segment.speaker === 'Visitor' ? base : base - 0.2) * 100) / 100
  const out: FieldSuggestion[] = []
  const add = (field: VisitorField, value: string, base: number, evidence: string) => { if (value) out.push({ field, value, score: score(base), speaker: segment.speaker, evidence, segmentId: segment.id }) }

  const literal = text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/)
  if (literal) add('email', literal[0].toLowerCase(), 0.95, literal[0])
  else {
    const cue = text.match(/\b(?:e-?mail(?: (?:address|id))?|mail id)\s+(?:is|:)?\s*(.+)$/i)
    const spoken = cue && spokenEmail(cue[1])
    if (spoken) add('email', spoken, 0.8, cue![0])
  }

  const phone = text.match(/\b(?:mobile|phone|contact|whats ?app|cell)(?: number| no\.?)?\s+(?:is|:)\s*(.+)$/i)
  if (phone) {
    const digits = spokenDigits(phone[1])
    if (digits.replace(/\D/g, '').length >= 7 && digits.replace(/\D/g, '').length <= 15) add('mobileNumber', digits, digits.replace(/\D/g, '').length === 10 ? 0.9 : 0.7, phone[0])
  }

  const name = text.match(/\b(?:my name is|my name's|name is|myself|i am called)\s+(.+)$/i)
  if (name) {
    const parts = takeWords(name[1], nameStop, 3)
    if (parts.length) add('customerName', titleCase(parts), 0.9, name[0])
  }

  const cued = text.match(/\b(?:company(?: name)?|organi[sz]ation|firm) is\s+(.+)$/i) ?? text.match(/\bi (?:work|am working|'m working) (?:at|for|with)\s+(.+)$/i)
  if (cued) {
    const parts = takeWords(cued[1], companyStop, 5)
    if (parts.length) add('companyName', titleCase(parts), 0.75, cued[0])
  } else {
    // "from <name> <legal suffix>" is accepted only when a company suffix confirms it is not a place.
    const all = words(text)
    let suffixed = words(text).findIndex((word, index, all) => index > 0 && companySuffix.test(word) && all[index - 1] && !companyStop.has(all[index - 1].toLowerCase()))
    while (suffixed >= 0 && companySuffix.test(all[suffixed + 1] ?? '')) suffixed++
    const fromAt = all.findIndex(word => word.toLowerCase() === 'from')
    if (suffixed > fromAt && fromAt >= 0) {
      const parts = all.slice(fromAt + 1, suffixed + 1)
      if (parts.length >= 2 && parts.length <= 6 && !parts.some(word => companyStop.has(word.toLowerCase()))) add('companyName', titleCase(parts), 0.7, all.slice(fromAt, suffixed + 1).join(' '))
    }
  }  return out
}

// Latest suggestion per field wins; callers decide whether anything is applied.
export function extractSuggestions(segments: LiveSegment[]): FieldSuggestion[] {
  const latest = new Map<VisitorField, FieldSuggestion>()
  for (const segment of segments) for (const suggestion of extractFromSegment(segment)) {
    const prior = latest.get(suggestion.field)
    // A different value heard earlier for the same field means the speaker may have corrected or contradicted themselves.
    const conflict = !!prior && (prior.conflict || prior.value.toLowerCase() !== suggestion.value.toLowerCase())
    latest.set(suggestion.field, { ...suggestion, conflict })
  }
  return [...latest.values()]
}