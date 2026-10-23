import { useEffect, useRef, useState } from 'react'
import { Check, FileAudio2, Mic2, ShieldCheck, Square, Upload } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { ApiError, duration, json, localMode, message, request, type Connection, type Conversation, type EngineSettings, type Page } from '../../services/officeApi'
import { recordingHooks } from '../../recording/apiHooks'
import { listRecoveries, RecorderSession, type CaptureState } from '../../recording/session'
import type { Recovery } from '../../recording/outbox'
import { AudioUploadError, MAX_AUDIO_FILE_BYTES, prepareAudioFile, uploadAudioFile, type UploadProgress } from '../../recording/upload'
import '../../styles/recording.css'

let persistentRecorder: RecorderSession | null = null
let persistentConversationId: string | null = null

export interface ConversationRecorderProps {
  customerName: string
  customerPhone?: string
  customerEmail?: string
  title?: string
  initialConversationId?: string | null
  onConversationChange: (id: string | null) => void
  onBusyChange?: (busy: boolean) => void
  onSaved?: (id: string) => void
  onCaptureChange?: (state: CaptureState) => void
  chunkSeconds?: number
  audioBucket?: 'session-audio' | 'walkin-audio'
}
const emptyCapture: CaptureState = { capture: 'idle', samples: 0, elapsedSeconds: 0, level: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false }
type Phase = 'idle' | 'preparing' | 'recording' | 'uploading' | 'saving' | 'saved' | 'error'
type Lease = { part_id: string; lease_token: string }

export default function ConversationRecorder({ customerName, customerPhone, customerEmail, title, initialConversationId, onConversationChange, onBusyChange, onSaved, onCaptureChange, compact, grouped = false, chunkSeconds = 180, audioBucket = 'session-audio' }: ConversationRecorderProps & { compact?: boolean; grouped?: boolean }) {
  const { user, workspace, loading, error: authError } = useOffice()
  const [consent, setConsent] = useState(false)
  // compact mode: the checkbox is not shown; start uses a quick confirmation to record consent
  compact = compact ?? false
  const [phase, setPhase] = useState<Phase>('idle')
  const [capture, setCapture] = useState<CaptureState>(emptyCapture)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [source, setSource] = useState<'microphone' | 'upload'>('microphone')
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId || null)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [policy, setPolicy] = useState<{ engineId: string | null; ready: boolean } | null>(null)
  const [policyLoading, setPolicyLoading] = useState(true)
  const [recoveries, setRecoveries] = useState<Recovery[]>([])
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [retryFile, setRetryFile] = useState(false)
  const [operation, setOperation] = useState(false)
  const mounted = useRef(false)
  const generation = useRef(0)
  const active = useRef<RecorderSession | null>(null)
  const persistentSubscription = useRef<(() => void) | null>(null)
  const abort = useRef<AbortController | null>(null)
  const operationRef = useRef(false)
  const idRef = useRef<string | null>(initialConversationId || null)
  const notifiedSaved = useRef<string | null>(null)
  const creation = useRef<{ key: string; body: object } | null>(null)
  const tabId = useRef(crypto.randomUUID())
  const callbacks = useRef({ onConversationChange, onBusyChange, onSaved, onCaptureChange })
  callbacks.current = { onConversationChange, onBusyChange, onSaved, onCaptureChange }
  const captureActive = capture.capture === 'recording' || capture.capture === 'paused'
  const unfinished = !!conversationId && !saved
  const busy = operation || captureActive || capture.saving || unfinished || (!!file && !saved)
  const blocked = loading || !user || !workspace || policyLoading || !policy?.ready
  // Microphone capture does not require the default speech engine to be connected; allow starting the mic
  // once the user is authenticated and a workspace is selected. The engine is only needed for transcription/processing.
  const micBlocked = loading || !user || !workspace
  const meterLevel = Math.min(1, Math.max(0, capture.level * 8))

  function disposeCapture() {
    const session = active.current; active.current = null
    void session?.dispose().catch(() => undefined)
  }

  function showSaved(id: string) {
    if (!mounted.current) return
    setConversationId(id); idRef.current = id; setSaved(true); setPhase('saved'); setError(''); setRetryFile(false)
    callbacks.current.onConversationChange(id)
    if (notifiedSaved.current !== id) { notifiedSaved.current = id; callbacks.current.onSaved?.(id) }
  }
  async function refreshRecoveries() {
    if (!user || !workspace) return
    const rows = await listRecoveries({ userId: user.id, workspaceId: workspace.id })
    if (mounted.current) setRecoveries(rows)
  }
  useEffect(() => {
    mounted.current = true
    if (persistentRecorder && (!initialConversationId || persistentConversationId === initialConversationId)) {
      active.current = persistentRecorder; idRef.current = persistentConversationId; setConversationId(persistentConversationId); persistentSubscription.current = persistentRecorder.subscribe(state => { if (mounted.current) setCapture(state) })
    }
    return () => {
      mounted.current = false; generation.current++
      persistentSubscription.current?.(); persistentSubscription.current = null
      const session = active.current ?? persistentRecorder
      if (session && ['recording', 'paused'].includes(session.snapshot().capture)) {
        persistentRecorder = null; persistentConversationId = null; active.current = null
        void session.stop().catch(() => undefined)
      }
      callbacks.current.onBusyChange?.(false)
    }
  }, [])
  useEffect(() => { callbacks.current.onBusyChange?.(busy) }, [busy])
  useEffect(() => { callbacks.current.onCaptureChange?.(capture) }, [capture])
  useEffect(() => {
    if (!file) { setPreview(''); return }
    const url = URL.createObjectURL(file); setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  useEffect(() => {
    let current = true
    if (!workspace || !user) { setPolicy(null); setPolicyLoading(false); return }
    const controller = new AbortController(); setPolicyLoading(source === 'upload'); if (source === 'upload') setError('')
    const recoveries = listRecoveries({ userId: user.id, workspaceId: workspace.id })
    const policy = source === 'upload'
      ? Promise.all([
        request<EngineSettings>('/engine-settings', workspace.id, { signal: controller.signal }),
        request<Page<Connection>>('/connections', workspace.id, { signal: controller.signal }),
      ]).then(([settings, connections]) => {
        const engineId = settings.default_engine_id
        return { engineId, ready: localMode || (!!engineId && connections.items.some(item => item.engine_id === engineId && item.enabled && item.state === 'connected')) }
      })
      : Promise.resolve(null)
    void Promise.all([policy, recoveries]).then(([nextPolicy, rows]) => {
      if (!current) return
      if (nextPolicy) setPolicy(nextPolicy)
      setRecoveries(rows)
    }).catch(err => { if (current && source === 'upload') { setPolicy(null); setError(message(err)) } }).finally(() => { if (current) setPolicyLoading(false) })
    return () => { current = false; controller.abort(); generation.current++ }
  }, [source, user?.id, workspace?.id])
  useEffect(() => {
    if (!initialConversationId || !workspace || !user) return
    const controller = new AbortController()
    void request<Conversation>(`/conversations/${encodeURIComponent(initialConversationId)}`, workspace.id, { signal: controller.signal }).then(conversation => {
      if (mounted.current && !controller.signal.aborted) {
        idRef.current = conversation.id; setConversationId(conversation.id)
        if (conversation.upload_state === 'saved') showSaved(conversation.id)
      }
    }).catch(err => { if (!controller.signal.aborted && mounted.current) setError(message(err)) })
    return () => controller.abort()
  }, [initialConversationId, user?.id, workspace?.id])
  // Route changes are allowed while capture is active; the RecorderSession remains alive.
  // Its own beforeunload guard still protects an in-progress recording from accidental tab close.
  useEffect(() => {
    if (capture.capture === 'stopped' && !capture.saving && !capture.error && capture.samples > 0 && idRef.current) showSaved(idRef.current)
  }, [capture.capture, capture.saving, capture.error, capture.samples])

  function validateStart(consentGranted = false) {
    if (!consent && !consentGranted) throw new Error('Confirm participant consent before recording or uploading audio.')
    if (!user || !workspace) throw new Error('Sign in and select a workspace before adding audio.')
    if (!customerName.trim()) throw new Error('Enter the customer name before adding audio.')
    if (!customerPhone?.trim() && !customerEmail?.trim()) throw new Error('Enter a phone number or email before adding audio.')
  }

  // When compact mode is used, offer a small consent confirmation when the user presses Start.
  async function startMicrophoneCompactFlow() {
    if (operationRef.current || captureActive || saved) return
    setConsent(true)
    await startMicrophone(true)
  }
  async function createConversation(signal: AbortSignal, reuseExisting = false) {
    if (reuseExisting && idRef.current) return idRef.current
    if (!reuseExisting) { idRef.current = null; creation.current = null; setConversationId(null); callbacks.current.onConversationChange(null) }
    if (!creation.current) creation.current = {
      key: crypto.randomUUID(),
      body: { customer_name: customerName.trim(), phone: customerPhone?.trim() || null, email: customerEmail?.trim() || null, title: title?.trim() || file?.name.slice(0, 200) || 'Sales enquiry conversation', language_hint: 'auto', consent_confirmed: true, audio_only: true, audio_bucket: audioBucket },
    }
    const conversation = await request<Conversation>('/conversations', workspace!.id, { ...json('POST', creation.current.body, creation.current.key), signal })
    signal.throwIfAborted()
    idRef.current = conversation.id; setConversationId(conversation.id); callbacks.current.onConversationChange(conversation.id)
    return conversation.id
  }
  function begin() {
    generation.current++; abort.current?.abort(); disposeCapture()
    operationRef.current = true; setOperation(true); setError('')
    const controller = new AbortController(); abort.current = controller
    return { controller, epoch: generation.current }
  }
  function isCurrent(epoch: number, controller: AbortController) { return mounted.current && epoch === generation.current && !controller.signal.aborted }
  function finish(epoch: number, controller: AbortController) {
    operationRef.current = false
    if (isCurrent(epoch, controller)) { setOperation(false); void refreshRecoveries().catch(err => setError(message(err))) }
  }
  async function startMicrophone(consentGranted = false) {
    if (operationRef.current || captureActive || saved) return
    try { validateStart(consentGranted) } catch (err) { setError(message(err)); return }
    const { controller, epoch } = begin(); setPhase('preparing'); setCapture(emptyCapture)
    try {
      let id = await createConversation(controller.signal)
      let lease: Lease
      try {
        lease = await request<Lease>(`/conversations/${id}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: true }), signal: controller.signal })
      } catch (cause) {
        // A stale row from an older session must never block a new recording.
        if (!(cause instanceof ApiError) || !['conflicting_state', 'recording_already_stopped'].includes(cause.code)) throw cause
        const freshId = await createConversation(controller.signal, false)
        lease = await request<Lease>(`/conversations/${freshId}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: true }), signal: controller.signal })
        id = freshId
      }
      controller.signal.throwIfAborted()
      const session = new RecorderSession({
        scope: { userId: user!.id, workspaceId: workspace!.id }, conversationId: id, partId: lease.part_id, leaseToken: lease.lease_token,
        chunkSeconds,
        hooks: recordingHooks(id, workspace!.id, lease.part_id, lease.lease_token, tabId.current, controller.signal, workspace!.member_id),
        onState: state => {
          if (!isCurrent(epoch, controller)) return
          setCapture(state)
          if (state.capture === 'recording' || state.capture === 'paused') setPhase('recording')
          else if (state.saving) setPhase('saving')
          else if (state.error) { setPhase('error'); setError(state.error); void refreshRecoveries().catch(() => undefined) }
        },
      })
      active.current = session
      persistentRecorder = session; persistentConversationId = id
      await session.start()
      if (!isCurrent(epoch, controller)) await session.dispose()
    } catch (err) { if (isCurrent(epoch, controller)) { setError(message(err)); setPhase('error') } }
    finally { finish(epoch, controller) }
  }
  async function changeCapture(action: 'pause' | 'resume' | 'stop') {
    if (operationRef.current || !active.current) return
    operationRef.current = true; setOperation(true); setError('')
    try {
      await active.current[action]()
      if (action === 'stop' && idRef.current && mounted.current) showSaved(idRef.current)
      if (action === 'stop') { persistentRecorder = null; persistentConversationId = null; active.current = null }
    } catch (err) {
      // Keep Stop available after a pause/resume error; the session still owns its stream.
      if (mounted.current) { setError(message(err)); if (action === 'stop') setPhase('error') }
    } finally {
      operationRef.current = false
      if (mounted.current) { setOperation(false); void refreshRecoveries().catch(err => setError(message(err))) }
    }
  }
  function chooseFile(next: File | null) {
    if (operation || captureActive || capture.saving || saved || recoveries.some(row => (!conversationId || row.conversationId === conversationId) && row.totalSamples > 0)) return
    setError('')
    if (next && (!next.size || next.size > MAX_AUDIO_FILE_BYTES)) { setFile(null); setError('Choose a nonempty audio file up to 100 MB.'); return }
    setFile(next); setSource('upload'); setProgress(null); setRetryFile(!!conversationId)
  }
  async function uploadFile(retry = false) {
    if (operationRef.current || !file || saved) return
    try { validateStart() } catch (err) { setError(message(err)); return }
    const { controller, epoch } = begin(); setPhase('preparing'); setRetryFile(false)
    try {
      const audio = await prepareAudioFile(file)
      controller.signal.throwIfAborted()
      const id = await createConversation(controller.signal, retry)
      const lease = await request<Lease>(`/conversations/${id}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: retry }), signal: controller.signal })
      controller.signal.throwIfAborted(); setPhase('uploading')
      await uploadAudioFile({
        scope: { userId: user!.id, workspaceId: workspace!.id }, conversationId: id, partId: lease.part_id, leaseToken: lease.lease_token, audio,
        chunkSeconds,
        hooks: recordingHooks(id, workspace!.id, lease.part_id, lease.lease_token, tabId.current, controller.signal, workspace!.member_id), signal: controller.signal,
        onProgress: value => { if (isCurrent(epoch, controller)) { setProgress(value); setPhase(value.phase === 'finalizing' ? 'saving' : 'uploading') } },
      })
      if (isCurrent(epoch, controller)) showSaved(id)
    } catch (err) {
      if (isCurrent(epoch, controller)) { setError(message(err)); setPhase('error'); setRetryFile(!(err instanceof AudioUploadError) || !err.preserved) }
    } finally { finish(epoch, controller) }
  }
  const status = saved ? 'AUDIO SAVED' : phase === 'preparing' ? 'PREPARING AUDIO' : capture.capture === 'paused' ? 'RECORDING PAUSED' : captureActive ? 'RECORDING IN PROGRESS' : operation ? 'SAVING AUDIO' : 'READY TO ADD AUDIO'

  return <div className={`recorder-content connected-recorder${compact ? ' compact-recorder' : ''}`}>
    <div className={grouped ? 'session-recording-container' : undefined}>
    <div className={`recorder-panel${captureActive ? ' is-recording' : ''}${saved ? ' has-clip' : ''}`}>
      {!compact && <div className="recorder-visual"><div className={`microphone-orbit${captureActive ? ' orbit-active' : ''}`}><div className="microphone-button">{source === 'upload' ? <Upload size={27} /> : <Mic2 size={27} />}</div></div>{captureActive && <div className="voice-level-wave" aria-label="Microphone input level" role="img">{Array.from({ length: 32 }, (_, index) => <i key={index} style={{ height: `${Math.max(3, Math.min(28, 4 + meterLevel * (9 + ((index * 7) % 13))))}px` }} />)}</div>}</div>}
      {!compact && <div className="recorder-copy">
        <span className={captureActive ? 'recording-live' : 'recording-ready-label'}>{saved && <Check size={13} />} {status}</span>
        <h3>{saved ? 'Audio saved in your workspace' : 'Record or upload the conversation'}</h3>
        <p>{saved ? 'Transcription and English translation run in the background. Open the saved enquiry to follow progress.' : 'Audio saves privately. ElevenLabs transcribes the original speech, then OpenAI translates it into English.'}</p>
        {(captureActive || capture.elapsedSeconds > 0) && <div className="elapsed-timer">{duration(capture.elapsedSeconds)}</div>}
      </div>}
      <div className="recorder-controls">
        {compact ? (
          // Minimal compact controls: single Start
          !captureActive && !operation && !saved && source === 'microphone' && <button type="button" className="button button-primary record-start-button" disabled={micBlocked} onClick={() => void startMicrophoneCompactFlow()}><Mic2 size={16} />Start recording</button>
        ) : (
          // Full controls preserved for non-compact usage
          <>
            {!captureActive && !operation && !saved && source === 'microphone' && <button type="button" className="button button-primary record-start-button" disabled={micBlocked || !consent} onClick={() => void startMicrophone()}><Mic2 size={16} />Start recording</button>}
            {!grouped && !operation && !captureActive && !capture.saving && !saved && <label className="button button-upload"><Upload size={15} />Choose audio file<input type="file" aria-label="Choose audio file" accept="audio/*,.m4a,.mp3,.wav,.aac,.ogg,.flac,.webm,.mp4" onChange={event => { chooseFile(event.target.files?.[0] || null); event.currentTarget.value = '' }} /></label>}
            {!grouped && source === 'upload' && file && !saved && !operation && (!unfinished || retryFile) && <button type="button" className="button button-primary" disabled={blocked || !consent} onClick={() => void uploadFile(unfinished)}><Upload size={15} />{retryFile ? 'Retry audio upload' : 'Upload & process'}</button>}
          </>
        )}

        {compact && captureActive && <div className="voice-level-wave compact-wave" aria-label="Microphone input level" role="img">{Array.from({ length: 24 }, (_, index) => <i key={index} style={{ height: `${Math.max(3, Math.min(28, 4 + meterLevel * (9 + ((index * 7) % 13))))}px` }} />)}</div>}

        {captureActive && <>
          {compact ? <button type="button" className="button button-stop" disabled={operation} onClick={() => void changeCapture('stop')}><Square size={14} />Stop</button> : <button type="button" className="button button-stop" disabled={operation} onClick={() => void changeCapture('stop')}><Square size={14} />Stop & save</button> }
        </>}

        {operation && <span role="status">{phase === 'preparing' ? 'Preparing audio…' : phase === 'saving' ? 'Finishing audio save…' : 'Uploading audio…'}</span>}
      </div>
    </div>
    {!compact && <div className="recording-consent"><label className="checkbox-field"><input type="checkbox" checked={consent} disabled={operation || captureActive || capture.saving || saved} onChange={event => setConsent(event.target.checked)} /><span>I have permission from all intended participants to record or upload and process this conversation.</span></label></div>}
    {loading && <p role="status">Loading workspace…</p>}
    {!loading && (!user || !workspace) && <div className="recorder-error" role="alert">{authError || 'Sign in and select a workspace to record, upload, and process audio.'}</div>}
    {!loading && user && workspace && policyLoading && <p role="status">Checking processing connections…</p>}
    {!loading && user && workspace && source === 'upload' && !policyLoading && !policy?.ready && <div className="audio-storage-notice">Connect and test ElevenLabs and OpenAI in Settings before uploading audio or processing a recording.</div>}
    {localMode && <div className="audio-storage-notice"><ShieldCheck size={15} />Audio and enquiries save on this computer. Add ElevenLabs and OpenAI keys in Settings when you want transcription and English translation.</div>}
    {error && <div className="recorder-error" role="alert">{error}</div>}
    {captureActive && <div className="connected-recording-metrics"><span>Saved on server: <strong>{duration(capture.confirmedSamples / 16000)}</strong></span><span>Pending here: <strong>{capture.pendingChunks} chunks · {(capture.pendingBytes / 1024).toFixed(0)} KB</strong></span></div>}
    {file && preview && <div className="audio-preview"><div className="audio-preview-heading"><span><FileAudio2 size={16} />{file.name}</span><span>{(file.size / 1024 / 1024).toFixed(1)} MB</span></div><audio controls preload="metadata" src={preview} aria-label="Selected audio preview" /><p>Up to 100 MB and two hours. Upload uses the same transcription and translation pipeline as microphone recording.</p></div>}
    {progress && !saved && <div className="connected-recording-progress" role="status"><progress aria-label="Audio upload progress" max={progress.totalSamples || 1} value={progress.confirmedSamples} /><span>{duration(progress.confirmedSamples / 16000)} of {duration(progress.totalSamples / 16000)} uploaded</span><p>Pending audio stays scoped to this account and workspace on this device.</p></div>}
    </div>
  </div>
}
