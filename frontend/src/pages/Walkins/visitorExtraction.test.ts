import { describe, expect, it } from 'vitest'
import type { Assignment, Turn } from '../../services/officeApi'
import { extractVisitorFields, VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD } from './visitorExtraction'

const turn = (id: string, speaker: string, text: string): Turn => ({
  id,
  speaker_id: speaker,
  start_seconds: 0,
  end_seconds: 2,
  english_text: text,
})

describe('visitor field extraction', () => {
  it('extracts only from the speaker explicitly confirmed as the visitor', () => {
    const turns = [
      turn('receptionist', 'voice-a', 'My name is Priya and my email is reception@example.com.'),
      turn('visitor', 'voice-b', 'My name is Rahul Sharma. I am from ABC Technologies.'),
      turn('contact', 'voice-b', 'You can reach me at +91 98765 43210 or rahul@example.com.'),
      turn('purpose', 'voice-b', 'I am here to discuss a software development proposal.'),
    ]
    const assignments: Assignment[] = [
      { speaker_id: 'voice-a', name: 'Receptionist', role: 'salesperson' },
      { speaker_id: 'voice-b', name: 'Visitor', role: 'customer' },
    ]

    const result = extractVisitorFields(turns, assignments)

    expect(result.customerName?.value).toBe('Rahul Sharma')
    expect(result.companyName?.value).toBe('ABC Technologies')
    expect(result.mobileNumber?.value).toBe('+91 98765 43210')
    expect(result.email?.value).toBe('rahul@example.com')
    expect(result.purpose?.value).toBe('New business enquiry')
    expect(result.customerName?.confidence).toBeGreaterThanOrEqual(VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD)
    expect(Object.values(result).some(field => field?.value.includes('Priya') || field?.value.includes('reception@example.com'))).toBe(false)
  })

  it('flags conflicting visitor values for confirmation instead of selecting one silently', () => {
    const result = extractVisitorFields([
      turn('first', 'visitor', 'My name is Rahul Sharma.'),
      turn('correction', 'visitor', 'Sorry, my name is Rahul Kumar Sharma.'),
    ], [{ speaker_id: 'visitor', name: 'Visitor', role: 'customer' }])

    expect(result.customerName?.conflict).toBe(true)
    expect(result.customerName?.alternatives).toEqual(['Rahul Sharma', 'Rahul Kumar Sharma'])
  })

  it('leaves fields empty when speakers have not been assigned', () => {
    expect(extractVisitorFields([turn('unassigned', 'voice-a', 'My name is Rahul Sharma.')], [])).toEqual({})
  })
})
