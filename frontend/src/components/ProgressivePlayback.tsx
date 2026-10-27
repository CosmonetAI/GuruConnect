import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Play, Square } from 'lucide-react'
import { requestStream } from '../services/officeApi'

export interface PlaybackHandle { playAt(seconds: number): Promise<void> }
type Props = { conversationId: string; workspaceId: string; memberId?: string; startSeconds?: number; onTimeChange?: (seconds: number) => void; compact?: boolean }

/** Authenticated media fetch; native playback provides pause, scrubbing and timing.
 * Keep the WAV as a Blob instead of duplicating the full recording into decoded JS PCM.
 */
const ProgressivePlayback = forwardRef<PlaybackHandle, Props>(function ProgressivePlayback({ conversationId, workspaceId, memberId, startSeconds = 0, onTimeChange, compact = false }, ref) {
  const [status, setStatus] = useState<'idle' | 'loading' | 'playing' | 'paused'>('idle')
  const [error, setError] = useState('')
  const [url, setUrl] = useState('')
  const audio = useRef<HTMLAudioElement>(null)
  const objectUrl = useRef('')
  const controller = useRef<AbortController | null>(null)
  const epoch = useRef(0)
  const target = useRef(startSeconds)
  const autoplay = useRef(false)
  const callback = useRef(onTimeChange); callback.current = onTimeChange
  useEffect(() => {
    setUrl(''); setStatus('idle'); setError('')
    return () => {
      ++epoch.current; controller.current?.abort(); audio.current?.pause()
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
      objectUrl.current = ''
    }
  }, [conversationId, workspaceId, memberId])
  async function playAt(seconds: number) {
    target.current = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
    autoplay.current = true
    if (objectUrl.current && audio.current) {
      audio.current.currentTime = target.current
      try { await audio.current.play() } catch { setError('Use the audio controls to start playback.') }
      return
    }
    if (controller.current) return
    const ticket = epoch.current
    const abort = new AbortController(); controller.current = abort
    setStatus('loading'); setError('')
    try {
      const response = await requestStream(`/conversations/${encodeURIComponent(conversationId)}/playback/stream?start_sample=0`, workspaceId, { signal: abort.signal }, memberId)
      const blob = await response.blob()
      if (abort.signal.aborted || ticket !== epoch.current) return
      if (!blob.size) throw new Error('The recording contains no playable audio.')
      objectUrl.current = URL.createObjectURL(blob); setUrl(objectUrl.current); setStatus('paused')
    } catch (cause) { if (!abort.signal.aborted && ticket === epoch.current) { setStatus('idle'); setError(cause instanceof Error ? cause.message : 'Playback is unavailable.') } }
    finally { if (controller.current === abort) controller.current = null }
  }
  useImperativeHandle(ref, () => ({ playAt }))
  function stop() {
    ++epoch.current; controller.current?.abort(); controller.current = null; autoplay.current = false
    audio.current?.pause(); if (audio.current) audio.current.currentTime = 0
    setStatus(objectUrl.current ? 'paused' : 'idle'); callback.current?.(-1)
  }
  return <div className={`progressive-playback${compact ? ' is-compact' : ''}`}>
    <button type="button" className="button button-secondary" onClick={() => status === 'loading' || status === 'playing' ? stop() : void playAt(audio.current?.currentTime || startSeconds)}>{status === 'loading' || status === 'playing' ? <Square size={15} /> : <Play size={15} />}{status === 'loading' ? 'Cancel playback' : status === 'playing' ? 'Stop playback' : 'Play recording'}</button>
    {url && <audio ref={audio} controls preload="metadata" src={url} aria-label="Conversation source audio" onLoadedMetadata={() => {
      if (!audio.current) return
      audio.current.currentTime = Math.min(target.current, Number.isFinite(audio.current.duration) ? audio.current.duration : target.current)
      if (autoplay.current) { autoplay.current = false; void audio.current.play().catch(() => setError('Use the audio controls to start playback.')) }
    }} onPlay={() => setStatus('playing')} onPause={() => setStatus('paused')} onEnded={() => setStatus('paused')} onTimeUpdate={() => callback.current?.(audio.current?.currentTime ?? -1)} onError={() => setError('This browser could not play the recording. Try downloading its audio.')} />}
    {error && <small role="alert" className="recording-error">{error}</small>}
  </div>
})
export default ProgressivePlayback
