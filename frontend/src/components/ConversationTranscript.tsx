import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useOffice } from '../contexts/OfficeContext'
import { ApiError, duration, errorMessageForCode, json, message, request, type Progress, type Transcript, type Turn } from '../services/officeApi'
import { OfficeDialog, OfficeNotice, OfficeStatus, useOfficeResource } from './OfficeUi'
import PageLoader from './PageLoader'

const activeJobStates = new Set(['queued', 'retry', 'running', 'processing'])
const finalStates = new Set(['complete', 'completed', 'failed', 'blocked', 'cancelled', 'deleted'])
const translationFailures = new Set(['failed', 'blocked', 'dead', 'uncertain'])

function pending(progress: Progress) {
  if (progress.jobs?.some(job => activeJobStates.has(job.status))) return true
  return !finalStates.has(progress.conversation.finalization_state) && !progress.jobs?.some(job => translationFailures.has(job.status))
}

export function ConversationTranscript({ conversationId }: { conversationId: string }) {
  const { user, workspace } = useOffice()
  if (!user || !workspace) return <OfficeNotice>Sign in and select a workspace to access this recording.</OfficeNotice>
  // Remount on access-scope changes; no previous customer's transcript or signed URL can remain on screen.
  return <ScopedConversationTranscript key={`${user.id}:${workspace.id}:${conversationId}`} conversationId={conversationId} />
}

function ScopedConversationTranscript({ conversationId }: { conversationId: string }) {
  const { workspace, localMode } = useOffice()
  const progress = useOfficeResource<Progress>(`/conversations/${conversationId}/progress`, pending)
  const transcript = useOfficeResource<Transcript>(`/conversations/${conversationId}/transcript`, value => progress.data ? pending(progress.data) : !finalStates.has(value.status || 'pending'))
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [original, setOriginal] = useState(false)
  const [query, setQuery] = useState('')
  const [time, setTime] = useState(-1)
  const [url, setUrl] = useState('')
  const [playbackLoading, setPlaybackLoading] = useState(false)
  const [playbackPending, setPlaybackPending] = useState(false)
  const [confirmation, setConfirmation] = useState<'translation' | 'all' | 'replay' | null>(null)
  const [assignment, setAssignment] = useState(false)
  const [editing, setEditing] = useState<Turn | null>(null)
  const audio = useRef<HTMLAudioElement>(null)
  const alive = useRef(true)
  const operations = useRef(new Set<AbortController>())
  const submission = useRef(false)
  const playbackSubmission = useRef(false)
  const expiry = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const c = progress.data?.conversation
  const versionId = transcript.data?.version_id || transcript.data?.id || c?.active_transcript_version_id
  const admin = workspace?.role === 'owner'
  const canReplay = admin && !localMode

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      operations.current.forEach(controller => controller.abort())
      clearTimeout(expiry.current)
      audio.current?.pause()
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
      const result = await request(path, workspace.id, { ...json('POST', body, crypto.randomUUID()), signal: controller.signal })
      if (!alive.current) return
      onSuccess(result); reload()
    } catch (failure) { if (alive.current && !controller.signal.aborted) setError(message(failure)) }
    finally { operations.current.delete(controller); submission.current = false; if (alive.current) setBusy(false) }
  }
  async function loadPlayback() {
    if (!workspace || playbackSubmission.current) return
    playbackSubmission.current = true
    setPlaybackLoading(true); setError(''); setPlaybackPending(false)
    const controller = new AbortController()
    operations.current.add(controller)
    try {
      for (let attempt = 0; attempt < 6; attempt += 1) {
        try {
          const result = await request<{ url: string; expires_in: number }>(`/conversations/${conversationId}/playback`, workspace.id, { signal: controller.signal })
          if (!alive.current) return
          const parsed = new URL(result.url)
          if (!['https:', 'http:'].includes(parsed.protocol)) throw new Error('The service returned an invalid playback link.')
          clearTimeout(expiry.current)
          setUrl(result.url)
          setPlaybackPending(false)
          expiry.current = setTimeout(() => {
            audio.current?.pause(); setUrl(''); setTime(-1)
            setNotice('Playback access expired. Load a fresh authorised link to continue.')
          }, Math.max(1, Math.min(result.expires_in || 60, 3600)) * 1000)
          return
        } catch (failure) {
          if (!(failure instanceof ApiError) || failure.code !== 'playback_assembly_pending' || attempt === 5) throw failure
          if (alive.current) setPlaybackPending(true)
          await new Promise<void>(resolve => setTimeout(resolve, 1500))
        }
      }
    } catch (failure) {
      if (!alive.current || controller.signal.aborted) return
      if (failure instanceof ApiError && failure.code === 'playback_assembly_pending') setPlaybackPending(true)
      else setError(message(failure))
    } finally { operations.current.delete(controller); playbackSubmission.current = false; if (alive.current) setPlaybackLoading(false) }
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
  const translationJobs = progress.data?.jobs?.filter(job => job.job_type === 'translate' && translationFailures.has(job.status) && (!transcript.data?.run_id || !job.run_id || job.run_id === transcript.data.run_id)) || []
  const translationAttention = translationJobs.length > 0 || ['failed', 'blocked', 'partial'].includes(c?.english_state || '')
  const speechJobs = progress.data?.jobs?.filter(job => ['transcribe', 'finalise'].includes(job.job_type) && translationFailures.has(job.status)) || []

  return <section className="office-processing" aria-label="Conversation processing">
    <div className="office-row office-between"><div><h2>Recording & transcript</h2><p>Private audio → ElevenLabs source transcript and speakers → OpenAI English translation.</p></div><button type="button" className="office-button office-secondary" disabled={progress.refreshing || transcript.refreshing} onClick={reload}>Refresh status</button></div>
    {(error || progress.error || transcript.error) && <OfficeNotice error>{error || progress.error || transcript.error}</OfficeNotice>}
    {notice && <OfficeNotice>{notice}</OfficeNotice>}
    {progress.pollPaused && <OfficeNotice>Automatic status checks paused after repeated checks. Refresh status to continue.</OfficeNotice>}
    {progress.loading && <PageLoader kind="connections" label="Checking processing status" detail="Reviewing transcription and translation readiness." compact />}
    {c && <dl className="office-stages">{[
      ['Capture', c.capture_state], ['Audio saved', c.upload_state], ['Source transcript', c.transcription_state], ['English transcript', c.english_state], ['Speakers', c.speaker_state], ['Final processing', c.finalization_state],
    ].map(([label, value]) => <div className="office-stage" key={label}><dt>{label}</dt><dd><OfficeStatus value={value} /></dd></div>)}</dl>}
    {!!speechJobs.length && <OfficeNotice><strong>Speech transcription needs attention</strong>{speechJobs.map(job => <p key={job.id}>{errorMessageForCode(job.error_code || '', 'Check your transcription connection and retry the failed stage. Saved audio is preserved.')}</p>)}<div className="office-row"><Link className="office-button office-secondary" to="/integrations">Check transcription connection</Link><button type="button" className="office-button" disabled={busy} onClick={() => setConfirmation('all')}>Retry failed stages</button></div></OfficeNotice>}
    <section className="office-panel">
      <div className="office-row office-between"><div><h3>Source audio</h3><p className="office-small">Timing refers to original speech intervals. Translated words have no invented exact alignment.</p></div>{!url && <button type="button" className="office-button office-secondary" disabled={playbackLoading || !c} onClick={() => void loadPlayback()}>{playbackLoading ? 'Preparing playback…' : 'Load playback'}</button>}</div>
      {playbackPending && <OfficeNotice>{c?.upload_state === 'saved' ? 'Playback assembly is pending. Your audio is saved while we assemble the complete recording from its verified chunks.' : 'Playback becomes available after the recording stops and all audio is saved.'}</OfficeNotice>}
      {url && <audio ref={audio} controls preload="metadata" src={url} aria-label="Conversation source audio" onTimeUpdate={event => setTime(event.currentTarget.currentTime)} onError={() => { clearTimeout(expiry.current); setUrl(''); setTime(-1); setError('Playback access may have expired. Load a fresh authorised link.') }} />}
    </section>
    <section className="office-panel">
      <div className="office-row office-between"><h3>English transcript</h3><OfficeStatus value={transcript.data?.status || c?.english_state} /></div>
      {translationAttention && <OfficeNotice><strong>English translation needs attention</strong><p>Source text and saved audio are preserved. Reconnecting the translation service does not automatically retry a previously blocked recording.</p>{translationJobs.map(job => <p key={job.id} className="office-small">{errorMessageForCode(job.error_code || '', 'This translation job is blocked. Check the translation service and usage limits before retrying.')}</p>)}<div className="office-row"><button type="button" disabled={busy} className="office-button" onClick={() => setConfirmation('translation')}>Retry English translation</button>{admin && <Link className="office-button office-secondary" to="/integrations">Check translation connection</Link>}</div></OfficeNotice>}
      {transcript.data?.manual_review_required && <OfficeNotice>Machine translation requires review. Check meaning, names, numbers and product codes against the source recording.</OfficeNotice>}
      <div className="office-row office-between"><label className="office-field" style={{ flex: '1 1 220px' }}>Find a phrase<input value={query} maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="Search source or English text" /></label><label className="office-checkbox"><input type="checkbox" checked={original} onChange={event => setOriginal(event.target.checked)} />Show original</label></div>
      {transcript.loading ? <PageLoader kind="recordings" label="Loading transcript" detail="Preparing the conversation turns for review." compact /> : turns.length ? <ol className="office-turns">{turns.map(turn => {
        const name = turn.speaker_id ? assignments.get(turn.speaker_id)?.name : undefined
        const playing = time >= turn.start_seconds && time < turn.end_seconds
        return <li className={`office-turn${playing ? ' is-playing' : ''}`} key={turn.id}><div className="office-row office-between"><div className="office-row"><strong>{name || (turn.speaker_id ? voices.get(turn.speaker_id) : 'Unresolved speaker')}</strong><span className="office-speaker-tag">{name ? 'Confirmed' : 'Provisional'}</span></div><div className="office-row"><button type="button" className="office-button office-secondary" disabled={!url} aria-label={`Play source audio at ${duration(turn.start_seconds)}`} onClick={() => { if (audio.current) { audio.current.currentTime = turn.start_seconds; void audio.current.play().catch(() => setError('Playback was blocked by your browser. Use the audio player controls.')) } }}>{duration(turn.start_seconds)}–{duration(turn.end_seconds)}</button><button type="button" className="office-button office-secondary" disabled={busy || !versionId} onClick={() => { setError(''); setEditing(turn) }}>Correct text</button></div></div>{turn.english_text != null ? <p>{turn.english_text}</p> : <p className="office-small">English unavailable for this turn. Source text is preserved.</p>}{original && <div className="office-source"><small>Original · {turn.source_language || 'detected language'}</small><p>{turn.source_text || 'Original unavailable for this engine pass.'}</p></div>}{turn.is_uncertain && <p className="office-small">Speaker attribution needs review.</p>}</li>
      })}</ol> : <p>{query ? 'No matching turns. Try a different phrase.' : 'The transcript will appear when processing is complete. Saved audio remains available if transcription or translation needs attention.'}</p>}
      <div className="office-row"><button type="button" className="office-button office-secondary" disabled={busy || !versionId || !speakers.length} onClick={() => { setError(''); setAssignment(true) }}>Assign speaker names</button></div>
      <p className="office-small">Detected voices are provisional groups, not verified identities. Listen before assigning a participant name.</p>
    </section>
    <section className="office-panel"><h3>Processing recovery</h3><p>Retry retained source text without resubmitting speech audio. A new speech pass is an explicit additional run.</p><div className="office-row"><button type="button" className="office-button office-secondary" disabled={busy || !c} onClick={() => setConfirmation('translation')}>Retry English translation</button><button type="button" className="office-button office-secondary" disabled={busy || !c} onClick={() => setConfirmation('all')}>Retry failed stages</button>{canReplay && <button type="button" className="office-button office-secondary" disabled={busy || c?.upload_state !== 'saved'} onClick={() => setConfirmation('replay')}>Create another speech pass</button>}</div>{!!progress.data?.jobs?.length && <ul className="office-pipeline">{progress.data.jobs.map(job => <li key={job.id}>{job.job_type.replaceAll('_', ' ')}: <OfficeStatus value={job.status} />{job.error_code && <span> · {errorMessageForCode(job.error_code, 'Stage needs attention. Check the worker, provider connection and usage limits.')}</span>}</li>)}</ul>}</section>
    {confirmation && <OfficeDialog title={confirmation === 'replay' ? 'Confirm additional speech processing' : confirmation === 'translation' ? 'Retry English translation?' : 'Retry failed processing stages?'} busy={busy} close={() => setConfirmation(null)}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>{confirmation === 'translation' ? 'This retries eligible failed or blocked translations from retained source text. Speech audio is not submitted again. English translation uses the currently selected workspace connection.' : confirmation === 'replay' ? 'This sends the saved original audio to ElevenLabs for another processing run. Previous audio and transcript versions remain preserved; this does not silently replace a completed result.' : 'This resumes eligible failed or blocked jobs. Depending on the stage, audio transcription and translation may run again.'}</p><p>Provider usage may be billed. Submissions with an uncertain outcome require administrator reconciliation and will not be retried automatically.</p>{confirmation === 'replay' && <p className="office-small">Estimated additional speech processing: approximately {duration(2 * (c?.duration_seconds || (c?.total_samples || 0) / 16000))}, plus overlap or retries. Translation incurs separate token usage.</p>}<button type="button" disabled={busy} className="office-button" onClick={retry}>{busy ? 'Submitting…' : confirmation === 'replay' ? 'Confirm another paid speech pass' : 'Confirm retry and provider usage'}</button></OfficeDialog>}
    {assignment && <OfficeDialog title="Confirm a speaker name" busy={busy} close={() => setAssignment(false)}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>Assignments apply to this transcript version. A voice group in another run does not identify the same person.</p><form onSubmit={assign}><label className="office-field">Detected voice<select name="speaker" required>{speakers.map(id => <option key={id} value={id}>{assignments.get(id)?.name || voices.get(id)}</option>)}</select></label><label className="office-field">Participant role<select name="role"><option value="customer">Customer</option><option value="salesperson">Salesperson</option><option value="other">Other participant</option></select></label><label className="office-field">Confirmed participant name<input name="name" required minLength={1} maxLength={120} /></label><button className="office-button" disabled={busy}>Save audited assignment</button></form></OfficeDialog>}
    {editing && <OfficeDialog title="Correct a transcript turn" busy={busy} close={() => setEditing(null)}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>Corrections create an audited revision. The original machine output remains preserved.</p><form onSubmit={correct}><label className="office-field">{editing.english_text != null ? 'English text' : 'Original source text'}<textarea name="text" required rows={6} maxLength={20000} defaultValue={editing.english_text ?? editing.source_text ?? ''} /></label><button className="office-button" disabled={busy}>Save correction</button></form></OfficeDialog>}
  </section>
}

export default ConversationTranscript
