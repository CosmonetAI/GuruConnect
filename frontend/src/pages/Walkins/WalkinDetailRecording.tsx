import { useEffect, useState } from 'react'
import { Mic2, Play, RotateCcw, Square } from 'lucide-react'

export default function WalkinDetailRecording({ registeredAt }: { registeredAt: string }) {
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [hasRecording, setHasRecording] = useState(true)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!recording) return
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [recording])

  const toggleRecording = () => {
    if (recording) {
      setRecording(false)
      setHasRecording(true)
      return
    }
    setElapsed(0)
    setRecording(true)
  }

  const playRegistrationRecording = () => {
    setPlaying(true)
    window.setTimeout(() => setPlaying(false), 1400)
  }

  return <section className="walkin-card walkin-detail-recording">
    <div className="walkin-card-heading"><div><h2>Recording</h2><p>Conversation audio for this visit.</p></div></div>
    <button type="button" className={`walkin-recording-action${recording ? ' is-recording' : ''}`} onClick={toggleRecording}>
      {recording ? <><Square size={15} fill="currentColor" /> Stop recording <strong>{formatDuration(elapsed)}</strong></> : hasRecording ? <><RotateCcw size={16} /> Record again</> : <><Mic2 size={16} /> Start recording</>}
    </button>
    <div className="walkin-recording-entry">
      <span className="walkin-recording-dot" />
      <div><strong>Recording from registration</strong><small>{new Date(registeredAt).toLocaleString()}</small></div>
      <button type="button" className="walkin-recording-play" onClick={playRegistrationRecording} aria-label="Play recording from registration"><Play size={14} fill="currentColor" />{playing ? 'Playing' : 'Play'}</button>
    </div>
  </section>
}

function formatDuration(seconds: number) {
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}
