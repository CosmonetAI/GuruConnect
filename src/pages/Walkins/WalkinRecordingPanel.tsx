import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AudioLines,
  Check,
  Mic2,
  Pause,
  Play,
  Save,
  Square,
  Trash2,
} from 'lucide-react'
import { listWalkinRecordings, saveWalkinRecording } from '../../services/walkinService'
import type { WalkinRecording } from '../../types/walkin'

type RecorderState = 'idle' | 'requesting' | 'recording' | 'paused' | 'finalizing' | 'review' | 'error'
type MeterState = 'waiting' | 'silence' | 'low' | 'good' | 'high' | 'missing'

type SpeechRecognitionResult = {
  isFinal: boolean
  0: { transcript: string }
}

type SpeechRecognitionEvent = Event & {
  resultIndex: number
  results: ArrayLike<SpeechRecognitionResult>
}

type SpeechRecognitionErrorEvent = Event & {
  error: string
}

type SpeechRecognitionLike = {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: SpeechRecognitionEvent) => void) | null
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike
type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
  AudioContext?: typeof AudioContext
  webkitAudioContext?: typeof AudioContext
}

const LOW_VOLUME_THRESHOLD = 0.035
const SILENCE_THRESHOLD = 0.003
const LOW_VOLUME_WARNING_DELAY_MS = 3000

function formatDuration(seconds: number) {
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function appendTranscript(current: string, addition: string) {
  const cleaned = addition.trim()
  if (!cleaned) return current
  return current ? `${current.trimEnd()} ${cleaned}` : cleaned
}

function microphoneError(error: unknown) {
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

function speechError(error: string) {
  if (error === 'not-allowed' || error === 'service-not-allowed') {
    return 'Speech recognition was blocked. Allow speech recognition in your browser settings or enter the transcript manually.'
  }
  if (error === 'network') return 'Speech recognition lost its network connection. You can continue recording and edit the transcript manually.'
  if (error === 'audio-capture') return 'Speech recognition could not access the microphone. Check your microphone or enter the transcript manually.'
  return 'Live speech recognition is unavailable. You can enter the transcript manually.'
}

export default function WalkinRecordingPanel({
  visitId,
  customerName,
  disabled = false,
}: {
  visitId: string
  customerName: string
  disabled?: boolean
}) {
  const [recordings, setRecordings] = useState<WalkinRecording[]>([])
  const [recorderState, setRecorderState] = useState<RecorderState>('idle')
  const [transcript, setTranscript] = useState('')
  const [interimTranscript, setInterimTranscript] = useState('')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [meterLevel, setMeterLevel] = useState(0)
  const [meterState, setMeterState] = useState<MeterState>('waiting')
  const [consentGiven, setConsentGiven] = useState(false)
  const [startedAt, setStartedAt] = useState('')
  const [endedAt, setEndedAt] = useState('')
  const [consentAt, setConsentAt] = useState('')
  const [error, setError] = useState('')
  const [speechNotice, setSpeechNotice] = useState('')
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const recordingActiveRef = useRef(false)
  const pausedRef = useRef(false)
  const speechFailedRef = useRef(false)
  const finalTranscriptRef = useRef('')
  const startClockRef = useRef(0)
  const carriedMillisecondsRef = useRef(0)
  const lowVolumeSinceRef = useRef<number | null>(null)
  const smoothedLevelRef = useRef(0)
  const animationFrameRef = useRef(0)
  const finalizeTimerRef = useRef(0)
  const speechFinishedRef = useRef(true)
  const mediaFinishedRef = useRef(true)
  const recordingIdRef = useRef('')
  const mountedRef = useRef(true)
  const chunksRef = useRef<Blob[]>([])

  const refreshRecordings = useCallback(async () => {
    try {
      setRecordings(await listWalkinRecordings(visitId))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Saved recordings could not be loaded.')
    }
  }, [visitId])

  useEffect(() => {
    mountedRef.current = true
    void refreshRecordings()
    return () => {
      mountedRef.current = false
      recordingActiveRef.current = false
      window.cancelAnimationFrame(animationFrameRef.current)
      window.clearTimeout(finalizeTimerRef.current)
      const recognition = recognitionRef.current
      recognitionRef.current = null
      if (recognition) {
        recognition.onend = null
        recognition.onresult = null
        recognition.onerror = null
        recognition.abort()
      }
      const recorder = recorderRef.current
      if (recorder) {
        recorder.ondataavailable = null
        recorder.onstop = null
        recorder.onerror = null
        if (recorder.state !== 'inactive') {
          try {
            recorder.stop()
          } catch {
            // Tracks are released below even when the browser has already stopped the recorder.
          }
        }
      }
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      const audioContext = audioContextRef.current
      audioContextRef.current = null
      if (audioContext && audioContext.state !== 'closed') void audioContext.close()
    }
  }, [refreshRecordings])

  useEffect(() => {
    if (recorderState !== 'recording') return
    const tick = () => {
      const analyser = analyserRef.current
      const track = streamRef.current?.getAudioTracks()[0]
      if (track && (track.readyState !== 'live' || track.muted)) {
        setMeterState('missing')
        setMeterLevel(0)
      } else if (analyser) {
        const samples = new Float32Array(analyser.fftSize)
        analyser.getFloatTimeDomainData(samples)
        const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length)
        const smoothed = smoothedLevelRef.current * 0.75 + rms * 0.25
        smoothedLevelRef.current = smoothed
        setMeterLevel(Math.min(100, Math.round(smoothed * 300)))
        if (smoothed >= LOW_VOLUME_THRESHOLD) {
          lowVolumeSinceRef.current = null
          setMeterState(smoothed >= 0.25 ? 'high' : 'good')
        } else if (smoothed >= SILENCE_THRESHOLD) {
          lowVolumeSinceRef.current ??= Date.now()
          setMeterState(Date.now() - lowVolumeSinceRef.current >= LOW_VOLUME_WARNING_DELAY_MS ? 'low' : 'silence')
        } else {
          lowVolumeSinceRef.current = null
          setMeterState('silence')
        }
      }
      setElapsedSeconds(Math.floor((carriedMillisecondsRef.current + Date.now() - startClockRef.current) / 1000))
      animationFrameRef.current = window.requestAnimationFrame(tick)
    }
    animationFrameRef.current = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(animationFrameRef.current)
  }, [recorderState])

  const updateTranscript = (value: string) => {
    finalTranscriptRef.current = value
    setTranscript(value)
  }

  const startSpeechRecognition = (recognition: SpeechRecognitionLike) => {
    try {
      recognition.start()
      speechFinishedRef.current = false
    } catch {
      setSpeechNotice('Live speech recognition could not start. You can still record and enter the transcript manually.')
      speechFailedRef.current = true
      speechFinishedRef.current = true
    }
  }

  const startRecording = async () => {
    if (disabled || recorderState === 'recording' || recorderState === 'paused' || recorderState === 'requesting') return
    if (!consentGiven) {
      setError('Confirm that the customer and receptionist have agreed to recording and browser-based speech recognition before starting.')
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setRecorderState('error')
      setError('Audio recording is not supported in this browser. Try a supported, secure browser or enter the transcript manually.')
      return
    }

    setError('')
    setMessage('')
    setSpeechNotice('')
    setRecorderState('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const recorder = new MediaRecorder(stream)
      const now = new Date().toISOString()
      recordingIdRef.current = crypto.randomUUID()
      setStartedAt(now)
      setEndedAt('')
      setConsentAt(now)
      setTranscript('')
      setInterimTranscript('')
      finalTranscriptRef.current = ''
      carriedMillisecondsRef.current = 0
      startClockRef.current = Date.now()
      pausedRef.current = false
      speechFailedRef.current = false
      lowVolumeSinceRef.current = null
      smoothedLevelRef.current = 0
      chunksRef.current = []
      mediaFinishedRef.current = false
      speechFinishedRef.current = true

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onerror = () => {
        let stopError = ''
        recordingActiveRef.current = false
        speechFailedRef.current = true
        speechFinishedRef.current = true
        mediaFinishedRef.current = true
        const recognition = recognitionRef.current
        recognitionRef.current = null
        if (recognition) {
          recognition.onend = null
          recognition.onresult = null
          recognition.onerror = null
          recognition.abort()
        }
        recorder.ondataavailable = null
        recorder.onstop = null
        recorder.onerror = null
        if (recorder.state !== 'inactive') {
          try {
            recorder.stop()
          } catch (reason) {
            stopError = reason instanceof Error
              ? `The browser could not safely stop the audio recorder: ${reason.message}`
              : 'The browser could not safely stop the audio recorder. Close this visit to release the microphone.'
          }
        }
        streamRef.current?.getTracks().forEach((track) => track.stop())
        streamRef.current = null
        const context = audioContextRef.current
        audioContextRef.current = null
        analyserRef.current = null
        if (context && context.state !== 'closed') void context.close()
        chunksRef.current = []
        setError(stopError || 'The browser encountered an error while recording. Try again or enter the transcript manually.')
        setRecorderState('error')
      }
      recorder.onstop = () => {
        mediaFinishedRef.current = true
        const capturedAudio = new Blob(chunksRef.current, { type: recorder.mimeType || 'application/octet-stream' })
        chunksRef.current = []
        if (capturedAudio.size === 0 && mountedRef.current) {
          setError('The recording is empty. Check your microphone and try recording again.')
        }
        streamRef.current?.getTracks().forEach((track) => track.stop())
        streamRef.current = null
        const context = audioContextRef.current
        audioContextRef.current = null
        if (context && context.state !== 'closed') void context.close()
        if (mountedRef.current && speechFinishedRef.current) {
          window.clearTimeout(finalizeTimerRef.current)
          setRecorderState('review')
        }
      }
      recorder.start(250)
      recorderRef.current = recorder

      const view = window as SpeechRecognitionWindow
      const Recognition = view.SpeechRecognition ?? view.webkitSpeechRecognition
      if (Recognition) {
        const recognition = new Recognition()
        recognition.continuous = true
        recognition.interimResults = true
        recognition.lang = 'en-IN'
        recognition.onresult = (event) => {
          let newlyFinalized = ''
          let interim = ''
          for (let index = event.resultIndex; index < event.results.length; index += 1) {
            const result = event.results[index]
            const phrase = result[0]?.transcript ?? ''
            if (result.isFinal) newlyFinalized = appendTranscript(newlyFinalized, phrase)
            else interim = appendTranscript(interim, phrase)
          }
          if (newlyFinalized) {
            finalTranscriptRef.current = appendTranscript(finalTranscriptRef.current, newlyFinalized)
            setTranscript(finalTranscriptRef.current)
          }
          setInterimTranscript(interim)
        }
        recognition.onerror = (event) => {
          if (event.error === 'no-speech' || event.error === 'aborted') return
          speechFailedRef.current = true
          speechFinishedRef.current = true
          setSpeechNotice(speechError(event.error))
          if (mediaFinishedRef.current && mountedRef.current) setRecorderState('review')
        }
        recognition.onend = () => {
          setInterimTranscript('')
          if (recordingActiveRef.current && !pausedRef.current && !speechFailedRef.current) {
            window.setTimeout(() => {
              if (recordingActiveRef.current && !pausedRef.current) startSpeechRecognition(recognition)
            }, 150)
          } else {
            speechFinishedRef.current = true
            if (mediaFinishedRef.current && mountedRef.current) {
              window.clearTimeout(finalizeTimerRef.current)
              setRecorderState('review')
            }
          }
        }
        recognitionRef.current = recognition
        startSpeechRecognition(recognition)
      } else {
        setSpeechNotice('Live speech recognition is not supported in this browser. You can record and enter the transcript manually.')
      }

      const AudioContextConstructor = view.AudioContext ?? view.webkitAudioContext
      if (AudioContextConstructor) {
        try {
          const audioContext = new AudioContextConstructor()
          void audioContext.resume().catch(() => {
            setSpeechNotice('The microphone is available, but audio level monitoring could not start.')
          })
          const source = audioContext.createMediaStreamSource(stream)
          const analyser = audioContext.createAnalyser()
          analyser.fftSize = 512
          source.connect(analyser)
          audioContextRef.current = audioContext
          analyserRef.current = analyser
        } catch {
          setSpeechNotice((previous) => previous || 'Audio level monitoring is unavailable in this browser.')
        }
      } else {
        setSpeechNotice((previous) => previous || 'Audio level monitoring is unavailable in this browser.')
      }
      recordingActiveRef.current = true
      mediaFinishedRef.current = false
      setMeterState('waiting')
      setRecorderState('recording')
      setConsentGiven(false)
    } catch (reason) {
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      setRecorderState('error')
      setError(microphoneError(reason))
    }
  }

  const pauseRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state !== 'recording') return
    carriedMillisecondsRef.current += Date.now() - startClockRef.current
    pausedRef.current = true
    recordingActiveRef.current = false
    try {
      recorder.pause()
    } catch (reason) {
      setError(reason instanceof Error ? `Recording could not be paused: ${reason.message}` : 'Recording could not be paused.')
      setRecorderState('error')
      return
    }
    try {
      recognitionRef.current?.stop()
    } catch {
      speechFinishedRef.current = true
      setSpeechNotice('Speech recognition could not be finalized. Review and edit the transcript before saving.')
    }
    setElapsedSeconds(Math.floor(carriedMillisecondsRef.current / 1000))
    setMeterState('waiting')
    setRecorderState('paused')
  }

  const resumeRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state !== 'paused') return
    try {
      recorder.resume()
    } catch (reason) {
      setError(reason instanceof Error ? `Recording could not resume: ${reason.message}` : 'Recording could not resume.')
      setRecorderState('error')
      return
    }
    pausedRef.current = false
    recordingActiveRef.current = true
    startClockRef.current = Date.now()
    const recognition = recognitionRef.current
    if (recognition && !speechFailedRef.current) startSpeechRecognition(recognition)
    setRecorderState('recording')
  }

  const stopRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') return
    if (!window.confirm('Stop recording and finalize the transcript? You can still review and save the transcript afterward.')) return
    setEndedAt(new Date().toISOString())
    if (recorder.state === 'recording') {
      carriedMillisecondsRef.current += Date.now() - startClockRef.current
    }
    setElapsedSeconds(Math.floor(carriedMillisecondsRef.current / 1000))
    recordingActiveRef.current = false
    pausedRef.current = false
    mediaFinishedRef.current = false
    speechFinishedRef.current = !recognitionRef.current || speechFailedRef.current
    setRecorderState('finalizing')
    recognitionRef.current?.stop()
    try {
      recorder.stop()
    } catch (reason) {
      setError(reason instanceof Error ? `Recording could not be stopped: ${reason.message}` : 'Recording could not be stopped safely.')
      window.clearTimeout(finalizeTimerRef.current)
      setRecorderState('error')
    }
    window.clearTimeout(finalizeTimerRef.current)
    const stoppedRecordingId = recordingIdRef.current
    finalizeTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current && recordingIdRef.current === stoppedRecordingId) {
        speechFinishedRef.current = true
        setRecorderState('review')
      }
    }, 5000)
  }

  const cancelRecording = () => {
    const hasUnsavedContent = recorderState !== 'idle' || transcript.trim().length > 0 || interimTranscript.trim().length > 0
    if (hasUnsavedContent && !window.confirm('Discard this recording and transcript? This content has not been saved.')) return
    recordingActiveRef.current = false
    pausedRef.current = false
    speechFailedRef.current = true
    const recognition = recognitionRef.current
    recognitionRef.current = null
    if (recognition) {
      recognition.onend = null
      recognition.onresult = null
      recognition.onerror = null
      recognition.abort()
    }
    const recorder = recorderRef.current
    if (recorder) {
      recorder.ondataavailable = null
      recorder.onstop = null
      recorder.onerror = null
    }
    if (recorder && recorder.state !== 'inactive') {
      try {
        recorder.stop()
      } catch (reason) {
        setError(reason instanceof Error ? `Recording could not be discarded: ${reason.message}` : 'Recording could not be discarded safely.')
      }
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    const context = audioContextRef.current
    audioContextRef.current = null
    analyserRef.current = null
    if (context && context.state !== 'closed') void context.close()
    chunksRef.current = []
    recorderRef.current = null
    mediaFinishedRef.current = true
    speechFinishedRef.current = true
    window.clearTimeout(finalizeTimerRef.current)
    recordingIdRef.current = ''
    finalTranscriptRef.current = ''
    setTranscript('')
    setInterimTranscript('')
    setStartedAt('')
    setEndedAt('')
    setConsentAt('')
    setElapsedSeconds(0)
    setMeterLevel(0)
    setMeterState('waiting')
    setSpeechNotice('')
    setError('')
    setRecorderState('idle')
  }

  const saveCurrentRecording = async () => {
    if (!transcript.trim() || !startedAt || !endedAt || saving) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const saved = await saveWalkinRecording({
        id: recordingIdRef.current,
        visitId,
        transcript,
        startedAt,
        endedAt,
        durationSeconds: elapsedSeconds,
        status: 'saved',
        audioStoragePath: null,
        consentAt,
        createdBy: '',
        createdAt: '',
        updatedAt: '',
      })
      setRecordings((previous) => [saved, ...previous.filter((recording) => recording.id !== saved.id)])
      setTranscript('')
      finalTranscriptRef.current = ''
      recordingIdRef.current = ''
      setRecorderState('idle')
      setMessage('Transcript and recording details saved to this visit. Audio was not uploaded or retained.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Recording transcript could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  const updateSavedTranscript = (recording: WalkinRecording, value: string) => {
    setRecordings((previous) => previous.map((item) =>
      item.id === recording.id ? { ...item, transcript: value } : item,
    ))
  }

  const saveEditedTranscript = async (recording: WalkinRecording) => {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const saved = await saveWalkinRecording(recording)
      setRecordings((previous) => previous.map((item) => item.id === saved.id ? saved : item))
      setMessage('Transcript changes saved.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Transcript changes could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  const clearTranscript = () => {
    if (!window.confirm('Clear the current transcript? This does not affect previously saved recordings.')) return
    updateTranscript('')
    setInterimTranscript('')
    setError('')
  }

  const meterMessage = meterState === 'missing'
    ? 'No microphone input detected. Please check your microphone and speak again.'
    : meterState === 'low'
      ? 'Your voice is too low. Please increase your speaking volume or move closer to the microphone.'
      : meterState === 'good'
        ? 'Audio level is good. Continue speaking.'
        : meterState === 'high'
          ? 'Audio level is high. Move slightly farther from the microphone to avoid distortion.'
          : meterState === 'silence'
            ? 'Waiting for speech…'
            : 'Speak to check the microphone level.'
  const active = recorderState === 'recording' || recorderState === 'paused'
  const canSave = recorderState === 'review' && !!transcript.trim() && !!startedAt && !!endedAt

  return (
    <section className="walkin-card walkin-recording-card" aria-labelledby="walkin-recording-title">
      <div className="walkin-card-heading">
        <div>
          <h2 id="walkin-recording-title">Audio recording &amp; transcript</h2>
          <p>Capture the conversation for {customerName} and save its transcript to this visit.</p>
        </div>
        <span className="walkin-card-icon"><AudioLines size={18} /></span>
      </div>

      <div className={`walkin-recording-status${recorderState === 'recording' ? ' is-recording' : ''}`} role="status" aria-live="polite">
        <span className="walkin-recording-indicator" aria-hidden="true" />
        {recorderState === 'recording' && <Mic2 className="walkin-recording-mic" size={15} aria-hidden="true" />}
        <strong>{recorderState === 'recording' ? 'Recording' : recorderState === 'paused' ? 'Paused' : recorderState === 'requesting' ? 'Requesting microphone access' : recorderState === 'finalizing' ? 'Finalizing transcript' : recorderState === 'review' ? 'Ready to review' : recorderState === 'error' ? 'Recording unavailable' : 'Ready to record'}</strong>
        {(active || recorderState === 'finalizing' || recorderState === 'review') && <span className="walkin-recording-timer">{formatDuration(elapsedSeconds)}</span>}
      </div>

      <div className="walkin-recording-controls">
        {(recorderState === 'idle' || recorderState === 'error') && (
          <button className="walkin-button primary" type="button" onClick={() => void startRecording()} disabled={disabled}>
            <Mic2 size={16} /> Start recording
          </button>
        )}
        {recorderState === 'requesting' && <button className="walkin-button secondary" type="button" disabled>Waiting for permission…</button>}
        {recorderState === 'recording' && (
          <>
            <button className="walkin-button secondary" type="button" onClick={pauseRecording}><Pause size={15} /> Pause</button>
            <button className="walkin-button walkin-stop-button" type="button" onClick={stopRecording}><Square size={14} fill="currentColor" /> Stop</button>
          </>
        )}
        {recorderState === 'paused' && (
          <>
            <button className="walkin-button secondary" type="button" onClick={resumeRecording}><Play size={15} fill="currentColor" /> Resume</button>
            <button className="walkin-button walkin-stop-button" type="button" onClick={stopRecording}><Square size={14} fill="currentColor" /> Stop</button>
          </>
        )}
        {recorderState === 'review' && (
          <button className="walkin-button primary" type="button" onClick={() => void saveCurrentRecording()} disabled={!canSave || saving || disabled}>
            <Save size={16} /> {saving ? 'Saving…' : 'Save Recording & Transcript'}
          </button>
        )}
        {(active || recorderState === 'finalizing' || recorderState === 'review' || recorderState === 'error') && (
          <button className="walkin-button secondary" type="button" onClick={cancelRecording}><Trash2 size={15} /> Cancel</button>
        )}
        {recorderState === 'review' && (
          <button className="walkin-button secondary" type="button" onClick={clearTranscript}><Trash2 size={15} /> Clear transcript</button>
        )}
      </div>

      {(recorderState === 'idle' || recorderState === 'requesting') && (
        <label className="walkin-consent walkin-recording-consent">
          <input type="checkbox" checked={consentGiven} onChange={(event) => setConsentGiven(event.target.checked)} disabled={active || recorderState === 'requesting' || disabled} />
          <span>I confirm the customer and receptionist have agreed to recording and browser-based speech recognition. Recognition may be processed by the browser’s speech service; no audio is saved by this app.</span>
        </label>
      )}

      {active && (
        <div className={`walkin-volume-meter meter-${meterState}`} aria-label={`Microphone audio level: ${meterState}`}>
          <div className="walkin-volume-meter-heading"><strong>Microphone level</strong><span>{meterState === 'good' ? 'Good' : meterState === 'low' ? 'Too low' : meterState === 'high' ? 'High' : meterState === 'missing' ? 'No input' : meterState === 'silence' ? 'Listening' : 'Waiting'}</span></div>
          <div className="walkin-volume-track" aria-hidden="true"><span style={{ width: `${meterLevel}%` }} /></div>
          <p className={meterState === 'low' || meterState === 'missing' ? 'volume-warning' : ''}>{meterMessage}</p>
        </div>
      )}

      {error && <div className="walkin-alert error" role="alert">{error}</div>}
      {speechNotice && <div className="walkin-notice" role="status">{speechNotice}</div>}
      {message && <div className="walkin-alert success" role="status">{message}</div>}

      <div className="walkin-transcript">
        <div className="walkin-transcript-heading"><strong>Live transcript</strong>{active && <span className="walkin-transcript-live"><span /> Live</span>}</div>
        {active ? (
          <div className="walkin-live-transcript" aria-live="polite" aria-label="Live speech transcript">
            <span>{transcript || 'Finalized speech will appear here.'}</span>
            {interimTranscript && <em>{interimTranscript}</em>}
          </div>
        ) : recorderState === 'review' ? (
          <>
            <label className="sr-only" htmlFor="walkin-recording-transcript">Review and edit transcript</label>
            <textarea id="walkin-recording-transcript" rows={5} value={transcript} onChange={(event) => updateTranscript(event.target.value)} disabled={disabled} placeholder="Review or enter the conversation transcript…" />
          </>
        ) : (
          <div className="walkin-transcript-empty">{recordings.length ? 'Select a saved transcript below or start a new recording.' : 'Start a recording to see the recognized words here. You can review and edit the transcript before saving.'}</div>
        )}
      </div>

      {recordings.length > 0 && (
        <div className="walkin-saved-recordings">
          <h3>Saved transcripts</h3>
          {recordings.map((recording) => (
            <article className="walkin-saved-recording" key={recording.id}>
              <div className="walkin-saved-recording-heading">
                <strong>{new Date(recording.startedAt).toLocaleString()}</strong>
                <span>{formatDuration(recording.durationSeconds)} · Saved {new Date(recording.createdAt).toLocaleString()}</span>
              </div>
              <textarea
                rows={4}
                aria-label={`Transcript recorded ${new Date(recording.startedAt).toLocaleString()}`}
                value={recording.transcript}
                onChange={(event) => updateSavedTranscript(recording, event.target.value)}
                disabled={disabled}
              />
              {!disabled && (
                <button className="walkin-button secondary" type="button" onClick={() => void saveEditedTranscript(recording)} disabled={saving}>
                  <Check size={15} /> Save transcript changes
                </button>
              )}
            </article>
          ))}
        </div>
      )}
      {recorderState === 'review' && startedAt && (
        <p className="walkin-recording-footnote">Started {new Date(startedAt).toLocaleString()} · Ended {endedAt ? new Date(endedAt).toLocaleString() : '—'} · Audio is held only temporarily in this tab and is discarded when you save or cancel.</p>
      )}
    </section>
  )
}
