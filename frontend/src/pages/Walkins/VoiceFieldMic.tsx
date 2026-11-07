import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { LoaderCircle, Mic2, Square } from 'lucide-react'
import { request } from '../../services/officeApi'

type VoiceField = 'name' | 'phone' | 'email' | 'organisation' | 'requirement' | 'customerType'

async function blobToBase64(blob: Blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize))
  return btoa(binary)
}

export default function VoiceFieldMic({ field, workspaceId, memberId, onValue, disabled = false }: {
  field: VoiceField
  workspaceId: string
  memberId?: string | null
  onValue(value: string): void
  disabled?: boolean
}) {
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const audioContext = useRef<AudioContext | null>(null)
  const animationFrame = useRef<number | null>(null)
  const chunks = useRef<Blob[]>([])
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [level, setLevel] = useState(0)

  const stopMeter = () => {
    if (animationFrame.current !== null) cancelAnimationFrame(animationFrame.current)
    animationFrame.current = null
    audioContext.current?.close().catch(() => undefined)
    audioContext.current = null
    setLevel(0)
  }

  const startMeter = (captured: MediaStream) => {
    const context = new AudioContext()
    const analyser = context.createAnalyser()
    analyser.fftSize = 256
    const source = context.createMediaStreamSource(captured)
    source.connect(analyser)
    const samples = new Uint8Array(analyser.fftSize)
    audioContext.current = context
    const measure = () => {
      analyser.getByteTimeDomainData(samples)
      let sum = 0
      for (const sample of samples) { const value = (sample - 128) / 128; sum += value * value }
      setLevel(Math.min(1, Math.sqrt(sum / samples.length) * 3.2))
      animationFrame.current = requestAnimationFrame(measure)
    }
    measure()
  }

  const stopStream = () => {
    stopMeter()
    stream.current?.getTracks().forEach(track => track.stop())
    stream.current = null
  }

  const finish = async () => {
    const current = recorder.current
    if (!current || current.state === 'inactive') return
    current.stop()
    setRecording(false)
  }

  const start = async () => {
    if (disabled || recording || busy) return
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('Microphone recording is not supported in this browser.')
      return
    }
    setError('')
    try {
      const captured = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.current = captured
      chunks.current = []
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported(type)) || ''
      const current = new MediaRecorder(captured, mimeType ? { mimeType } : undefined)
      recorder.current = current
      startMeter(captured)
      current.ondataavailable = event => { if (event.data.size) chunks.current.push(event.data) }
      current.onerror = () => { setError('The microphone could not be read. Try again.'); stopStream(); setRecording(false) }
      current.onstop = () => {
        const blob = new Blob(chunks.current, { type: current.mimeType || 'audio/webm' })
        stopStream()
        if (!blob.size) { setError('No speech was captured. Try again.'); return }
        setBusy(true)
        void blobToBase64(blob).then(audioBase64 => request<{ value: string }>('/walkins/voice-field', workspaceId, { method: 'POST', body: JSON.stringify({ field, audioBase64, contentType: blob.type || 'audio/webm' }) }, memberId))
          .then(result => onValue(result.value))
          .catch(reason => setError(reason instanceof Error ? reason.message : 'Voice extraction failed. Try again.'))
          .finally(() => setBusy(false))
      }
      current.start()
      setRecording(true)
    } catch {
      stopStream()
      setError('Microphone permission is required to answer by voice.')
    }
  }

  useEffect(() => () => { stopStream() }, [])

  return <span className="walkin-voice-field-control">
    <button type="button" className={`walkin-field-mic${recording ? ' is-recording' : ''}`} style={{ '--mic-level': level } as CSSProperties} aria-label={recording ? 'Stop voice answer' : `Answer ${field} by voice`} title={recording ? 'Stop and extract answer' : 'Answer by voice'} disabled={disabled || busy} onClick={() => void (recording ? finish() : start())}>
      {busy ? <LoaderCircle size={15} className="walkin-mic-spin" /> : recording ? <Square size={13} fill="currentColor" /> : <Mic2 size={15} />}
    </button>
    {error && <span className="walkin-voice-field-error" role="status">{error}</span>}
  </span>
}
