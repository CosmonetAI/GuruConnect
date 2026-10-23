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

/** Plays the server's complete, chunk-joined WAV as one AudioBuffer. */
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
      const audioContext = new AudioContext()
      context.current = audioContext
      await audioContext.resume()
      playbackStart.current = startSeconds
      const wav = await response.arrayBuffer()
      if (currentRun !== run.current) return
      const buffer = await audioContext.decodeAudioData(wav)
      if (!buffer.length) throw new Error('The recording contains no playable audio.')
      const source = audioContext.createBufferSource()
      source.buffer = buffer; source.connect(audioContext.destination)
      const startAt = audioContext.currentTime + 0.08
      const total = buffer.duration
      setDurationSeconds(startSeconds + total)
      clockStart.current = startAt - startSeconds
      source.onended = () => {
        sources.current.delete(source)
        if (currentRun === run.current) { setStatus('ended'); onTimeChange?.(startSeconds + total) }
      }
      sources.current.add(source)
      source.start(startAt)
      setStatus('playing')
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
