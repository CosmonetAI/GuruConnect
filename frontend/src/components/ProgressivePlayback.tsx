import { useEffect, useRef, useState } from 'react'
import { Play, RefreshCw, Square } from 'lucide-react'
import { requestStream } from '../services/officeApi'

type Props = {
  conversationId: string
  workspaceId: string
  startSeconds?: number
  onTimeChange?: (seconds: number) => void
  compact?: boolean
}

type Status = 'idle' | 'loading' | 'playing' | 'ended'

function append(left: Uint8Array, right: Uint8Array) {
  const value = new Uint8Array(left.length + right.length)
  value.set(left); value.set(right, left.length)
  return value
}

/** Plays the continuous PCM stream without waiting for the full recording. */
export default function ProgressivePlayback({ conversationId, workspaceId, startSeconds = 0, onTimeChange, compact = false }: Props) {
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState('')
  const [durationSeconds, setDurationSeconds] = useState(0)
  const [progress, setProgress] = useState(0)
  const run = useRef(0)
  const abort = useRef<AbortController | null>(null)
  const context = useRef<AudioContext | null>(null)
  const sources = useRef(new Set<AudioBufferSourceNode>())
  const clockStart = useRef(0)
  const playbackStart = useRef(startSeconds)
  const nextStart = useRef(0)
  const streamDone = useRef(false)

  function stop() {
    run.current += 1
    abort.current?.abort()
    abort.current = null
    sources.current.forEach(source => { try { source.stop() } catch { /* already ended */ } })
    sources.current.clear()
    const current = context.current
    context.current = null
    if (current) void current.close()
    setStatus('idle'); setProgress(0); onTimeChange?.(-1)
  }

  useEffect(() => () => stop(), [])
  useEffect(() => {
    if (status !== 'playing' || !context.current) return
    const timer = window.setInterval(() => {
      const current = Math.max(0, context.current!.currentTime - clockStart.current)
      const value = Math.min(durationSeconds || current, playbackStart.current + current)
      setProgress(value); onTimeChange?.(value)
    }, 250)
    return () => window.clearInterval(timer)
  }, [status, durationSeconds, onTimeChange])

  async function play() {
    if (status === 'loading' || status === 'playing') return
    stop()
    const currentRun = run.current
    const controller = new AbortController()
    abort.current = controller
    setError(''); setProgress(startSeconds); setStatus('loading')
    try {
      const sample = Math.max(0, Math.floor(startSeconds * 16000))
      const response = await requestStream(`/conversations/${conversationId}/playback/stream?start_sample=${sample}`, workspaceId, { signal: controller.signal })
      if (!response.body) throw new Error('The recording stream is unavailable.')
      const contentLength = Number(response.headers.get('Content-Length') || 0)
      const total = contentLength > 44 ? (contentLength - 44) / 2 / 16000 : 0
      setDurationSeconds(startSeconds + total)
      const audioContext = new AudioContext()
      context.current = audioContext
      await audioContext.resume()
      playbackStart.current = startSeconds
      nextStart.current = 0
      streamDone.current = false
      let received = new Uint8Array()
      let headerRead = false
      let firstScheduled = false
      const reader = response.body.getReader()
      const schedule = (pcm: Uint8Array) => {
        const samples = Math.floor(pcm.byteLength / 2)
        if (!samples || currentRun !== run.current) return
        const buffer = audioContext.createBuffer(1, samples, 16000)
        const channel = buffer.getChannelData(0)
        const view = new DataView(pcm.buffer, pcm.byteOffset, samples * 2)
        for (let index = 0; index < samples; index += 1) channel[index] = view.getInt16(index * 2, true) / 32768
        const source = audioContext.createBufferSource()
        source.buffer = buffer; source.connect(audioContext.destination)
        const startAt = Math.max(nextStart.current, audioContext.currentTime + (firstScheduled ? 0 : 0.08))
        source.onended = () => {
          sources.current.delete(source)
          if (streamDone.current && sources.current.size === 0 && currentRun === run.current) {
            setStatus('ended'); onTimeChange?.(startSeconds + total)
          }
        }
        sources.current.add(source); source.start(startAt)
        if (!firstScheduled) {
          firstScheduled = true; clockStart.current = startAt - startSeconds; setStatus('playing')
        }
        nextStart.current = startAt + buffer.duration
      }
      while (true) {
        const result = await reader.read()
        if (result.done) break
        if (currentRun !== run.current) return
        received = append(received, result.value)
        if (!headerRead) {
          if (received.length < 44) continue
          if (String.fromCharCode(...received.slice(0, 4)) !== 'RIFF' || String.fromCharCode(...received.slice(8, 12)) !== 'WAVE') throw new Error('The recording format is invalid.')
          received = received.slice(44); headerRead = true
        }
        const aligned = received.length - (received.length % 2)
        if (aligned) { schedule(received.slice(0, aligned)); received = received.slice(aligned) }
      }
      if (received.length) schedule(received)
      streamDone.current = true
      if (!firstScheduled) throw new Error('The recording contains no playable audio.')
    } catch (cause) {
      if (controller.signal.aborted || currentRun !== run.current) return
      setStatus('idle'); setError(cause instanceof Error ? cause.message : 'Playback is unavailable.')
      stop()
    } finally {
      if (abort.current === controller) abort.current = null
    }
  }

  const active = status === 'loading' || status === 'playing'
  return <div className={`progressive-playback${compact ? ' is-compact' : ''}`}>
    <button type="button" className="button button-secondary" onClick={() => active ? stop() : void play()}>
      {active ? <Square size={15} /> : status === 'ended' ? <RefreshCw size={15} /> : <Play size={15} />}
      {status === 'loading' ? 'Starting playback…' : active ? 'Stop playback' : status === 'ended' ? 'Play again' : 'Play recording'}
    </button>
    {durationSeconds > 0 && <progress max={durationSeconds} value={Math.min(durationSeconds, progress)} aria-label="Playback progress" />}
    {error && <small className="recording-error">{error}</small>}
  </div>
}
