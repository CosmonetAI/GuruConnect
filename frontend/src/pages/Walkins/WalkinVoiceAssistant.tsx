import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Eye, EyeOff, Mic2, UserRound } from 'lucide-react'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import { useOffice } from '../../contexts/OfficeContext'
import { errorMessageForCode, json, message, request, type Assignment, type Progress, type Transcript, type Turn } from '../../services/officeApi'
import { useOfficeResource } from '../../components/OfficeUi'
import { extractVisitorFields, VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD, type VisitorField, type VisitorFieldSuggestion } from './visitorExtraction'
import './Walkins.css'

const finalStates = new Set(['complete', 'completed', 'failed', 'blocked', 'cancelled', 'deleted'])

function processingPending(value: Progress) {
  if (value.jobs?.some(job => ['queued', 'retry', 'running', 'processing'].includes(job.status))) return true
  return !finalStates.has(value.conversation.finalization_state)
}

function transcriptPending(value: Transcript) {
  return !finalStates.has(value.status || 'pending') || ['provisional', 'review'].includes(value.speaker_state || '')
}

function displayTime(seconds: number) {
  const value = Math.max(0, Math.floor(seconds))
  return `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`
}

function speakerTitle(id: string, speakers: string[], assignments: Assignment[]) {
  const assignment = assignments.find(item => item.speaker_id === id)
  if (assignment?.role === 'customer') return 'Visitor'
  if (assignment?.role === 'salesperson') return 'Receptionist'
  if (assignment?.role === 'other' || assignment?.role === 'unknown') return 'Unknown speaker'
  const index = speakers.indexOf(id)
  return index >= 0 ? `Detected voice ${index + 1} · unconfirmed` : 'Unknown speaker'
}

export default function WalkinVoiceAssistant({
  customerName,
  onConversationChange,
  onBusyChange,
  onSuggestions,
  onAcceptSuggestion,
}: {
  customerName: string
  onConversationChange: (id: string | null) => void
  onBusyChange: (busy: boolean) => void
  onSuggestions: (suggestions: Partial<Record<VisitorField, VisitorFieldSuggestion>>) => void
  onAcceptSuggestion: (field: VisitorField, suggestion: VisitorFieldSuggestion) => void
}) {
  const { workspace, localMode } = useOffice()
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [assignmentBusy, setAssignmentBusy] = useState(false)
  const [error, setError] = useState('')
  const [transcriptHidden, setTranscriptHidden] = useState(false)
  const [capture, setCapture] = useState({ capture: 'idle', elapsedSeconds: 0, level: 0 })
  const [volumeWarning, setVolumeWarning] = useState(false)
  const captureRef = useRef(capture)
  const lowSince = useRef<number | null>(null)
  captureRef.current = capture

  const progress = useOfficeResource<Progress>(
    conversationId ? `/conversations/${encodeURIComponent(conversationId)}/progress` : null,
    processingPending,
  )
  const transcript = useOfficeResource<Transcript>(
    conversationId ? `/conversations/${encodeURIComponent(conversationId)}/transcript` : null,
    transcriptPending,
  )

  useEffect(() => {
    transcript.reload()
  }, [progress.data?.conversation.active_transcript_version_id, transcript.reload])

  useEffect(() => {
    if (capture.capture !== 'recording') {
      lowSince.current = null
      setVolumeWarning(false)
      return
    }
    const timer = window.setInterval(() => {
      const current = captureRef.current
      if (current.capture !== 'recording') return
      if (current.level < 0.008) {
        lowSince.current ??= Date.now()
        if (Date.now() - lowSince.current >= 8000) setVolumeWarning(true)
      } else {
        lowSince.current = null
        setVolumeWarning(false)
      }
    }, 500)
    return () => window.clearInterval(timer)
  }, [capture.capture])

  const turns = transcript.data?.turns ?? []
  const assignments = transcript.data?.speaker_assignments ?? []
  const speakers = useMemo(() => [...new Set(turns.map(turn => turn.speaker_id).filter((id): id is string => !!id))], [turns])
  const suggestions = useMemo(() => extractVisitorFields(turns, assignments), [turns, assignments])
  const latestSuggestionCallback = useRef(onSuggestions)
  latestSuggestionCallback.current = onSuggestions
  useEffect(() => {
    latestSuggestionCallback.current(transcriptHidden ? {} : suggestions)
  }, [suggestions, transcriptHidden])

  // Auto-assignment state & refs: perform a conservative auto-assign when speaker-scoped suggestions
  // indicate one speaker is very likely the visitor. Provide an undo affordance.
  const autoAssignedRef = useRef(false)
  const autoAssignTimer = useRef<number | null>(null)
  const [undoVisible, setUndoVisible] = useState(false)
  const lastAutoAssigned = useRef<{customer?: string; receptionist?: string} | null>(null)

  // Evaluate automatic assignment eligibility when suggestions or assignments update.
  useEffect(() => {
    // Do not auto-assign if we've already done so for this conversation or if a customer role already exists
    if (autoAssignedRef.current) return
    if (assignments.some(a => a.role === 'customer')) return
    if (!speakers.length || Object.keys(suggestions).length === 0) return

    // Map turn id -> speaker id for quick lookup
    const turnSpeaker = new Map(turns.map(t => [t.id, t.speaker_id]))

    // Score speakers by suggestions attributed to turns from them
    const speakerScores: Record<string, {sum: number; count: number; max: number}> = {}
    for (const [, suggestion] of Object.entries(suggestions)) {
      const speakerId = turnSpeaker.get(suggestion.sourceTurnId)
      if (!speakerId) continue
      const entry = speakerScores[speakerId] ??= { sum: 0, count: 0, max: 0 }
      entry.sum += suggestion.confidence
      entry.count += 1
      entry.max = Math.max(entry.max, suggestion.confidence)
    }

    const scored = Object.entries(speakerScores)
    if (!scored.length) return
    scored.sort((a, b) => b[1].sum - a[1].sum)
    const [bestSpeaker, bestScore] = scored[0]
    const avgConfidence = bestScore.sum / Math.max(1, bestScore.count)

    // Conservative thresholds for auto-assignment
    const AVG_CONF_THRESHOLD = 0.9
    const MAX_CONF_THRESHOLD = 0.95
    const SUM_CONF_THRESHOLD = 1.8 // approx two strong fields
    const shouldAuto = avgConfidence >= AVG_CONF_THRESHOLD || bestScore.max >= MAX_CONF_THRESHOLD || bestScore.sum >= SUM_CONF_THRESHOLD
    if (!shouldAuto) return

    // Stabilization window: wait a few seconds to avoid transient misattributions
    if (autoAssignTimer.current) window.clearTimeout(autoAssignTimer.current)
    autoAssignTimer.current = window.setTimeout(async () => {
      // Double-check conditions again in case assignments changed during the wait
      if (autoAssignedRef.current) return
      if (assignments.some(a => a.role === 'customer')) return
      // Find a receptionist candidate (any other speaker)
      const receptionist = speakers.find(s => s !== bestSpeaker) ?? speakers[0]
      lastAutoAssigned.current = { customer: bestSpeaker, receptionist }
      try {
        // assign visitor then receptionist. assignSpeaker handles in-flight checks and reloads.
        await assignSpeaker(bestSpeaker, 'customer')
        // give a small gap in case backend ordering matters
        await new Promise(resolve => setTimeout(resolve, 150))
        await assignSpeaker(receptionist, 'salesperson')
        autoAssignedRef.current = true
        setUndoVisible(true)
      } catch {
        // ignore; assignSpeaker sets errors
      }
    }, 3500)
    return () => {
      if (autoAssignTimer.current) window.clearTimeout(autoAssignTimer.current)
      autoAssignTimer.current = null
    }
  }, [suggestions, assignments, speakers, turns, assignSpeaker])

  const updateConversation = useCallback((id: string | null) => {
    setConversationId(id)
    setTranscriptHidden(false)
    setError('')
    onConversationChange(id)
  }, [onConversationChange])

  const updateCapture = useCallback((state: { capture: string; elapsedSeconds: number; level: number }) => {
    setCapture(state)
  }, [])

  async function assignSpeaker(speakerId: string, role: 'customer' | 'salesperson' | 'unknown') {
    if (!workspace || !conversationId || !transcript.data?.version_id || assignmentBusy) return
    const name = role === 'customer' ? 'Visitor' : role === 'salesperson' ? 'Receptionist' : 'Unknown speaker'
    setAssignmentBusy(true)
    setError('')
    try {
      await request(`/conversations/${encodeURIComponent(conversationId)}/speakers`, workspace.id, json('POST', {
        version_id: transcript.data.version_id,
        speaker_ref: speakerId,
        role,
        name,
      }, crypto.randomUUID()))
      transcript.reload()
    } catch (reason) {
      setError(message(reason))
    } finally {
      setAssignmentBusy(false)
    }
  }

  const failedSpeech = progress.data?.jobs?.find(job =>
    ['transcribe', 'diarize', 'assemble', 'finalise'].includes(job.job_type)
    && ['failed', 'blocked', 'dead', 'uncertain'].includes(job.status),
  )
  const live = capture.capture === 'recording'
  const paused = capture.capture === 'paused'
  const noTranscript = !turns.length && transcript.data?.status && finalStates.has(transcript.data.status)

  return (
    <section className="walkin-card walkin-recording-card" aria-labelledby="walkin-voice-heading">
      <div className="walkin-card-heading">
        <div>
          <h2 id="walkin-voice-heading">Voice assistant</h2>
          <p>{localMode ? 'Local demo audio is stored on this device and sent to the configured speech provider for processing.' : 'Audio is stored in the authenticated workspace and sent to its configured speech provider for processing.'} Transcript segments arrive as audio chunks are processed. Get both participants’ consent. When the assistant is very confident it may automatically identify the visitor and receptionist and apply detected fields to the form; an undo option is displayed so you can revert the automatic assignment.</p>
        </div>
        <span className="walkin-card-icon"><Mic2 size={18} /></span>
      </div>
      <ConversationRecorder
        customerName={customerName.trim() || 'Walk-in visitor'}
        title="Visitor registration"
        initialConversationId={conversationId}
        onConversationChange={updateConversation}
        onBusyChange={onBusyChange}
        onCaptureState={updateCapture}
      />
      <div className="walkin-live-volume" aria-live="polite">
        <div className="walkin-recording-status">
          <span className={`walkin-recording-indicator${live ? ' is-live' : ''}`} />
          <strong>{live ? 'Recording' : paused ? 'Paused' : capture.capture === 'stopped' ? 'Completed' : 'Ready'}</strong>
          <span className="walkin-recording-timer">{displayTime(capture.elapsedSeconds)}</span>
        </div>
        {live && <div className={`walkin-volume-meter${volumeWarning ? ' meter-low' : ''}`}>
          <div className="walkin-volume-meter-heading"><span>Microphone level</span><span>{Math.round(Math.min(1, capture.level * 6) * 100)}%</span></div>
          <div className="walkin-volume-track" role="meter" aria-label="Microphone input level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(1, capture.level * 6) * 100)}>
            <span style={{ width: `${Math.round(Math.min(1, capture.level * 6) * 100)}%` }} />
          </div>
          {volumeWarning && <p className="volume-warning">Your voice is too low. Please speak louder or move closer to the microphone.</p>}
        </div>}
      </div>

      {error && <div className="walkin-alert error" role="alert">{error}</div>}
      {(progress.error || transcript.error) && <div className="walkin-alert error" role="alert">{progress.error || transcript.error}</div>}
      {failedSpeech && <div className="walkin-alert error" role="alert">{errorMessageForCode(failedSpeech.error_code || '', 'Speech transcription or speaker detection failed. Continue by entering visitor details manually.')}</div>}
      {conversationId && <section className="walkin-transcript" aria-label="Live conversation transcript">
        <div className="walkin-transcript-heading">
          <strong>Live conversation transcript</strong>
          <span className={live ? 'walkin-transcript-live' : ''}>{live && <span />} {live ? 'LIVE' : transcript.refreshing ? 'UPDATING' : 'WORKSPACE TRANSCRIPT'}</span>
        </div>
        {progress.data?.conversation && <p className="walkin-field-hint">
          Speech: {progress.data.conversation.transcription_state.replaceAll('_', ' ')} · Speakers: {progress.data.conversation.speaker_state.replaceAll('_', ' ')}
        </p>}
        {speakers.length > 0 && <div className="walkin-speaker-assignments">
          {speakers.map((speaker, index) => {
            const assigned = assignments.find(item => item.speaker_id === speaker)
            return <label key={speaker}>
              <span><UserRound size={14} /> Detected voice {index + 1}</span>
              <select aria-label={`Assign detected voice ${index + 1}`} disabled={assignmentBusy || !transcript.data?.version_id} value={assigned?.role === 'customer' || assigned?.role === 'salesperson' || assigned?.role === 'unknown' ? assigned.role : assigned ? 'unknown' : ''} onChange={event => {
                if (event.target.value) void assignSpeaker(speaker, event.target.value as 'customer' | 'salesperson' | 'unknown')
              }}>
                <option value="">Confirm speaker…</option>
                <option value="customer">Visitor</option>
                <option value="salesperson">Receptionist</option>
                <option value="unknown">Unknown speaker</option>
              </select>
              {assigned && <Check size={14} aria-label="Speaker role confirmed" />}
            </label>
          })}
        </div>}
        <div className="walkin-live-transcript">
          {transcriptHidden
            ? <p className="walkin-transcript-empty">Transcript hidden from this form. Saved audio and transcript remain in the workspace.</p>
            : turns.length
              ? <ol className="walkin-transcript-turns">{turns.map((turn: Turn) => <li key={turn.id}>
                <div><strong>{turn.speaker_id ? speakerTitle(turn.speaker_id, speakers, assignments) : 'Unknown speaker'}</strong><time>{displayTime(turn.start_seconds)}</time></div>
                <p>{turn.english_text || turn.source_text || 'Speech not recognized.'}</p>
                {turn.is_uncertain && <small>Speaker attribution is uncertain. Review this turn before using it.</small>}
              </li>)}</ol>
              : noTranscript
                ? <p className="walkin-transcript-empty">No speech was recognized. Check the microphone or enter visitor details manually.</p>
                : <p className="walkin-transcript-empty">{progress.data?.conversation.transcription_state === 'failed' ? 'Transcription is unavailable. Enter visitor details manually.' : 'Transcript segments appear here as the workspace speech service processes the recording.'}</p>}
        </div>
        <div className="walkin-transcript-actions">
          <button type="button" className="walkin-button secondary" onClick={() => setTranscriptHidden(value => !value)}>{transcriptHidden ? <><Eye size={15} /> Show transcript</> : <><EyeOff size={15} /> Clear transcript view</>}</button>
          {transcriptHidden && <span className="walkin-field-hint">This hides the transcript only. It does not delete workspace audio or transcript data.</span>}
        </div>
        {/* Undo affordance for automatic assignment */}
        {undoVisible && lastAutoAssigned.current && <div className="walkin-autoassign-notice">
          <p>Visitor and receptionist voices were identified automatically. <button type="button" className="walkin-button secondary" onClick={async () => {
            setUndoVisible(false)
            autoAssignedRef.current = false
            const customer = lastAutoAssigned.current?.customer
            const receptionist = lastAutoAssigned.current?.receptionist
            if (customer) await assignSpeaker(customer, 'unknown')
            if (receptionist) await assignSpeaker(receptionist, 'unknown')
            lastAutoAssigned.current = null
          }}>Undo</button></p>
        </div>}
        {!transcriptHidden && <section className="walkin-extraction-suggestions">
          <h3>Visitor details detected</h3>
          {Object.entries(suggestions).length
            ? Object.entries(suggestions).map(([field, suggestion]) => {
              const value = suggestion!
              return <div className="walkin-extraction-suggestion" key={field}>
                <div><strong>{field.replaceAll(/([A-Z])/g, ' $1').replace(/^./, char => char.toUpperCase())}</strong><span>{value.conflict ? 'Conflicting values — review required' : `${Math.round(value.confidence * 100)}% confidence${value.confidence >= VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD ? ' · suggested' : ' · confirm manually'}`}</span><p>{value.conflict ? value.alternatives.join(' / ') : value.value}</p><small>Source: “{value.sourceText}”</small></div>
                {(value.conflict || value.confidence < VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD) && <button type="button" className="walkin-button secondary" onClick={() => onAcceptSuggestion(field as VisitorField, value)}>Use suggestion</button>}
              </div>
            })
            : <p className="walkin-field-hint">Confirm a detected voice as the visitor to extract fields. Receptionist turns are not used for visitor details.</p>}
        </section>}
      </section>}
      <p className="walkin-recording-footnote">Diarization runs through the connected backend speech provider. Detected voices are provisional, and the receptionist must confirm who is the visitor. If recording or processing is unavailable, complete the form manually.</p>
    </section>
  )
}
