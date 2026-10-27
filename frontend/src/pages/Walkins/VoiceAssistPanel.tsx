import { useEffect, useRef, useState } from 'react'
import { Mic2, Sparkles } from 'lucide-react'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import { canAutoFill, confidenceOptions, defaultConfidencePercent, extractSuggestions, type VisitorField } from '../../voice/extract'
import { liveSpeechSupported, startLiveSpeech, type LiveSegment } from '../../voice/liveSpeech'
import { classifySpeaker } from '../../voice/speaker'
import { createLevelWatcher, defaultLevelSettings } from '../../voice/levelWatcher'
import type { CaptureState } from '../../recording/session'
import './Walkins.css'

export interface VoiceAssistPanelProps {
  customerName: string
  customerPhone?: string
  customerEmail?: string
  onConversationChange: (id: string | null) => void
  onBusyChange: (busy: boolean) => void
  values?: Record<VisitorField, string>
  validate?: (field: VisitorField, value: string) => string | null
  onApply?: (field: VisitorField, value: string) => void
  onAiFieldsChange?: (fields: VisitorField[]) => void
}
const labels: Record<VisitorField, string> = { customerName: 'customer name', mobileNumber: 'mobile number', email: 'email', companyName: 'company name' }
const blank = { customerName: '', mobileNumber: '', email: '', companyName: '' }

export default function VoiceAssistPanel({ customerName, customerPhone, customerEmail, onConversationChange, onBusyChange, values = blank, validate, onApply, onAiFieldsChange }: VoiceAssistPanelProps) {
  const [capture, setCapture] = useState<CaptureState | null>(null)
  const [captions, setCaptions] = useState(false)
  const [automatic, setAutomatic] = useState(true)
  const [threshold, setThreshold] = useState(defaultConfidencePercent)
  const [segments, setSegments] = useState<LiveSegment[]>([])
  const [speechError, setSpeechError] = useState('')
  const [quiet, setQuiet] = useState(false)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const callbacks = useRef({ values, validate, onApply, onAiFieldsChange }); callbacks.current = { values, validate, onApply, onAiFieldsChange }
  const applied = useRef(new Map<VisitorField, string>())
  const protectedFields = useRef(new Set<VisitorField>())
  const previous = useRef(values)
  const watcher = useRef(createLevelWatcher(() => defaultLevelSettings))
  const supported = liveSpeechSupported()
  const recording = capture?.capture === 'recording'
  useEffect(() => {
    for (const field of Object.keys(values) as VisitorField[]) {
      if (applied.current.has(field) && previous.current[field] !== values[field] && applied.current.get(field) !== values[field]) protectedFields.current.add(field)
    }
    previous.current = values
  }, [values])
  useEffect(() => {
    if (!capture || capture.capture !== 'recording') { watcher.current.reset(); setQuiet(false); return }
    setQuiet(watcher.current.push(capture.level, Date.now()))
  }, [capture])
  useEffect(() => {
    if (!captions || !recording || !supported) return
    setSpeechError('')
    const live = startLiveSpeech({
      onSegment: segment => setSegments(current => {
        const existing = current.find(item => item.id === segment.id)
        const speaker = existing?.manual ? existing.speaker : segment.speaker !== 'Unknown' ? segment.speaker : classifySpeaker(segment.text, current.at(-1)?.speaker)
        const next = { ...segment, speaker, manual: existing?.manual }
        return existing ? current.map(item => item.id === segment.id ? next : item) : [...current, next]
      }),
      onError: error => {
        setCaptions(false)
        setSpeechError(error === 'permission' ? 'Live captions were blocked by the browser. Saved audio recording is unaffected.' : error === 'network' ? 'Live captions lost their network connection. Saved audio recording is unaffected.' : 'Live captions are unavailable. Saved audio recording is unaffected.')
      },
    })
    return () => live?.stop()
  }, [captions, recording, supported])
  const suggestions = extractSuggestions(segments)
  useEffect(() => {
    if (!automatic) return
    for (const item of extractSuggestions(segments)) {
      const { values, validate, onApply, onAiFieldsChange } = callbacks.current
      if (values[item.field] || protectedFields.current.has(item.field) || applied.current.get(item.field) === item.value || !canAutoFill(item, threshold) || validate?.(item.field, item.value)) continue
      applied.current.set(item.field, item.value)
      onApply?.(item.field, item.value)
      onAiFieldsChange?.([...applied.current.keys()])
    }
  }, [segments, automatic, threshold])
  return <section className="walkin-card voice-assist" aria-labelledby="voice-assist-title">
    <div className="walkin-section-heading"><span><Sparkles size={16} /></span><div><h2 id="voice-assist-title">Voice assist</h2><p>Record the visitor conversation. Optional browser captions can suggest form fields; review them before registering.</p></div></div>
    <div className="voice-simple-recorder"><Mic2 size={30} aria-hidden /><ConversationRecorder customerName={customerName.trim() || 'Walk-in visitor'} customerPhone={customerPhone} customerEmail={customerEmail} title="Walk-in registration" onConversationChange={onConversationChange} onBusyChange={onBusyChange} onCaptureChange={setCapture} chunkSeconds={10} audioBucket="walkin-audio" compact /></div>
    <label><input type="checkbox" checked={captions} disabled={!supported} onChange={event => setCaptions(event.target.checked)} />Live captions</label>
    {!supported && <p>Live captions are not available in this browser. Saved audio still uses the managed transcription service.</p>}
    {capture?.capture === 'paused' && <p>Paused. Live captions resume with recording.</p>}
    <p className="field-hint">Browser captions and text-based speaker guesses are provisional. The saved recording has its own source transcript and English translation.</p>
    {speechError && <p role="alert">{speechError}</p>}{quiet && <p role="alert">Microphone input is very quiet. Move closer and check the selected microphone.</p>}
    <label><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} />Auto-fill blank fields</label>
    <label>Auto-fill confidence threshold<select aria-label="Auto-fill confidence threshold" value={threshold} onChange={event => setThreshold(Number(event.target.value))}>{confidenceOptions.map(value => <option key={value} value={value}>{value}%</option>)}</select></label>
    {segments.map(segment => <div key={segment.id}><label>Speaker label<select aria-label="Speaker label" value={segment.speaker} onChange={event => setSegments(current => current.map(item => item.id === segment.id ? { ...item, speaker: event.target.value as LiveSegment['speaker'], manual: true } : item))}><option>Unknown</option><option>Receptionist</option><option>Visitor</option></select></label><p>{segment.text}{!segment.final && '…'}</p></div>)}
    {suggestions.filter(item => values[item.field] !== item.value || edits[item.field] !== undefined).map(item => {
      const value = edits[item.field] ?? item.value
      const invalid = validate?.(item.field, value)
      return <div key={item.field}><label>Suggested {labels[item.field]}<input value={value} onChange={event => setEdits(current => ({ ...current, [item.field]: event.target.value }))} /></label>{invalid && <p>{invalid}</p>}{item.conflict && <p>This conflicts with something said earlier. Confirm the correct value.</p>}<button type="button" disabled={!!invalid || !value.trim()} onClick={() => { protectedFields.current.add(item.field); onApply?.(item.field, value); setEdits(current => { const next = { ...current }; delete next[item.field]; return next }) }}>{values[item.field] ? `Replace “${values[item.field]}”` : `Use for ${labels[item.field]}`}</button></div>
    })}
  </section>
}
