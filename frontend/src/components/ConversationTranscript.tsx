import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { duration, errorMessageForCode, json, message, request, type Progress, type Transcript, type Turn } from '../services/officeApi'
import { OfficeDialog, OfficeNotice, OfficeStatus, useOfficeResource } from './OfficeUi'
import PageLoader from './PageLoader'
import ProgressivePlayback, { type PlaybackHandle } from './ProgressivePlayback'

const activeJobStates = new Set(['queued', 'retry', 'running', 'processing'])
const finalStates = new Set(['complete', 'completed', 'failed', 'blocked', 'cancelled', 'deleted'])
const translationFailures = new Set(['failed', 'blocked', 'dead', 'uncertain'])

function pending(progress: Progress) {
  if (progress.jobs?.some(job => activeJobStates.has(job.status))) return true
  return !finalStates.has(progress.conversation.finalization_state) && !progress.jobs?.some(job => translationFailures.has(job.status))
}

export function ConversationTranscript({ conversationId, compact = false }: { conversationId: string; compact?: boolean }) {
  const { user, workspace } = useOffice()
  if (!user || !workspace) return <OfficeNotice>Sign in and select a workspace to access this recording.</OfficeNotice>
  // Remount on access-scope changes; no previous customer's transcript or signed URL can remain on screen.
  return <ScopedConversationTranscript key={`${user.id}:${workspace.id}:${workspace.member_id ?? user.id}:${conversationId}`} conversationId={conversationId} compact={compact} />
}

function ScopedConversationTranscript({ conversationId, compact = false }: { conversationId: string; compact?: boolean }) {
  const { workspace, localMode } = useOffice()
  const progress = useOfficeResource<Progress>(`/conversations/${conversationId}/progress`, pending)
  const transcript = useOfficeResource<Transcript>(`/conversations/${conversationId}/transcript`, value => progress.data ? pending(progress.data) : !finalStates.has(value.status || 'pending'))
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [original, setOriginal] = useState(false)
  const [query, setQuery] = useState('')
  const [time, setTime] = useState(-1)
  const [confirmation, setConfirmation] = useState<'translation' | 'all' | 'replay' | null>(null)
  const [assignment, setAssignment] = useState(false)
  const [editing, setEditing] = useState<Turn | null>(null)
  const alive = useRef(true)
  const operations = useRef(new Set<AbortController>())
  const submission = useRef(false)
  const playback = useRef<PlaybackHandle>(null)
  const c = progress.data?.conversation
  const versionId = transcript.data?.version_id || transcript.data?.id || c?.active_transcript_version_id
  const admin = workspace?.role === 'owner'
  const canReplay = admin && !localMode

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      operations.current.forEach(controller => controller.abort())
    }
  }, [])
  useEffect(() => { if (c) transcript.reload() }, [c?.active_transcript_version_id, c?.english_state, c?.speaker_state, c?.finalization_state, transcript.reload])

  function reload() { progress.reload(); transcript.reload() }
  async function mutate(path: string, body: unknown, onSuccess: (result: unknown) => void) {
    if (submission.current || !workspace) return
    submission.current = true
    setBusy(true); setError(''); setNotice('')
    const controller = new AbortController()
    operations.current.add(controller)
    try {
      const result = await request(path, workspace.id, { ...json('POST', body, crypto.randomUUID()), signal: controller.signal }, workspace.member_id)
      if (!alive.current) return
      onSuccess(result); reload()
    } catch (failure) { if (alive.current && !controller.signal.aborted) setError(message(failure)) }
    finally { operations.current.delete(controller); submission.current = false; if (alive.current) setBusy(false) }
  }
  function retry() {
    if (!confirmation) return
    const stage = confirmation
    if (stage === 'replay') {
      void mutate(`/conversations/${conversationId}/runs`, { engine_id: 'elevenlabs', kind: 'replay', idempotency_key: crypto.randomUUID() }, () => {
        setConfirmation(null); setNotice('A separate processing run was queued. Existing audio and transcript history remain available.')
      })
      return
    }
    void mutate(`/conversations/${conversationId}/retry`, { stage }, result => {
      const value = result as { resumed_job_ids?: string[]; uncertain_job_ids?: string[] }
      setConfirmation(null)
      setNotice(value.uncertain_job_ids?.length
        ? 'Some provider submissions have an uncertain outcome. They were not resubmitted; an administrator must reconcile their usage before another paid attempt.'
        : value.resumed_job_ids?.length ? `${value.resumed_job_ids.length} eligible processing job(s) resumed. Follow the actual progress below.` : 'No eligible failed or blocked jobs were resumed. Refresh the status; an active job may already be running.')
    })
  }
  function assign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void mutate(`/conversations/${conversationId}/speakers`, { version_id: versionId, speaker_ref: String(form.get('speaker')), role: String(form.get('role')), name: String(form.get('name')).trim() }, () => { setAssignment(false); setNotice('Speaker assignment saved with an audit record.') })
  }
  function correct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editing) return
    const text = String(new FormData(event.currentTarget).get('text'))
    const field = editing.english_text != null ? 'english_text' : 'source_text'
    void mutate(`/conversations/${conversationId}/revisions`, { version_id: versionId, turn_id: editing.id, [field]: text }, () => { setEditing(null); setNotice('Correction saved as an audited revision. Original machine output is preserved.') })
  }
  const assignments = new Map((transcript.data?.speaker_assignments || []).map(item => [item.speaker_id, item]))
  const speakers = [...new Set((transcript.data?.turns || []).map(turn => turn.speaker_id).filter((id): id is string => !!id))]
  const voices = new Map(speakers.map((id, index) => [id, `Detected voice ${index + 1}`]))
  const turns = (transcript.data?.turns || []).filter(turn => `${turn.english_text || ''} ${turn.source_text || ''}`.toLowerCase().includes(query.toLowerCase()))
  const totalSeconds = typeof c?.total_samples === 'number' ? c.total_samples / 16000 : c?.duration_seconds
  const activeFinalEnglishComplete = c?.finalization_state === 'complete' && c.english_state === 'complete'
    && transcript.data?.status === 'complete' && transcript.data.kind === 'final'
    && !!transcript.data.version_id && transcript.data.version_id === c.active_transcript_version_id
    && typeof totalSeconds === 'number' && Number.isFinite(totalSeconds) && totalSeconds > 0
    && typeof transcript.data.covered_seconds === 'number' && Number.isFinite(transcript.data.covered_seconds)
    && transcript.data.covered_seconds >= totalSeconds
  const translationJobs = progress.data?.jobs?.filter(job => {
    if (job.job_type !== 'translate' || !translationFailures.has(job.status)
      || (transcript.data?.run_id && job.run_id && job.run_id !== transcript.data.run_id)) return false
    // A published final version supersedes failed progressive windows. Keep
    // uncertain submissions and final/unclassified failures visible for review.
    const supersededWindow = activeFinalEnglishComplete && !!transcript.data?.run_id && job.run_id === transcript.data.run_id
      && job.status !== 'uncertain' && 'window_id' in job && typeof job.window_id === 'string' && !!job.window_id
    return !supersededWindow
  }) || []
  const translationAttention = translationJobs.length > 0 || ['failed', 'blocked', 'partial'].includes(c?.english_state || '')
  const speechJobs = progress.data?.jobs?.filter(job => ['transcribe', 'finalise'].includes(job.job_type) && translationFailures.has(job.status)) || []
  const actionableSpeechJobs = activeFinalEnglishComplete ? [] : speechJobs
  const speechMessages = [...new Set(actionableSpeechJobs.map(job => errorMessageForCode(job.error_code || '', 'Contact your administrator if the recording cannot be processed. Saved audio is preserved.')))]
  const processingNeedsAttention = actionableSpeechJobs.length > 0 || translationAttention || !activeFinalEnglishComplete

  return <section className={`office-processing${compact ? ' office-processing-compact' : ''}`} aria-label="Conversation processing">
    {!compact && <div className="office-row office-between"><div><h2>Recording & transcript</h2><p>Review the saved conversation, listen to the recording, and correct any text before sharing it with the customer.</p></div><button type="button" className="office-button office-secondary" disabled={progress.refreshing || transcript.refreshing} onClick={reload}>Refresh status</button></div>}
    {(error || progress.error || transcript.error) && <OfficeNotice error>{error || progress.error || transcript.error}</OfficeNotice>}
    {notice && <OfficeNotice>{notice}</OfficeNotice>}
    {progress.pollPaused && <OfficeNotice>Automatic status checks paused after repeated checks. Refresh status to continue.</OfficeNotice>}
    {progress.loading && <PageLoader kind="connections" label="Checking processing status" detail="Reviewing transcription and translation readiness." compact />}
    {!compact && c && <dl className="office-stages">{[
      ['Recording', c.capture_state], ['Audio saved', c.upload_state], ['Original transcript', c.transcription_state], ['English transcript', c.english_state], ['Speakers identified', c.speaker_state], ['Ready to use', c.finalization_state],
    ].map(([label, value]) => <div className="office-stage" key={label}><dt>{label}</dt><dd><OfficeStatus value={value} /></dd></div>)}</dl>}
    {!!actionableSpeechJobs.length && <OfficeNotice><strong>We couldn’t finish the recording</strong><p>The saved audio is safe. Try processing the recording again, or contact your administrator if the problem continues.</p>{speechMessages.map(text => <p key={text} className="office-small">{text}</p>)}<div className="office-row"><button type="button" className="office-button" disabled={busy} onClick={() => setConfirmation('all')}>Retry audio processing</button></div></OfficeNotice>}
    <section className="office-panel">
      <div className="office-row office-between"><div><h3>Original recording</h3><p className="office-small">Listen here to verify names, numbers, and product details in the conversation.</p></div><button type="button" className="office-button office-secondary" disabled={!c || c.upload_state !== 'saved'} onClick={() => void playback.current?.playAt(0)}>Play recording</button></div>
      {c && workspace && <ProgressivePlayback ref={playback} conversationId={conversationId} workspaceId={workspace.id} memberId={workspace.member_id} onTimeChange={setTime} />}

    </section>
    <section className="office-panel">
      <div className="office-row office-between"><h3>English transcript</h3><OfficeStatus value={transcript.data?.status || c?.english_state} /></div>
      {translationAttention && <OfficeNotice><strong>English transcript needs attention</strong><p>We have kept the original conversation. Try the translation again when the language service is available.</p>{translationJobs.map(job => <p key={job.id} className="office-small">{errorMessageForCode(job.error_code || '', 'The English transcript could not be completed. Check the translation service before retrying.')}</p>)}<div className="office-row"><button type="button" disabled={busy} className="office-button" onClick={() => setConfirmation('translation')}>Retry translation</button></div></OfficeNotice>}
      {transcript.data?.manual_review_required && <OfficeNotice>Machine translation requires review. Check meaning, names, numbers and product codes against the source recording.</OfficeNotice>}
      <div className="office-transcript-tools"><label className="office-field">Find a phrase<input value={query} maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="Search source or English text" /></label><label className="office-checkbox"><input type="checkbox" checked={original} onChange={event => setOriginal(event.target.checked)} /><span>Show original</span></label></div>
      {transcript.loading ? <PageLoader kind="recordings" label="Loading transcript" detail="Preparing the conversation turns for review." compact /> : turns.length ? <ol className="office-turns">{turns.map(turn => {
        const name = turn.speaker_id ? assignments.get(turn.speaker_id)?.name : undefined
        const playing = time >= turn.start_seconds && time < turn.end_seconds
        return <li className={`office-turn${playing ? ' is-playing' : ''}`} key={turn.id}><div className="office-row office-between"><div className="office-row"><strong>{name || (turn.speaker_id ? voices.get(turn.speaker_id) : 'Unresolved speaker')}</strong><span className="office-speaker-tag">{name ? 'Confirmed' : 'Provisional'}</span></div><div className="office-row"><button type="button" className="office-button office-secondary" disabled={c?.upload_state !== 'saved'} aria-label={`Play source audio at ${duration(turn.start_seconds)}`} onClick={() => void playback.current?.playAt(turn.start_seconds)}>{duration(turn.start_seconds)}–{duration(turn.end_seconds)}</button><button type="button" className="office-button office-secondary" disabled={busy || !versionId} onClick={() => { setError(''); setEditing(turn) }}>Correct text</button></div></div>{turn.english_text != null ? <p>{turn.english_text}</p> : <p className="office-small">English unavailable for this turn. Source text is preserved.</p>}{original && <div className="office-source"><small>Original · {turn.source_language || 'detected language'}</small><p>{turn.source_text || 'Original unavailable for this engine pass.'}</p></div>}{turn.is_uncertain && <p className="office-small">Speaker attribution needs review.</p>}</li>
      })}</ol> : <p>{query ? 'No matching turns. Try a different phrase.' : 'The transcript will appear when processing is complete. Saved audio remains available if transcription or translation needs attention.'}</p>}
      <div className="office-row"><button type="button" className="office-button office-secondary" disabled={busy || !versionId || !speakers.length} onClick={() => { setError(''); setAssignment(true) }}>Assign speaker names</button></div>
      <p className="office-small">Detected voices are provisional groups, not verified identities. Listen before assigning a participant name.</p>
    </section>
    {!compact && processingNeedsAttention && <section className="office-panel office-recovery-panel" aria-labelledby="processing-help-title"><div className="office-recovery-heading"><div><span className="office-eyebrow">ACTION NEEDED</span><h3 id="processing-help-title">Fix this recording</h3><p>Your saved audio and current transcript are safe. Use one of these actions only if the recording is incomplete or needs another attempt.</p></div></div><div className="office-recovery-actions">{translationAttention && <button type="button" className="office-button office-secondary" disabled={busy || !c} onClick={() => setConfirmation('translation')}>Retry translation</button>}{actionableSpeechJobs.length > 0 && <button type="button" className="office-button office-secondary" disabled={busy || !c} onClick={() => setConfirmation('all')}>Retry audio processing</button>}{canReplay && <button type="button" className="office-button office-secondary" disabled={busy || c?.upload_state !== 'saved'} onClick={() => setConfirmation('replay')}>Reprocess recording</button>}</div></section>}
    {confirmation && <OfficeDialog title={confirmation === 'replay' ? 'Reprocess this recording?' : confirmation === 'translation' ? 'Retry the English transcript?' : 'Retry recording processing?'} busy={busy} close={() => setConfirmation(null)}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>{confirmation === 'translation' ? 'This retries the English transcript using the saved source text. The original audio is not uploaded again.' : confirmation === 'replay' ? 'This sends the saved audio for a new transcription pass. The previous transcript remains preserved.' : 'This retries the unfinished recording processing steps. Your saved audio and existing transcript remain preserved.'}</p><p>Provider usage may be billed. If a submission has an uncertain outcome, an administrator must reconcile it before another attempt.</p>{confirmation === 'replay' && <p className="office-small">Estimated additional speech processing: approximately {duration(2 * (c?.duration_seconds || (c?.total_samples || 0) / 16000))}, plus overlap or retries. Translation incurs separate token usage.</p>}<button type="button" disabled={busy} className="office-button" onClick={retry}>{busy ? 'Submitting…' : confirmation === 'replay' ? 'Confirm reprocessing' : 'Confirm and continue'}</button></OfficeDialog>}
    {assignment && <OfficeDialog title="Confirm a speaker name" busy={busy} close={() => setAssignment(false)}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>Assignments apply to this transcript version. A voice group in another run does not identify the same person.</p><form onSubmit={assign}><label className="office-field">Detected voice<select name="speaker" required>{speakers.map(id => <option key={id} value={id}>{assignments.get(id)?.name || voices.get(id)}</option>)}</select></label><label className="office-field">Participant role<select name="role"><option value="customer">Customer</option><option value="salesperson">Salesperson</option><option value="other">Other participant</option></select></label><label className="office-field">Confirmed participant name<input name="name" required minLength={1} maxLength={120} /></label><button className="office-button" disabled={busy}>Save audited assignment</button></form></OfficeDialog>}
    {editing && <OfficeDialog title="Correct a transcript turn" busy={busy} close={() => setEditing(null)}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>Corrections create an audited revision. The original machine output remains preserved.</p><form onSubmit={correct}><label className="office-field">{editing.english_text != null ? 'English text' : 'Original source text'}<textarea name="text" required rows={6} maxLength={20000} defaultValue={editing.english_text ?? editing.source_text ?? ''} /></label><button className="office-button" disabled={busy}>Save correction</button></form></OfficeDialog>}
  </section>
}

export default ConversationTranscript
