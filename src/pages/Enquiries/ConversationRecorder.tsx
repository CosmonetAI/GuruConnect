import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { AudioLines, Check, FileAudio2, Mic2, Pause, Play, RotateCcw, ShieldCheck, Square, Upload, X } from 'lucide-react'

type RecorderState = 'idle' | 'requesting' | 'recording' | 'paused' | 'finalizing' | 'ready' | 'error'
type ClipInfo = { duration: number; name: string; consentTimestamp: string }

const mimeTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
}

function permissionMessage(error: unknown) {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
      return 'Microphone access was denied. Allow microphone access in your browser settings, then try again.'
    }
    if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
      return 'No microphone was found. Connect a microphone and try again.'
    }
    if (error.name === 'NotReadableError' || error.name === 'TrackStartError') {
      return 'The microphone is unavailable or being used by another application.'
    }
  }
  return error instanceof Error ? `Recording could not start: ${error.message}` : 'Recording could not start. Check your microphone and try again.'
}

export default function ConversationRecorder({ onClipChange, stopRef, isRecordingRef, onRecordingChange }: { onClipChange: (clip: ClipInfo | null) => void, stopRef?: MutableRefObject<(() => void) | null>, isRecordingRef?: MutableRefObject<boolean | null>, onRecordingChange?: (isRecording: boolean) => void }) {
  const [recorderState, setRecorderState] = useState<RecorderState>('idle')
  const [consentGiven, setConsentGiven] = useState(false)
  const [consentTimestamp, setConsentTimestamp] = useState('')
  const [clipInfo, setClipInfo] = useState<ClipInfo | null>(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [recordingError, setRecordingError] = useState('')
  const [audioError, setAudioError] = useState('')
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const startTimeRef = useRef(0)
  const carriedMillisecondsRef = useRef(0)
  const busyRef = useRef(false)
  const discardRef = useRef(false)
  const activeConsentRef = useRef('')
  const pausedRef = useRef(false)
  const mountedRef = useRef(true)

  const releaseTracks = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
  }

  useEffect(() => {
    if (recorderState !== 'recording') return
    const interval = window.setInterval(() => {
      setElapsedSeconds(Math.floor((carriedMillisecondsRef.current + Date.now() - startTimeRef.current) / 1000))
    }, 250)
    return () => window.clearInterval(interval)
  }, [recorderState])

  useEffect(() => () => {
    mountedRef.current = false
    const recorder = recorderRef.current
    if (recorder) {
      recorder.ondataavailable = null
      recorder.onstop = null
      recorder.onerror = null
      if (recorder.state !== 'inactive') {
        try {
          recorder.stop()
        } catch {
          // The component is unmounting; the tracks are still released below.
        }
      }
    }
    releaseTracks()
  }, [])

  useEffect(() => {
    if (!previewUrl) return
    return () => URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const clearClip = () => {
    onClipChange(null)
    setClipInfo(null)
    setPreviewUrl('')
    setElapsedSeconds(0)
    setConsentTimestamp('')
    activeConsentRef.current = ''
    setAudioError('')
    setRecordingError('')
    setRecorderState('idle')
  }

  const startRecording = async () => {
    if (busyRef.current || recorderState === 'recording' || recorderState === 'paused') return
    setRecordingError('')
    if (!consentGiven) {
      setRecordingError('Confirm that everyone on the call has agreed to recording before you start.')
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setRecorderState('error')
      setRecordingError('Audio recording is not supported in this browser. You can still attach an audio file for local preview.')
      return
    }
    busyRef.current = true
    setRecorderState('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const mimeType = mimeTypes.find((type) => MediaRecorder.isTypeSupported(type))
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
      const confirmedAt = new Date().toISOString()
      activeConsentRef.current = confirmedAt
      setConsentTimestamp(confirmedAt)
      chunksRef.current = []
      carriedMillisecondsRef.current = 0
      discardRef.current = false
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onerror = () => {
        discardRef.current = true
        releaseTracks()
        setRecorderState('error')
        setRecordingError('The browser encountered an error while recording. Try again or choose an audio file.')
      }
      recorder.onstop = () => {
        releaseTracks()
        if (discardRef.current) {
          discardRef.current = false
          return
        }
        const duration = Math.max(0, Math.round((carriedMillisecondsRef.current + (pausedRef.current ? 0 : Date.now() - startTimeRef.current)) / 1000))
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || chunksRef.current[0]?.type || 'application/octet-stream' })
        if (blob.size === 0) {
          setRecorderState('error')
          setRecordingError('The recording is empty. Check your microphone and try recording again.')
          return
        }
        const extension = blob.type.split(';')[0].split('/')[1]?.replace(/^x-/, '') || 'audio'
        const name = `conversation-${new Date().toISOString().replace(/[:.]/g, '-')}.${extension}`
        const url = URL.createObjectURL(blob)
        setPreviewUrl(url)
        const clip = { duration, name, consentTimestamp: activeConsentRef.current }
        setClipInfo(clip)
        setElapsedSeconds(duration)
        onClipChange(clip)
        setRecorderState('ready')
      }
      recorder.start(250)
      recorderRef.current = recorder
      startTimeRef.current = Date.now()
      pausedRef.current = false
      setElapsedSeconds(0)
      setRecorderState('recording')
    } catch (error) {
      releaseTracks()
      setRecorderState('error')
      setRecordingError(permissionMessage(error))
    } finally {
      busyRef.current = false
    }
  }

  const pauseRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state !== 'recording') return
    try {
      carriedMillisecondsRef.current += Date.now() - startTimeRef.current
      pausedRef.current = true
      recorder.pause()
      setElapsedSeconds(Math.floor(carriedMillisecondsRef.current / 1000))
      setRecorderState('paused')
    } catch (error) {
      setRecorderState('error')
      setRecordingError(error instanceof Error ? `Recording could not be paused: ${error.message}` : 'Recording could not be paused.')
    }
  }

  const resumeRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state !== 'paused') return
    try {
      startTimeRef.current = Date.now()
      pausedRef.current = false
      recorder.resume()
      setRecorderState('recording')
    } catch (error) {
      setRecorderState('error')
      setRecordingError(error instanceof Error ? `Recording could not resume: ${error.message}` : 'Recording could not resume.')
    }
  }

  const stopRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') return
    setElapsedSeconds(Math.floor((carriedMillisecondsRef.current + (recorder.state === 'recording' ? Date.now() - startTimeRef.current : 0)) / 1000))
    setRecorderState('finalizing')
    try {
      recorder.stop()
    } catch (error) {
      releaseTracks()
      setRecorderState('error')
      setRecordingError(error instanceof Error ? `Recording could not stop: ${error.message}` : 'Recording could not stop safely.')
    }
  }

  const discardClip = () => {
    if (!window.confirm('Discard this recording? This cannot be undone.')) return
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      discardRef.current = true
      setRecorderState('finalizing')
      try {
        recorder.stop()
      } catch (error) {
        releaseTracks()
        setRecordingError(error instanceof Error ? `Recording could not be discarded: ${error.message}` : 'Recording could not be discarded safely.')
      }
    }
    releaseTracks()
    clearClip()
  }

  const selectAudioFile = (file?: File) => {
    if (!file) return
    setAudioError('')
    if (clipInfo && !window.confirm('Replace the current audio preview?')) return
    if (!file.type.startsWith('audio/')) {
      setAudioError('Choose a valid audio file.')
      return
    }
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      setAudioError('Stop the current recording before attaching an audio file.')
      return
    }
    clearClip()
    const url = URL.createObjectURL(file)
    setPreviewUrl(url)
    const clip = { duration: 0, name: file.name, consentTimestamp: '' }
    setClipInfo(clip)
    onClipChange(clip)
    setRecorderState('ready')
  }

  const rerecord = () => {
    if (!consentGiven) {
      setRecordingError('Confirm that everyone on the call has agreed to recording before you start again.')
      return
    }
    if (!window.confirm('Discard this recording and start a new one?')) return
    releaseTracks()
    clearClip()
    void startRecording()
  }

  const updateFileDuration = (duration: number) => {
    if (!Number.isFinite(duration)) return
    setClipInfo((previous) => {
      if (!previous) return previous
      const updated = { ...previous, duration: Math.round(duration) }
      onClipChange(updated)
      return updated
    })
  }

  const recording = recorderState === 'recording' || recorderState === 'paused'
  const hasClip = recorderState === 'ready' && clipInfo !== null && previewUrl !== ''

  // expose stop function and recording state to parent via optional refs and callback
  useEffect(() => {
    if (stopRef) stopRef.current = stopRecording
    if (isRecordingRef) isRecordingRef.current = recording
    if (typeof onRecordingChange === 'function') {
      try { onRecordingChange(recording) } catch {}
    }
  }, [recording, stopRef, isRecordingRef, onRecordingChange, stopRecording])

  return (
    <div className="recorder-content">
      <div className={`recorder-panel${recording ? ' is-recording' : ''}${hasClip ? ' has-clip' : ''}`}>
        <div className="recorder-visual">
          <div className={`microphone-orbit${recording ? ' orbit-active' : ''}`}>
            <div className="microphone-button"><Mic2 size={27} strokeWidth={1.8} /></div>
          </div>
          {recording && <span className="recording-pulse" aria-hidden="true" />}
        </div>
        <div className="recorder-copy">
          {recorderState === 'recording' ? (
            <>
              <span className="recording-live"><span /> RECORDING IN PROGRESS</span>
              <h3>Conversation is being captured</h3>
              <p>Keep this tab open until you stop and preview the recording.</p>
            </>
          ) : recorderState === 'paused' ? (
            <>
              <span className="recording-paused">RECORDING PAUSED</span>
              <h3>Take your time</h3>
              <p>Resume when the conversation is ready to continue.</p>
            </>
          ) : recorderState === 'requesting' ? (
            <>
              <span className="recording-ready-label">MICROPHONE ACCESS</span>
              <h3>Waiting for permission…</h3>
              <p>Your browser will ask for microphone access now.</p>
            </>
          ) : recorderState === 'finalizing' ? (
            <>
              <span className="recording-ready-label">SAVING LOCAL CLIP</span>
              <h3>Preparing your preview…</h3>
              <p>Finishing the recording on this device.</p>
            </>
          ) : hasClip ? (
            <>
              <span className="recording-ready-label"><Check size={13} /> PREVIEW READY</span>
              <h3>{clipInfo.name}</h3>
              <p><FileAudio2 size={14} /> {clipInfo.duration ? `${formatDuration(clipInfo.duration)} · ` : ''}Available in this browser only</p>
            </>
          ) : (
            <>
              <span className="recording-ready-label"><span className="ready-indicator" /> READY TO RECORD</span>
              <h3>Capture the conversation</h3>
              <p>Capture sales-conversation context for this enquiry, or choose a file to preview locally.</p>
            </>
          )}
          {(recording || recorderState === 'finalizing') && (
            <div className="elapsed-timer" aria-live="off">{formatDuration(elapsedSeconds)}</div>
          )}
        </div>
        <div className="recorder-controls">
          {!recording && !hasClip && (
            <button type="button" className="button button-primary record-start-button" onClick={startRecording} disabled={recorderState === 'requesting' || recorderState === 'finalizing'}>
              <Mic2 size={16} /> {recorderState === 'requesting' ? 'Requesting access…' : 'Start recording'}
            </button>
          )}
          {recorderState === 'recording' && (
            <>
              <button type="button" className="button button-secondary" onClick={pauseRecording}><Pause size={15} /> Pause</button>
              <button type="button" className="button button-stop" onClick={stopRecording}><Square size={14} fill="currentColor" /> Stop</button>
            </>
          )}
          {recorderState === 'paused' && (
            <>
              <button type="button" className="button button-secondary" onClick={resumeRecording}><Play size={15} fill="currentColor" /> Resume</button>
              <button type="button" className="button button-stop" onClick={stopRecording}><Square size={14} fill="currentColor" /> Stop</button>
            </>
          )}
          {hasClip && (
            <>
              <button type="button" className="button button-secondary" onClick={rerecord}><RotateCcw size={15} /> Record again</button>
              <button type="button" className="icon-action-button" aria-label="Discard recording" onClick={discardClip}><X size={17} /></button>
            </>
          )}
          {!hasClip && !recording && recorderState !== 'requesting' && (
            <label className="button button-upload">
              <Upload size={15} /> Choose audio file
              <input type="file" accept="audio/*" onChange={(event) => { selectAudioFile(event.target.files?.[0]); event.currentTarget.value = '' }} />
            </label>
          )}
        </div>
      </div>

      <div className="recording-consent">
        <label className="checkbox-field">
          <input type="checkbox" checked={consentGiven} onChange={(event) => setConsentGiven(event.target.checked)} disabled={recording || recorderState === 'requesting'} />
          <span>I confirm that everyone on this conversation has agreed to being recorded.</span>
        </label>
        <span className="consent-detail"><ShieldCheck size={14} /> Recording will not start until you confirm consent above.</span>
        {consentTimestamp && (
          <span className="consent-detail consent-confirmed"><Check size={14} /> Consent confirmed at {new Date(consentTimestamp).toLocaleString()}. Authenticated user and server audit are unavailable in demo mode.</span>
        )}
      </div>

      {recordingError && <div className="recorder-error" role="alert"><X size={15} /> {recordingError}</div>}
      {audioError && <div className="recorder-error" role="alert"><X size={15} /> {audioError}</div>}

      {hasClip && previewUrl && (
        <div className="audio-preview">
          <div className="audio-preview-heading"><span><AudioLines size={16} /> Audio preview</span><span className="local-only-badge">LOCAL ONLY</span></div>
          <audio
            src={previewUrl}
            controls
            preload="metadata"
            onLoadedMetadata={(event) => updateFileDuration(event.currentTarget.duration)}
            onError={() => setAudioError('This audio format could not be previewed by your browser.')}
          />
          <div className="audio-storage-notice"><AlertCircleIcon /> Audio is not uploaded or linked to the enquiry. Secure storage is not configured.</div>
          {clipInfo.consentTimestamp && <span className="consent-detail"><ShieldCheck size={13} /> Consent time held only in this page: {new Date(clipInfo.consentTimestamp).toLocaleString()}</span>}
        </div>
      )}

      {!hasClip && (
        <div className="transcription-unavailable"><span className="transcription-icon"><AudioLines size={15} /></span><div><strong>Transcription service not configured</strong><span>Audio stays on this device. No transcript or AI suggestions will be generated.</span></div></div>
      )}
    </div>
  )
}

function AlertCircleIcon() {
  return <span aria-hidden="true" className="alert-circle-icon">!</span>
}
