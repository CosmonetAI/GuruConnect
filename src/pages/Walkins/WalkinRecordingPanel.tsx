import { useCallback, useEffect, useState } from 'react'
import { Link, useBlocker } from 'react-router-dom'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import ConversationTranscript from '../../components/ConversationTranscript'
import { useOffice } from '../../contexts/OfficeContext'
import { useScopedResource } from '../../hooks/useScopedResource'
import type { Conversation, Page } from '../../services/officeApi'
import '../Enquiries/NewEnquiry.css'

export default function WalkinRecordingPanel({ visitId, customerName, disabled = false }: { visitId: string; customerName: string; disabled?: boolean }) {
  const { user, workspace, setCaptureBusy } = useOffice()
  const key = `guruconnect:walkin-audio:${user?.id}:${workspace?.id}:${visitId}`
  const [currentId, setCurrentId] = useState<string | null>(() => { try { return sessionStorage.getItem(key) } catch { return null } })
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const result = useScopedResource<Page<Conversation>>(`/conversations?q=${encodeURIComponent(visitId)}&limit=100`)
  const blocker = useBlocker(busy)
  useEffect(() => { setCaptureBusy(busy); return () => setCaptureBusy(false) }, [busy, setCaptureBusy])
  useEffect(() => { if (blocker.state === 'blocked') { setError('Finish saving or detach the audio before leaving this visit.'); blocker.reset() } }, [blocker])
  const attach = useCallback((id: string | null) => {
    setCurrentId(id)
    try { if (id) sessionStorage.setItem(key, id); else sessionStorage.removeItem(key) } catch { setError('Audio is still saved by the backend. This tab could not retain its pending link.') }
  }, [key])
  const saved = useCallback((id: string) => { setSelected(id); void result.reload() }, [result.reload])
  return <section className="walkin-card new-enquiry-page walkin-connected-audio"><div className="walkin-card-heading"><div><h2>Conversation audio & English transcript</h2><p>Record or upload consented audio. The GuruConnect worker saves playback, transcribes speech and translates non-English turns.</p></div></div>
    {(error || result.error) && <p className="flow-error" role="alert">{error || result.error}</p>}
    {disabled ? <p>This visit is closed. Saved recordings remain available below.</p> : <ConversationRecorder customerName={customerName} title={`Walk-in visit ${visitId}`} initialConversationId={currentId} onConversationChange={attach} onBusyChange={setBusy} onSaved={saved} />}
    <div className="walkin-audio-links">{result.loading && <p role="status">Loading saved visit recordings…</p>}{result.data?.items.map(recording => <button type="button" key={recording.id} disabled={busy} onClick={() => setSelected(recording.id)}>Review recording · {new Date(recording.created_at).toLocaleString()}</button>)}</div>
    {selected && <ConversationTranscript conversationId={selected} />}
    <p className="field-hint">Saved audio stays in <Link to="/recordings">Recordings</Link> even before this visit is converted to an enquiry. Processing states show actual provider results.</p>
  </section>
}
