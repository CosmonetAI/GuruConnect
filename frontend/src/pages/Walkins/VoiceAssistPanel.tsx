import { useEffect, useRef, useState } from 'react'
import { Mic2, Sparkles } from 'lucide-react'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import { canAutoFill, defaultConfidencePercent, extractSuggestions, type VisitorField } from '../../voice/extract'
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
  liveCaptionsDefault?: boolean
}
const blank = { customerName: '', mobileNumber: '', email: '', companyName: '' }

export default function VoiceAssistPanel({ customerName, customerPhone, customerEmail, onConversationChange, onBusyChange, values = blank, validate, onApply, onAiFieldsChange, liveCaptionsDefault = true }: VoiceAssistPanelProps) {
  const [capture, setCapture] = useState<CaptureState | null>(null)
  const captions = liveCaptionsDefault
  const automatic = true
  const threshold = defaultConfidencePercent
  const [segments, setSegments] = useState<LiveSegment[]>([])
  const [speechError, setSpeechError] = useState('')
  const [quiet, setQuiet] = useState(false)
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
        setSpeechError(error === 'permission' ? 'Live captions were blocked by the browser. Saved audio recording is unaffected.' : error === 'network' ? 'Live captions lost their network connection. Saved audio recording is unaffected.' : 'Live captions are unavailable. Saved audio recording is unaffected.')
      },
    })
    return () => live?.stop()
  }, [captions, recording, supported])
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
  return <section className="voice-assist" aria-labelledby="voice-assist-title">
    <div className="walkin-section-heading"><span><Sparkles size={16} /></span><div><h2 id="voice-assist-title">Voice registration</h2><p>Speak naturally. Visitor details are filled in automatically.</p></div></div>
    <div className="voice-simple-recorder"><Mic2 size={30} aria-hidden /><ConversationRecorder customerName={customerName.trim() || 'Walk-in visitor'} customerPhone={customerPhone} customerEmail={customerEmail} title="Walk-in registration" onConversationChange={onConversationChange} onBusyChange={onBusyChange} onCaptureChange={setCapture} chunkSeconds={5} audioBucket="walkin-audio" compact registrationOnly /></div>
    {supported && segments.length > 0 && <div className="voice-transcript" aria-live="polite">{segments.map(segment => <p key={segment.id}><strong>{segment.speaker}:</strong> {segment.text}{!segment.final && '…'}</p>)}</div>}
    {speechError && <p role="alert">{speechError}</p>}{quiet && <p role="alert">Microphone input is very quiet. Move closer and check the selected microphone.</p>}
  </section>
}
