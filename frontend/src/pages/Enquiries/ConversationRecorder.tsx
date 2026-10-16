import { useEffect, useRef, useState } from 'react'
import { Check, FileAudio2, Mic2, Pause, Play, RotateCcw, ShieldCheck, Square, Upload } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { duration, json, localMode, message, request, type Connection, type Conversation, type EngineSettings, type Page } from '../../services/officeApi'
import { recordingHooks } from '../../recording/apiHooks'
import { listRecoveries, RecorderSession, recoverRecording, type CaptureState } from '../../recording/session'
import type { Recovery } from '../../recording/outbox'
import { AudioUploadError, MAX_AUDIO_FILE_BYTES, prepareAudioFile, uploadAudioFile, type UploadProgress } from '../../recording/upload'
import '../../styles/recording.css'

export interface ConversationRecorderProps {
  customerName: string
  title?: string
  initialConversationId?: string | null
  onConversationChange: (id: string | null) => void
  onBusyChange?: (busy: boolean) => void
  onSaved?: (id: string) => void
  onCaptureChange?: (state: CaptureState) => void
}
const emptyCapture: CaptureState = { capture: 'idle', samples: 0, elapsedSeconds: 0, level: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false }
type Phase = 'idle' | 'preparing' | 'recording' | 'uploading' | 'saving' | 'saved' | 'error'
type Lease = { part_id: string; lease_token: string }

export default function ConversationRecorder({ customerName, title, initialConversationId, onConversationChange, onBusyChange, onSaved, onCaptureChange, compact }: ConversationRecorderProps & { compact?: boolean }) {
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
    return () => {
      mounted.current = false; generation.current++
      abort.current?.abort(); disposeCapture()
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
    const controller = new AbortController(); setPolicyLoading(true); setError('')
    void Promise.all([
      request<EngineSettings>('/engine-settings', workspace.id, { signal: controller.signal }),
      request<Page<Connection>>('/connections', workspace.id, { signal: controller.signal }),
      listRecoveries({ userId: user.id, workspaceId: workspace.id }),
    ]).then(([settings, connections, rows]) => {
      if (!current) return
      const engineId = settings.default_engine_id
      setPolicy({ engineId, ready: localMode || (!!engineId && connections.items.some(item => item.engine_id === engineId && item.enabled && item.state === 'connected')) })
      setRecoveries(rows)
    }).catch(err => { if (current) { setPolicy(null); setError(message(err)) } }).finally(() => { if (current) setPolicyLoading(false) })
    return () => { current = false; controller.abort(); generation.current++; abort.current?.abort(); disposeCapture() }
  }, [user?.id, workspace?.id])
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
  useEffect(() => {
    if (!busy) return
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    const leave = (event: MouseEvent) => {
      const target = event.target
      const link = target instanceof Element ? target.closest('a') : null
      if (link && link.getAttribute('href')?.startsWith('/')) { event.preventDefault(); event.stopPropagation(); setError('Finish saving or recover this audio before leaving the enquiry.') }
    }
    window.addEventListener('beforeunload', unload); document.addEventListener('click', leave, true)
    return () => { window.removeEventListener('beforeunload', unload); document.removeEventListener('click', leave, true) }
  }, [busy])
  useEffect(() => {
    if (capture.capture === 'stopped' && !capture.saving && !capture.error && capture.samples > 0 && idRef.current) showSaved(idRef.current)
  }, [capture.capture, capture.saving, capture.error, capture.samples])

  function validateStart(skipEngineCheck = false, consentGranted = false) {
    if (!consent && !consentGranted) throw new Error('Confirm participant consent before recording or uploading audio.')
    if (!user || !workspace) throw new Error('Sign in and select a workspace before adding audio.')
    if (!customerName.trim()) throw new Error('Enter the customer name before adding audio.')
    if (!skipEngineCheck && !policy?.ready) throw new Error('Connect and test the default speech engine in Settings before adding audio.')
  }

  // When compact mode is used, offer a small consent confirmation when the user presses Start.
  async function startMicrophoneCompactFlow() {
    if (operationRef.current || captureActive || saved) return
    setConsent(true)
    await startMicrophone(true, true)
  }
  async function createConversation(signal: AbortSignal) {
    if (idRef.current) return idRef.current
    if (!creation.current) creation.current = {
      key: crypto.randomUUID(),
      body: { customer_name: customerName.trim(), title: title?.trim() || file?.name.slice(0, 200) || 'Sales enquiry conversation', language_hint: 'auto', consent_confirmed: true, audio_only: false },
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
  async function startMicrophone(skipEngineCheck = false, consentGranted = false) {
    if (operationRef.current || captureActive || saved) return
    try { validateStart(skipEngineCheck, consentGranted) } catch (err) { setError(message(err)); return }
    const { controller, epoch } = begin(); setPhase('preparing'); setCapture(emptyCapture)
    try {
      const hadId = !!idRef.current
      const id = await createConversation(controller.signal)
      const lease = await request<Lease>(`/conversations/${id}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: hadId }), signal: controller.signal })
      controller.signal.throwIfAborted()
      const session = new RecorderSession({
        scope: { userId: user!.id, workspaceId: workspace!.id }, conversationId: id, partId: lease.part_id, leaseToken: lease.lease_token,
        hooks: recordingHooks(id, workspace!.id, lease.part_id, lease.lease_token, tabId.current, controller.signal),
        onState: state => {
          if (!isCurrent(epoch, controller)) return
          setCapture(state)
          if (state.capture === 'recording' || state.capture === 'paused') setPhase('recording')
          else if (state.saving) setPhase('saving')
          else if (state.error) { setPhase('error'); setError(state.error); void refreshRecoveries().catch(() => undefined) }
        },
      })
      active.current = session
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
      const id = await createConversation(controller.signal)
      const lease = await request<Lease>(`/conversations/${id}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: retry }), signal: controller.signal })
      controller.signal.throwIfAborted(); setPhase('uploading')
      await uploadAudioFile({
        scope: { userId: user!.id, workspaceId: workspace!.id }, conversationId: id, partId: lease.part_id, leaseToken: lease.lease_token, audio,
        hooks: recordingHooks(id, workspace!.id, lease.part_id, lease.lease_token, tabId.current, controller.signal), signal: controller.signal,
        onProgress: value => { if (isCurrent(epoch, controller)) { setProgress(value); setPhase(value.phase === 'finalizing' ? 'saving' : 'uploading') } },
      })
      if (isCurrent(epoch, controller)) showSaved(id)
    } catch (err) {
      if (isCurrent(epoch, controller)) { setError(message(err)); setPhase('error'); setRetryFile(!(err instanceof AudioUploadError) || !err.preserved) }
    } finally { finish(epoch, controller) }
  }
  async function recover(recovery: Recovery) {
    if (operationRef.current || captureActive || !workspace || !user) return
    const { controller, epoch } = begin(); setPhase('saving')
    try {
      const current = await request<Conversation>(`/conversations/${recovery.conversationId}`, workspace.id, { signal: controller.signal })
      if (current.customer_name_snapshot && current.customer_name_snapshot !== customerName.trim() && !window.confirm(`This saved recording belongs to ${current.customer_name_snapshot}. Attach it to this enquiry?`)) {
        setPhase('idle'); return
      }
      let token = recovery.leaseToken
      if (current.upload_state !== 'saved') {
        const lease = await request<Lease>(`/conversations/${recovery.conversationId}/lease`, workspace.id, { ...json('POST', { tab_id: tabId.current, takeover: true, purpose: 'recover' }), signal: controller.signal })
        token = lease.lease_token
      }
      controller.signal.throwIfAborted()
      await recoverRecording({ userId: user.id, workspaceId: workspace.id }, recovery.conversationId, recordingHooks(recovery.conversationId, workspace.id, recovery.partId, token, tabId.current, controller.signal), token, controller.signal)
      if (isCurrent(epoch, controller)) showSaved(recovery.conversationId)
    } catch (err) { if (isCurrent(epoch, controller)) { setPhase('error'); setError(message(err)) } }
    finally { finish(epoch, controller) }
  }
  function removeAttachment() {
    if (operation || captureActive || capture.saving) return
    if (conversationId && !window.confirm('Detach this audio from the enquiry? Saved audio stays in the workspace, and pending audio stays on this device for recovery.')) return
    generation.current++; abort.current?.abort(); disposeCapture()
    setConversationId(null); idRef.current = null; creation.current = null; notifiedSaved.current = null
    callbacks.current.onConversationChange(null); setSaved(false); setPhase('idle'); setCapture(emptyCapture); setFile(null); setSource('microphone'); setProgress(null); setError(''); setRetryFile(false)
  }
  const applicableRecoveries = recoveries.filter(row => (!conversationId || row.conversationId === conversationId) && row.totalSamples > 0)
  const status = saved ? 'AUDIO SAVED' : phase === 'preparing' ? 'PREPARING AUDIO' : capture.capture === 'paused' ? 'RECORDING PAUSED' : captureActive ? 'RECORDING IN PROGRESS' : operation ? 'SAVING AUDIO' : unfinished ? 'AUDIO NEEDS ATTENTION' : 'READY TO ADD AUDIO'

  return <div className={`recorder-content connected-recorder${compact ? ' compact-recorder' : ''}`}>
    <div className={`recorder-panel${captureActive ? ' is-recording' : ''}${saved ? ' has-clip' : ''}`}>
      {!compact && <div className="recorder-visual"><div className={`microphone-orbit${captureActive ? ' orbit-active' : ''}`}><div className="microphone-button">{source === 'upload' ? <Upload size={27} /> : <Mic2 size={27} />}</div></div></div>}
      {!compact && <div className="recorder-copy">
        <span className={captureActive ? 'recording-live' : 'recording-ready-label'}>{saved && <Check size={13} />} {status}</span>
        <h3>{saved ? 'Audio saved in your workspace' : 'Record or upload the conversation'}</h3>
        <p>{saved ? 'Transcription and English translation run in the background. Open the saved enquiry to follow progress.' : 'Audio saves privately. ElevenLabs transcribes the original speech, then OpenAI translates it into English.'}</p>
        {(captureActive || capture.elapsedSeconds > 0) && <div className="elapsed-timer">{duration(capture.elapsedSeconds)}</div>}
      </div>}
      <div className="recorder-controls">
        {compact ? (
          // Minimal compact controls: single Start
          !captureActive && !operation && !saved && source === 'microphone' && <button type="button" className="button button-primary record-start-button" disabled={micBlocked} onClick={() => void startMicrophoneCompactFlow()}><Mic2 size={16} />{unfinished ? 'Retry recording' : 'Start recording'}</button>
        ) : (
          // Full controls preserved for non-compact usage
          <>
            {!captureActive && !operation && !saved && source === 'microphone' && <button type="button" className="button button-primary record-start-button" disabled={micBlocked || !consent || !!applicableRecoveries.length} onClick={() => void startMicrophone()}><Mic2 size={16} />{unfinished ? 'Retry microphone' : 'Start recording'}</button>}
            { !operation && !captureActive && !capture.saving && !saved && !applicableRecoveries.length && <label className="button button-upload"><Upload size={15} />Choose audio file<input type="file" aria-label="Choose audio file" accept="audio/*,.m4a,.mp3,.wav,.aac,.ogg,.flac,.webm,.mp4" onChange={event => { chooseFile(event.target.files?.[0] || null); event.currentTarget.value = '' }} /></label>}
            {source === 'upload' && file && !saved && !operation && (!unfinished || retryFile) && <button type="button" className="button button-primary" disabled={blocked || !consent} onClick={() => void uploadFile(unfinished)}><Upload size={15} />{retryFile ? 'Retry audio upload' : 'Upload & process'}</button>}
            {(saved || ((unfinished || file) && !operation && !captureActive)) && <button type="button" className="button button-secondary" onClick={removeAttachment}>{conversationId ? 'Detach audio' : 'Remove selected file'}</button>}
          </>
        )}

        {captureActive && <>
          <button type="button" className="button button-secondary" disabled={operation} onClick={() => void changeCapture(capture.capture === 'paused' ? 'resume' : 'pause')}>{capture.capture === 'paused' ? <Play size={15} /> : <Pause size={15} />}{capture.capture === 'paused' ? 'Resume' : 'Pause'}</button>
          {compact ? <button type="button" className="button button-stop" disabled={operation} onClick={() => void changeCapture('stop')}><Square size={14} />Stop</button> : <button type="button" className="button button-stop" disabled={operation} onClick={() => void changeCapture('stop')}><Square size={14} />Stop & save</button> }
        </>}

        {operation && <span role="status">{phase === 'preparing' ? 'Preparing audio…' : phase === 'saving' ? 'Finishing audio save…' : 'Uploading audio…'}</span>}
      </div>
    </div>
    {!compact && <div className="recording-consent"><label className="checkbox-field"><input type="checkbox" checked={consent} disabled={operation || captureActive || capture.saving || saved} onChange={event => setConsent(event.target.checked)} /><span>I have permission from all intended participants to record or upload and process this conversation.</span></label><span className="consent-detail"><ShieldCheck size={14} />Consent is recorded by the authenticated backend before audio is captured or uploaded.</span></div>}
    {loading && <p role="status">Loading workspace…</p>}
    {!loading && (!user || !workspace) && <div className="recorder-error" role="alert">{authError || 'Sign in and select a workspace to record, upload, and process audio.'}</div>}
    {!loading && user && workspace && policyLoading && <p role="status">Checking processing connections…</p>}
    {!loading && user && workspace && !policyLoading && !policy?.ready && <div className="audio-storage-notice">Connect and test ElevenLabs and OpenAI in Settings. The default speech engine must be connected before recording or upload starts.</div>}
    {localMode && <div className="audio-storage-notice"><ShieldCheck size={15} />Audio and enquiries save on this computer. Add ElevenLabs and OpenAI keys in Settings when you want transcription and English translation.</div>}
    {error && <div className="recorder-error" role="alert">{error}</div>}
    {captureActive && <div className="connected-recording-metrics"><span>Saved on server: <strong>{duration(capture.confirmedSamples / 16000)}</strong></span><span>Pending here: <strong>{capture.pendingChunks} chunks · {(capture.pendingBytes / 1024).toFixed(0)} KB</strong></span><meter aria-label="Microphone input level" min={0} max={1} value={Math.min(1, capture.level * 6)} /></div>}
    {file && preview && <div className="audio-preview"><div className="audio-preview-heading"><span><FileAudio2 size={16} />{file.name}</span><span>{(file.size / 1024 / 1024).toFixed(1)} MB</span></div><audio controls preload="metadata" src={preview} aria-label="Selected audio preview" /><p>Up to 100 MB and two hours. Upload uses the same transcription and translation pipeline as microphone recording.</p></div>}
    {progress && !saved && <div className="connected-recording-progress" role="status"><progress aria-label="Audio upload progress" max={progress.totalSamples || 1} value={progress.confirmedSamples} /><span>{duration(progress.confirmedSamples / 16000)} of {duration(progress.totalSamples / 16000)} uploaded</span><p>Pending audio stays scoped to this account and workspace on this device.</p></div>}
    {!compact && !operation && !captureActive && !saved && applicableRecoveries.map(row => <div className="audio-storage-notice connected-audio-recovery" key={row.key}><RotateCcw size={18} /><div><strong>Audio is waiting on this device</strong><p>Recording {row.conversationId.slice(0, 8)} · {duration(row.totalSamples / 16000)}. Recover it to attach its saved conversation to this enquiry.</p><button type="button" className="button button-secondary" onClick={() => void recover(row)}>Recover audio</button></div></div>)}
  </div>
}
