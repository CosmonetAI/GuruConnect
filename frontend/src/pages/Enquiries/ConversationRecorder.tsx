import { useEffect, useRef, useState } from 'react'
import { Check, FileAudio2, Mic2, Square, Upload } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { duration, json, message, request, type Connection, type Conversation, type EngineSettings, type Page } from '../../services/officeApi'
import { recordingHooks } from '../../recording/apiHooks'
import { discardRecovery, listRecoveries, recoverRecording, RecorderSession, type CaptureState } from '../../recording/session'
import type { Recovery } from '../../recording/outbox'
import { AudioUploadError, MAX_AUDIO_FILE_BYTES, prepareAudioFile, uploadAudioFile, type UploadProgress } from '../../recording/upload'
import '../../styles/recording.css'

export interface ConversationRecorderProps {
  customerName: string
  customerPhone?: string
  customerEmail?: string
  title?: string
  initialConversationId?: string | null
  onConversationChange: (id: string | null) => void | Promise<void>
  onBusyChange?: (busy: boolean) => void
  onSaved?: (id: string) => void
  onCaptureChange?: (state: CaptureState) => void
  chunkSeconds?: number
  audioBucket?: 'session-audio' | 'walkin-audio'
  compact?: boolean
  grouped?: boolean
}
const emptyCapture: CaptureState = { capture: 'idle', samples: 0, elapsedSeconds: 0, level: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false }
type Phase = 'idle' | 'preparing' | 'recording' | 'uploading' | 'saving' | 'saved' | 'error'
type Lease = { part_id: string; lease_token: string }

function recordingTabId(userId?: string, workspaceId?: string, memberId?: string) {
  if (!userId || !workspaceId) return crypto.randomUUID()
  const key = `guruconnect:recording-tab:${userId}:${workspaceId}:${memberId ?? userId}`
  try {
    const existing = sessionStorage.getItem(key)
    if (existing) return existing
    const id = crypto.randomUUID(); sessionStorage.setItem(key, id); return id
  } catch { return crypto.randomUUID() }
}

export default function ConversationRecorder(props: ConversationRecorderProps) {
  const { user, workspace } = useOffice()
  // Recording state must never cross account/workspace boundaries. The conversation ID
  // is deliberately not a key: creating a conversation must not unmount its microphone.
  return <ScopedRecorder key={`${user?.id}:${workspace?.id}:${workspace?.member_id ?? user?.id}`} {...props} />
}

function ScopedRecorder({ customerName, customerPhone, customerEmail, title, initialConversationId, onConversationChange, onBusyChange, onSaved, onCaptureChange, compact = false, grouped = false, chunkSeconds = 10, audioBucket = 'session-audio' }: ConversationRecorderProps) {
  const { user, workspace, loading, localMode, error: authError } = useOffice()
  const [consent, setConsent] = useState(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [capture, setCapture] = useState<CaptureState>(emptyCapture)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId || null)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [processingReady, setProcessingReady] = useState<boolean | null>(null)
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
  const tabId = useRef(recordingTabId(user?.id, workspace?.id, workspace?.member_id))
  const callbacks = useRef({ onConversationChange, onBusyChange, onSaved, onCaptureChange })
  callbacks.current = { onConversationChange, onBusyChange, onSaved, onCaptureChange }
  const captureActive = ['recording', 'paused'].includes(capture.capture)
  const unfinished = !!conversationId && !saved
  const anotherMemberView = !!user && !!workspace?.member_id && workspace.member_id !== user.id
  const busy = operation || captureActive || capture.saving || (!anotherMemberView && (unfinished || (!!file && !saved)))
  const blocked = loading || !user || !workspace || anotherMemberView
  const meterLevel = Math.min(1, Math.max(0, capture.level * 8))

  function isCurrent(epoch: number, controller: AbortController) { return mounted.current && epoch === generation.current && !controller.signal.aborted }
  async function notify(id: string | null) { await callbacks.current.onConversationChange(id) }
  async function showSaved(id: string, reportChange = true) {
    if (!mounted.current || idRef.current !== id) return
    setConversationId(id); setSaved(true); setPhase('saved'); setError(''); setRetryFile(false); setFile(null)
    if (reportChange) await notify(id)
    if (mounted.current && idRef.current === id && notifiedSaved.current !== id) { notifiedSaved.current = id; callbacks.current.onSaved?.(id) }
  }
  async function refreshRecoveries() {
    if (!user || !workspace) return
    const epoch = generation.current
    const rows = await listRecoveries({ userId: user.id, workspaceId: workspace.id })
    if (mounted.current && epoch === generation.current) setRecoveries(rows)
  }
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false; generation.current++; abort.current?.abort()
      const session = active.current; active.current = null
      void session?.dispose().catch(() => undefined)
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
    if (!workspace || !user) return
    const controller = new AbortController()
    void refreshRecoveries().catch(reason => { if (!controller.signal.aborted) setError(message(reason)) })
    void Promise.all([
      request<EngineSettings>('/engine-settings', workspace.id, { signal: controller.signal }),
      request<Page<Connection>>('/connections', workspace.id, { signal: controller.signal }),
    ]).then(([settings, connections]) => {
      if (!controller.signal.aborted) setProcessingReady(settings.default_engine_id === 'elevenlabs' && ['elevenlabs', 'openai-translator'].every(engine => connections.items.some(item => item.engine_id === engine && item.state === 'connected')))
    }).catch(() => { if (!controller.signal.aborted) setProcessingReady(false) })
    return () => controller.abort()
  }, [user?.id, workspace?.id])
  useEffect(() => {
    if (!initialConversationId || !workspace || !user || active.current || operationRef.current) return
    const controller = new AbortController()
    idRef.current = initialConversationId; setConversationId(initialConversationId); setSaved(false)
    void request<Conversation>(`/conversations/${encodeURIComponent(initialConversationId)}`, workspace.id, { signal: controller.signal }, workspace.member_id).then(async conversation => {
      if (!mounted.current || controller.signal.aborted || idRef.current !== initialConversationId) return
      if (conversation.upload_state === 'saved') await showSaved(conversation.id, false)
    }).catch(reason => { if (!controller.signal.aborted && mounted.current) setError(message(reason)) })
    return () => controller.abort()
  }, [initialConversationId, user?.id, workspace?.id])

  function validateStart() {
    if (anotherMemberView) throw new Error('Switch to your own account view to record.')
    if (!consent) throw new Error('Confirm participant consent before recording or uploading audio.')
    if (!user || !workspace) throw new Error('Sign in and select a workspace before adding audio.')
    if (!customerName.trim()) throw new Error('Enter the customer name before adding audio.')
    if (!customerPhone?.trim() && !customerEmail?.trim()) throw new Error('Enter a phone number or email before adding audio.')
  }
  async function createConversation(signal: AbortSignal, reuseExisting = false) {
    if (reuseExisting && idRef.current) return idRef.current
    if (!creation.current) creation.current = {
      key: crypto.randomUUID(),
      body: { customer_name: customerName.trim(), phone: customerPhone?.trim() || null, email: customerEmail?.trim() || null, title: title?.trim() || file?.name.slice(0, 200) || 'Sales enquiry conversation', language_hint: 'auto', consent_confirmed: true, audio_only: false, audio_bucket: audioBucket },
    }
    const conversation = await request<Conversation>('/conversations', workspace!.id, { ...json('POST', creation.current.body, creation.current.key), signal }, workspace!.member_id)
    signal.throwIfAborted()
    idRef.current = conversation.id; setConversationId(conversation.id); await notify(conversation.id)
    signal.throwIfAborted()
    return conversation.id
  }
  function begin() {
    generation.current++; abort.current?.abort()
    operationRef.current = true; setOperation(true); setError('')
    const controller = new AbortController(); abort.current = controller
    return { controller, epoch: generation.current }
  }
  function finish(epoch: number, controller: AbortController) {
    if (!isCurrent(epoch, controller)) return
    operationRef.current = false; setOperation(false)
    void refreshRecoveries().catch(reason => { if (mounted.current) setError(message(reason)) })
  }
  async function startMicrophone() {
    if (operationRef.current || captureActive || saved || unfinished) return
    try { validateStart() } catch (reason) { setError(message(reason)); return }
    const { controller, epoch } = begin(); setPhase('preparing'); setCapture(emptyCapture)
    try {
      const id = await createConversation(controller.signal)
      const lease = await request<Lease>(`/conversations/${id}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: false }), signal: controller.signal }, workspace!.member_id)
      controller.signal.throwIfAborted()
      const session = new RecorderSession({
        scope: { userId: user!.id, workspaceId: workspace!.id }, conversationId: id, partId: lease.part_id, leaseToken: lease.lease_token, chunkSeconds, signal: controller.signal,
        hooks: recordingHooks(id, workspace!.id, lease.part_id, lease.lease_token, tabId.current, controller.signal, workspace!.member_id),
        onState: state => {
          if (!isCurrent(epoch, controller)) return
          setCapture(state)
          if (['recording', 'paused'].includes(state.capture)) setPhase('recording')
          else if (state.saving) setPhase('saving')
          else if (state.error) { setPhase('error'); setError(state.error); void refreshRecoveries().catch(() => undefined) }
          else if (state.capture === 'stopped' && state.samples > 0) void showSaved(id).catch(reason => { if (mounted.current) setError(message(reason)) })
        },
      })
      active.current = session
      await session.start()
      if (!isCurrent(epoch, controller)) await session.dispose()
    } catch (reason) { if (isCurrent(epoch, controller)) { setError(message(reason)); setPhase('error') } }
    finally { finish(epoch, controller) }
  }
  async function changeCapture(action: 'pause' | 'resume' | 'stop') {
    if (operationRef.current || !active.current) return
    const epoch = generation.current; const session = active.current
    operationRef.current = true; setOperation(true); setError('')
    try {
      await session[action]()
      if (mounted.current && epoch === generation.current && action === 'stop' && idRef.current) { await showSaved(idRef.current); active.current = null }
    } catch (reason) { if (mounted.current && epoch === generation.current) { setError(message(reason)); if (action === 'stop') setPhase('error') } }
    finally { if (mounted.current && epoch === generation.current) { operationRef.current = false; setOperation(false); void refreshRecoveries().catch(() => undefined) } }
  }
  function chooseFile(next: File | null) {
    if (blocked || operationRef.current || captureActive || capture.saving || saved || unfinished) return
    setError('')
    if (next && (!next.size || next.size > MAX_AUDIO_FILE_BYTES)) { setFile(null); setError('Choose a nonempty audio file up to 100 MB.'); return }
    setFile(next); setProgress(null); setRetryFile(false)
  }
  async function uploadFile(retry = false) {
    if (operationRef.current || !file || saved) return
    try { validateStart() } catch (reason) { setError(message(reason)); return }
    const { controller, epoch } = begin(); setPhase('preparing'); setRetryFile(false)
    try {
      const audio = await prepareAudioFile(file); controller.signal.throwIfAborted()
      const id = await createConversation(controller.signal, retry)
      const lease = await request<Lease>(`/conversations/${id}/lease`, workspace!.id, { ...json('POST', { tab_id: tabId.current, takeover: retry, purpose: retry ? 'recover' : 'capture' }), signal: controller.signal }, workspace!.member_id)
      controller.signal.throwIfAborted(); setPhase('uploading')
      await uploadAudioFile({ scope: { userId: user!.id, workspaceId: workspace!.id }, conversationId: id, partId: lease.part_id, leaseToken: lease.lease_token, audio, chunkSeconds,
        hooks: recordingHooks(id, workspace!.id, lease.part_id, lease.lease_token, tabId.current, controller.signal, workspace!.member_id), signal: controller.signal,
        onProgress: value => { if (isCurrent(epoch, controller)) { setProgress(value); setPhase(value.phase === 'finalizing' ? 'saving' : 'uploading') } },
      })
      if (isCurrent(epoch, controller)) await showSaved(id)
    } catch (reason) { if (isCurrent(epoch, controller)) { setError(message(reason)); setPhase('error'); setRetryFile(!(reason instanceof AudioUploadError) || !reason.preserved) } }
    finally { finish(epoch, controller) }
  }
  async function recover(row: Recovery) {
    if (anotherMemberView || operationRef.current || captureActive || capture.saving || !workspace || !user) return
    const { controller, epoch } = begin(); setPhase('saving')
    try {
      const old = active.current; active.current = null; await old?.dispose()
      const original = await request<Conversation>(`/conversations/${row.conversationId}`, workspace.id, { signal: controller.signal }, workspace.member_id)
      const digits = (value?: string) => value?.replace(/\D/g, '')
      const belongsToCustomer = original.phone ? digits(original.phone) === digits(customerPhone) : original.email ? original.email.toLowerCase() === customerEmail?.toLowerCase() : !original.customer_name_snapshot || original.customer_name_snapshot === customerName.trim()
      if (!belongsToCustomer) throw new Error('This pending recording belongs to another customer. Open the original customer before recovering it.')
      const lease = await request<Lease>(`/conversations/${row.conversationId}/lease`, workspace.id, { ...json('POST', { tab_id: tabId.current, takeover: true, purpose: 'recover' }), signal: controller.signal }, workspace.member_id)
      await recoverRecording({ userId: user.id, workspaceId: workspace.id }, row.conversationId, recordingHooks(row.conversationId, workspace.id, lease.part_id, lease.lease_token, tabId.current, controller.signal, workspace.member_id), lease.lease_token, controller.signal)
      if (isCurrent(epoch, controller)) { idRef.current = row.conversationId; setCapture(emptyCapture); await showSaved(row.conversationId) }
    } catch (reason) { if (isCurrent(epoch, controller)) { setError(message(reason)); setPhase('error') } }
    finally { finish(epoch, controller) }
  }
  async function resetRecording(discard = false, id = idRef.current) {
    if (anotherMemberView || operationRef.current || captureActive || capture.saving || !user || !workspace) return
    if (discard && !window.confirm('Permanently discard this pending recording and its local audio?')) return
    const { controller, epoch } = begin()
    try {
      const session = active.current; active.current = null; await session?.dispose()
      if (discard && id) {
        await request(`/conversations/${encodeURIComponent(id)}`, workspace.id, { method: 'DELETE', signal: controller.signal }, workspace.member_id)
        await discardRecovery({ userId: user.id, workspaceId: workspace.id }, id)
      }
      if (!isCurrent(epoch, controller)) return
      if (id === idRef.current || !idRef.current) {
        idRef.current = null; creation.current = null; notifiedSaved.current = null
        setConversationId(null); setSaved(false); setFile(null); setCapture(emptyCapture); setProgress(null); setRetryFile(false); setConsent(false); setPhase('idle')
        await notify(null)
      }
    } catch (reason) { if (isCurrent(epoch, controller)) setError(message(reason)) }
    finally { finish(epoch, controller) }
  }
  const relevantRecoveries = recoveries.filter(row => !conversationId || row.conversationId === conversationId)
  const status = saved ? 'AUDIO SAVED' : phase === 'preparing' ? 'PREPARING AUDIO' : capture.capture === 'paused' ? 'RECORDING PAUSED' : captureActive ? 'RECORDING IN PROGRESS' : operation ? 'SAVING AUDIO' : 'READY TO ADD AUDIO'
  return <div className={`recorder-content connected-recorder${compact ? ' compact-recorder' : ''}`}>
    <div className={grouped ? 'session-recording-container' : undefined}>
      <div className={`recorder-panel${captureActive ? ' is-recording' : ''}${saved ? ' has-clip' : ''}`}>
        {!compact && <div className="recorder-visual"><div className={`microphone-orbit${captureActive ? ' orbit-active' : ''}`}><div className="microphone-button"><Mic2 size={27} /></div></div></div>}
        <div className="recorder-copy"><span className={captureActive ? 'recording-live' : 'recording-ready-label'}>{saved && <Check size={13} />} {status}</span><h3>{saved ? 'Audio saved in your workspace' : 'Record or upload the conversation'}</h3><p>{saved ? 'Follow source transcription and English translation below or in Conversation history.' : 'Consented audio is transcribed by ElevenLabs, then translated to English where needed.'}</p>{(captureActive || capture.elapsedSeconds > 0) && <div className="elapsed-timer">{duration(capture.elapsedSeconds)}</div>}</div>
        <div className="recorder-controls">
          {!captureActive && !operation && !saved && !unfinished && !file && <button type="button" className="button button-primary" disabled={blocked || !consent} onClick={() => void startMicrophone()}><Mic2 size={16} />Start recording</button>}
          {!operation && !captureActive && !capture.saving && !saved && !unfinished && <label className="button button-upload"><Upload size={15} />Choose audio file<input type="file" aria-label="Choose audio file" accept="audio/*,.m4a,.mp3,.wav,.aac,.ogg,.flac,.webm,.mp4" disabled={blocked} onChange={event => { chooseFile(event.target.files?.[0] || null); event.currentTarget.value = '' }} /></label>}
          {file && !saved && !operation && (!unfinished || retryFile) && <button type="button" className="button button-primary" disabled={blocked || !consent} onClick={() => void uploadFile(unfinished)}>{retryFile ? 'Retry audio upload' : 'Upload & process'}</button>}
          {file && !operation && !unfinished && <button type="button" className="button button-quiet" onClick={() => { setFile(null); setError(''); setProgress(null) }}>Remove selected file</button>}
          {captureActive && <><button type="button" className="button button-secondary" disabled={operation} onClick={() => void changeCapture(capture.capture === 'paused' ? 'resume' : 'pause')}>{capture.capture === 'paused' ? 'Resume' : 'Pause'}</button><button type="button" className="button button-stop" disabled={operation} onClick={() => void changeCapture('stop')}><Square size={14} />Stop & save</button><div className="voice-level-wave" aria-label="Microphone input level" role="img">{Array.from({ length: 24 }, (_, index) => <i key={index} style={{ height: `${Math.max(3, Math.min(28, 4 + meterLevel * (9 + ((index * 7) % 13))))}px` }} />)}</div></>}
          {saved && !operation && !anotherMemberView && <button type="button" className="button button-secondary" onClick={() => void resetRecording()}>Record another conversation</button>}
          {conversationId && !anotherMemberView && !captureActive && !operation && !capture.saving && <button type="button" className="button button-quiet" onClick={() => void resetRecording()}>Detach audio</button>}
          {unfinished && !anotherMemberView && !captureActive && !operation && <button type="button" className="button button-danger-quiet" onClick={() => void resetRecording(true)}>Discard recording</button>}
          {operation && <span role="status">{phase === 'preparing' ? 'Preparing audio…' : phase === 'saving' ? 'Finishing audio save…' : 'Uploading audio…'}</span>}
        </div>
      </div>
      <div className="recording-consent"><label className="checkbox-field"><input type="checkbox" checked={consent} disabled={operation || captureActive || capture.saving || saved} onChange={event => setConsent(event.target.checked)} /><span>I have permission from all intended participants to record or upload and process this conversation.</span></label></div>
      {anotherMemberView && <p role="status">Switch to your own account view to record.</p>}
      {loading && <p role="status">Loading workspace…</p>}
      {!loading && (!user || !workspace) && <div className="recorder-error" role="alert">{authError || 'Sign in and select a workspace to record, upload, and process audio.'}</div>}
      {processingReady === false && <p className="audio-storage-notice">Automatic processing is waiting for the managed speech services. Saved audio is retained; contact your administrator if processing stays blocked.</p>}
      {localMode && <p className="audio-storage-notice">Audio and enquiries save on this computer. Speech services are managed by your administrator.</p>}
      {error && <div className="recorder-error" role="alert">{error}</div>}
      {!anotherMemberView && !captureActive && !capture.saving && relevantRecoveries.length > 0 && <section aria-label="Pending audio recovery">{relevantRecoveries.map(row => <div key={row.key}><p>Pending recording · {duration(row.totalSamples / 16000)} · {new Date(row.updatedAt).toLocaleString()}</p><button type="button" className="button button-primary" disabled={operation} onClick={() => void recover(row)}>Recover audio</button><button type="button" className="button button-danger-quiet" disabled={operation} onClick={() => void resetRecording(true, row.conversationId)}>Discard pending audio</button></div>)}</section>}
      {captureActive && <div className="connected-recording-metrics"><span>Saved on server: <strong>{duration(capture.confirmedSamples / 16000)}</strong></span><span>Pending here: <strong>{capture.pendingChunks} chunks · {(capture.pendingBytes / 1024).toFixed(0)} KB</strong></span></div>}
      {file && preview && <div className="audio-preview"><div className="audio-preview-heading"><span><FileAudio2 size={16} />{file.name}</span><span>{(file.size / 1024 / 1024).toFixed(1)} MB</span></div><audio controls preload="metadata" src={preview} aria-label="Selected audio preview" /><p>Up to 100 MB and two hours.</p></div>}
      {progress && !saved && <div className="connected-recording-progress" role="status"><progress aria-label="Audio upload progress" max={progress.totalSamples || 1} value={progress.confirmedSamples} /><span>{duration(progress.confirmedSamples / 16000)} of {duration(progress.totalSamples / 16000)} uploaded</span></div>}
    </div>
  </div>
}
