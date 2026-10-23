import { Link, useParams } from 'react-router-dom'
import { ArrowRight, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'
import { duration, type Conversation, type Page } from '../services/officeApi'
import ConversationTranscript from '../components/ConversationTranscript'
import PageLoader from '../components/PageLoader'
import ProgressivePlayback from '../components/ProgressivePlayback'
import { request } from '../services/officeApi'
import '../styles/recordings.css'

function sessionUrl(item: Conversation) {
  const params = new URLSearchParams({ start: '1', name: item.customer_name_snapshot })
  if (item.phone) params.set('phone', item.phone)
  if (item.email) params.set('email', item.email)
  return `/ai-chats?${params.toString()}`
}

export default function Recordings() {
  const { id } = useParams()
  const { workspace } = useOffice()
  const result = useScopedResource<Page<Conversation>>(id ? null : '/conversations?limit=100')
  const [deleting, setDeleting] = useState<string | null>(null)
  async function deleteRecording(recordingId: string) {
    if (!workspace || !window.confirm('Delete this recording and its transcript?')) return
    setDeleting(recordingId)
    try { await request(`/conversations/${encodeURIComponent(recordingId)}`, workspace.id, { method: 'DELETE' }); await result.reload() }
    finally { setDeleting(null) }
  }
  if (id) return <div><Link to="/recordings" className="back-link">← Back to recordings</Link><ConversationTranscript conversationId={id} /></div>
  const groups = [...(result.data?.items ?? []).reduce((map, item) => {
    const key = item.phone || item.customer_id || item.customer_name_snapshot || item.id
    const group = map.get(key) ?? { name: item.customer_name_snapshot, phone: item.phone, items: [] as Conversation[] }
    group.items.push(item); map.set(key, group); return map
  }, new Map<string, { name: string; phone?: string; items: Conversation[] }>()).values()]
  return <div><header className="flow-page-header"><div><div className="page-kicker">PRIVATE AUDIO HISTORY</div><h1 className="page-title">Recordings</h1><p>Every saved recording stays separate while remaining grouped under the customer.</p></div><Link to="/enquiries/new" className="button button-primary">Record a conversation</Link></header><section className="flow-panel recording-history">{result.loading && <PageLoader kind="recordings" label="Loading conversation history" detail="Gathering saved audio and transcripts by customer." />}{result.error && <p role="alert" className="flow-error">{result.error} <button onClick={() => void result.reload()}><RefreshCw size={14} /> Try again</button></p>}{groups.map(group => <section className="recording-customer" key={`${group.phone ?? group.name}-${group.items[0].id}`}><header><div><h2>{group.name}</h2><p>{group.phone || 'Phone not provided'}</p></div><span>{group.items.length} recording{group.items.length === 1 ? '' : 's'}</span></header>{group.items.map((item, index) => <article className="recording-row" key={item.id}><div><strong>Recording {group.items.length - index}</strong><small>{new Date(item.created_at).toLocaleString()} · {duration(item.duration_seconds ?? (item.total_samples ?? 0) / 16000)}</small><small className="recording-state">{item.finalization_state.replaceAll('_', ' ')}</small></div><div className="recording-actions">{workspace && <ProgressivePlayback conversationId={item.id} workspaceId={workspace.id} />}<Link className="button button-quiet" to={`/recordings/${item.id}`}>Transcript <ArrowRight size={14} /></Link><Link className="button button-quiet" to={sessionUrl(item)}>Open session <ArrowRight size={14} /></Link><button type="button" className="button button-danger-quiet" onClick={() => void deleteRecording(item.id)} disabled={deleting === item.id}><Trash2 size={14} />Delete</button></div></article>)}</section>)}{!result.loading && !result.error && !groups.length && <div className="flow-empty"><h2>No recordings yet</h2><p>Record with your microphone or upload an audio file from a new enquiry.</p></div>}</section></div>
}
