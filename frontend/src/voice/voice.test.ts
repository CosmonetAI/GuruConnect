import { afterEach, describe, expect, it, vi } from 'vitest'
import { canAutoFill, defaultConfidencePercent, extractSuggestions, normalizeConfidencePercent, spokenDigits } from './extract'
import { createLevelWatcher, levelPercent } from './levelWatcher'
import { classifySpeechError, liveSpeechSupported, startLiveSpeech } from './liveSpeech'
import type { LiveSegment } from './liveSpeech'

const seg = (text: string, speaker: LiveSegment['speaker'] = 'Unknown', final = true): LiveSegment => ({ id: text, text, final, speaker })

describe('extractSuggestions', () => {
  it('extracts only cue-backed values', () => {
    const result = extractSuggestions([seg('my name is priya sharma and I need a laptop'), seg('my mobile number is 98765 43210'), seg('email is priya at example dot com'), seg('I work at Acme Traders')])
    expect(Object.fromEntries(result.map(item => [item.field, item.value]))).toEqual({ customerName: 'Priya Sharma', mobileNumber: '9876543210', email: 'priya@example.com', companyName: 'Acme Traders' })
  })
  it('does not invent values from ordinary speech', () => {
    expect(extractSuggestions([seg('I am looking for a laptop'), seg('I am from Mumbai'), seg('call me tomorrow at five')])).toEqual([])
  })
  it('ignores interim and receptionist segments and rejects short numbers', () => {
    expect(extractSuggestions([seg('my name is Ravi', 'Unknown', false), seg('my name is Anil', 'Receptionist'), seg('phone number is 123')])).toEqual([])
  })
  it('scores lower without a Visitor label and gates auto-fill on the label and threshold', () => {
    const unlabelled = extractSuggestions([seg('my name is Ravi')])[0]
    const visitor = extractSuggestions([seg('my name is Ravi', 'Visitor')])[0]
    expect(unlabelled.score).toBeLessThan(visitor.score)
    expect(canAutoFill(unlabelled, 70)).toBe(false)
    expect(canAutoFill(visitor, 85)).toBe(true)
    expect(canAutoFill(visitor, 95)).toBe(false)
    expect(canAutoFill(extractSuggestions([seg('phone number is 12345678', 'Visitor')])[0], 70)).toBe(true)
    expect(canAutoFill(extractSuggestions([seg('phone number is 12345678', 'Visitor')])[0], 75)).toBe(false)
  })
  it('never allows auto-fill for receptionist or unknown phone and email', () => {
    for (const text of ['my mobile number is 9876543210', 'email is desk@example.com']) {
      expect(extractSuggestions([seg(text, 'Receptionist')])).toEqual([])
      const [item] = extractSuggestions([seg(text, 'Unknown')])
      expect(item).toBeDefined()
      expect(canAutoFill(item, 70)).toBe(false)
    }
  })
  it('falls back to the default threshold for invalid settings', () => {
    expect(normalizeConfidencePercent(10)).toBe(defaultConfidencePercent)
    expect(normalizeConfidencePercent(NaN)).toBe(defaultConfidencePercent)
    expect(normalizeConfidencePercent(90)).toBe(90)
  })
  it('flags a differing repeated value as a conflict', () => {
    expect(extractSuggestions([seg('email is a@x.com'), seg('email is b@x.com')])[0]).toMatchObject({ value: 'b@x.com', conflict: true })
    expect(extractSuggestions([seg('email is a@x.com'), seg('email is A@x.com')])[0].conflict).toBe(false)
  })
  it('uses a legal suffix to accept "from" companies and latest value wins', () => {
    expect(extractSuggestions([seg('I am from Blue Star Pvt Ltd')])[0]).toMatchObject({ field: 'companyName', value: 'Blue Star Pvt Ltd' })
    expect(extractSuggestions([seg('my name is Asha'), seg('sorry my name is Aisha')])[0].value).toBe('Aisha')
  })
  it('converts spoken digits', () => {
    expect(spokenDigits('double nine eight')).toBe('998')
  })
})

describe('createLevelWatcher', () => {
  it('alerts only after the configured quiet period and resets on speech', () => {
    const watcher = createLevelWatcher(() => ({ thresholdPercent: 5, seconds: 3 }))
    expect(levelPercent(0.5)).toBe(100)
    expect(watcher.push(0.001, 0)).toBe(false)
    expect(watcher.push(0.001, 2900)).toBe(false)
    expect(watcher.push(0.001, 3000)).toBe(true)
    expect(watcher.push(0.1, 3100)).toBe(false)
    expect(watcher.push(0.001, 4000)).toBe(false)
  })
  it('respects changed thresholds', () => {
    let threshold = 1
    const watcher = createLevelWatcher(() => ({ thresholdPercent: threshold, seconds: 1 }))
    watcher.push(0.01, 0)
    expect(watcher.push(0.01, 1000)).toBe(false)
    threshold = 20
    watcher.reset(); watcher.push(0.01, 2000)
    expect(watcher.push(0.01, 3000)).toBe(true)
  })
})

describe('liveSpeech', () => {
  afterEach(() => { delete (globalThis as Record<string, unknown>).webkitSpeechRecognition })
  it('reports unsupported browsers', () => {
    const onError = vi.fn()
    expect(liveSpeechSupported()).toBe(false)
    expect(startLiveSpeech({ onSegment: vi.fn(), onError })).toBeNull()
    expect(onError).toHaveBeenCalledWith('unsupported')
  })
  it('emits Unknown-speaker segments, restarts after silence and releases on stop', () => {
    const instances: Array<Record<string, any>> = []
    ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = class { start = vi.fn(); abort = vi.fn(); constructor() { instances.push(this as never) } }
    const onSegment = vi.fn(); const onError = vi.fn()
    const handle = startLiveSpeech({ onSegment, onError }, 'en-IN')!
    const recognition = instances[0]
    expect(recognition.lang).toBe('en-IN')
    recognition.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: ' hello ' }], { isFinal: true })] })
    expect(onSegment).toHaveBeenCalledWith(expect.objectContaining({ text: 'hello', final: true, speaker: 'Unknown' }))
    recognition.onend()
    expect(recognition.start).toHaveBeenCalledTimes(2)
    recognition.onerror({ error: 'no-speech' })
    expect(onError).not.toHaveBeenCalled()
    handle.stop()
    expect(recognition.abort).toHaveBeenCalled()
    expect(recognition.onend).toBeNull()
  })
  it('classifies errors', () => {
    expect(classifySpeechError('not-allowed')).toBe('permission')
    expect(classifySpeechError('network')).toBe('network')
    expect(classifySpeechError('aborted')).toBeNull()
  })
})