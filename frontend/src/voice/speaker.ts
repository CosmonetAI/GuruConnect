import type { LiveSegment } from './liveSpeech'

type Speaker = LiveSegment['speaker']

const receptionist = [
  /\bhow (?:can|may) i help\b/, /\bmay i (?:have|know|get)\b/, /\bcan i (?:have|get|take)\b/, /\bcould you (?:please|tell|share|give|spell)\b/,
  /\bcan you (?:please|tell|share|give|spell)\b/, /\bwelcome\b/, /\bwho (?:are you|would you like to|do you want to)\b/, /\bwhat(?:'s| is) your (?:name|number|email|company)\b/,
  /\byour (?:name|number|email|company)\b.*\?/, /\bplease (?:have a seat|wait|take|share|tell|give|spell)\b/, /\bhave a seat\b/, /\bwhich (?:company|department)\b/,
  /\bhow may i\b/, /\bthank you for (?:coming|waiting|visiting)\b/, /\blet me (?:check|call|inform|note)\b/,
]
const visitor = [
  /\bmy name(?:'s| is)\b/, /\bmyself\b/, /\bi(?:'m| am) (?:from|here|looking|calling|working|a |an |the )/, /\bi (?:work|want|wanted|need|would like|have an? (?:appointment|meeting))\b/,
  /\b(?:my|our) (?:email|e-mail|mobile|phone|number|company|organi[sz]ation|contact)\b/, /\bcompany(?: name)? is\b/, /\bhere (?:to|for)\b/, /\bi(?:'m| am) looking\b/,
  /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]/,
]

// Text-only heuristic: the browser speech API does not identify voices, so this is a best guess the user can override.
export function classifySpeaker(text: string, previous: Speaker = 'Unknown'): Speaker {
  const value = text.toLowerCase().trim()
  if (!value) return 'Unknown'
  const r = receptionist.some(pattern => pattern.test(value))
  const v = visitor.some(pattern => pattern.test(value)) || (value.replace(/\D/g, '').length >= 8 && !value.includes('?'))
  if (v && !r) return 'Visitor'
  if (r && !v) return 'Receptionist'
  if (r && v) return previous === 'Receptionist' ? 'Visitor' : 'Receptionist'
  // A plain statement right after the receptionist spoke is taken as the visitor's reply, and a question after the visitor as the receptionist's.
  if (previous === 'Receptionist') return value.endsWith('?') ? 'Receptionist' : 'Visitor'
  if (previous === 'Visitor' && value.endsWith('?')) return 'Receptionist'
  return previous === 'Visitor' ? 'Visitor' : 'Unknown'
}
