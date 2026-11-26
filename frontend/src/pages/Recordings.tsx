import { Link, useParams } from 'react-router-dom'
import { ArrowRight, Download, RefreshCw, Search, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'
import { duration, type Conversation, type Page } from '../services/officeApi'
import ConversationTranscript from '../components/ConversationTranscript'
import PageLoader from '../components/PageLoader'
import ProgressivePlayback from '../components/ProgressivePlayback'
import { request, requestStream } from '../services/officeApi'
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
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [query, setQuery] = useState('')
  const cursor = cursors[cursors.length - 1]
  const params = new URLSearchParams({ limit: '100' })
  if (cursor) params.set('cursor', cursor)
  if (query) params.set('q', query)
  const result = useScopedResource<Page<Conversation>>(id ? null : `/conversations?${params}`)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState('')
  const [search, setSearch] = useState('')
  useEffect(() => { const timer = setTimeout(() => { setQuery(search.trim()); setCursors([null]) }, 250); return () => clearTimeout(timer) }, [search])
  async function deleteRecording(recordingId: string) {
    if (!workspace || !window.confirm('Delete this recording and its transcript?')) return
    setDeleting(recordingId)
    setDownloadError('')
    try { await request(`/conversations/${encodeURIComponent(recordingId)}`, workspace.id, { method: 'DELETE' }, workspace.member_id); await result.reload() }
    catch (error) { setDownloadError(error instanceof Error ? error.message : 'Unable to delete this recording.') }
    finally { setDeleting(null) }
  }
  async function downloadRecording(item: Conversation) {
    if (!workspace) return
    setDownloading(item.id); setDownloadError('')
    try {
      const response = await requestStream(`/conversations/${encodeURIComponent(item.id)}/playback/download`, workspace.id, {}, workspace.member_id)
      const blob = await response.blob()
      const safeName = (item.customer_name_snapshot || 'recording').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase() || 'recording'
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `${safeName}-${item.id.slice(0, 8)}.mp3`; anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : 'Unable to download this recording.')
    } finally { setDownloading(null) }
  }
  if (id) return <div><Link to="/recordings" className="back-link">← Back to recordings</Link><ConversationTranscript conversationId={id} /></div>
  const normalizedSearch = search.trim().toLowerCase()
  const visibleItems = result.data?.items ?? []
  const groups = [...visibleItems.reduce((map, item) => {
    const key = item.phone || item.customer_id || item.customer_name_snapshot || item.id
    const group = map.get(key) ?? { name: item.customer_name_snapshot, phone: item.phone, items: [] as Conversation[] }
    group.items.push(item); map.set(key, group); return map
  }, new Map<string, { name: string; phone?: string; items: Conversation[] }>()).values()]
  return <div><header className="flow-page-header"><div><div className="page-kicker">PRIVATE AUDIO HISTORY</div><h1 className="page-title">Recordings</h1><p>Every saved recording stays separate while remaining grouped under the customer.</p></div><Link to="/enquiries/new" className="button button-primary">Record a conversation</Link></header><section className="flow-panel recording-history"><label className="recording-search"><Search size={17} /><span className="sr-only">Search recordings</span><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by name, phone, or email" /></label>{downloadError && <p role="alert" className="flow-error">{downloadError}</p>}{result.loading && <PageLoader kind="recordings" label="Loading conversation history" detail="Gathering saved audio and transcripts by customer." />}{result.error && <p role="alert" className="flow-error">{result.error} <button onClick={() => void result.reload()}><RefreshCw size={14} /> Try again</button></p>}{groups.map(group => <section className="recording-customer" key={`${group.phone ?? group.name}-${group.items[0].id}`}><header><div><h2>{group.name}</h2><p>{group.phone || 'Phone not provided'}</p></div><span>{group.items.length} recording{group.items.length === 1 ? '' : 's'}</span></header>{group.items.map((item, index) => <article className="recording-row" key={item.id}><div><strong>Recording {group.items.length - index}</strong><small>{new Date(item.created_at).toLocaleString()} · {duration(item.duration_seconds ?? (item.total_samples ?? 0) / 16000)}</small><small className="recording-state">{item.finalization_state.replaceAll('_', ' ')}</small></div><div className="recording-actions">{workspace && <ProgressivePlayback conversationId={item.id} workspaceId={workspace.id} memberId={workspace.member_id} />}<button type="button" className="button button-quiet" onClick={() => void downloadRecording(item)} disabled={downloading === item.id}><Download size={14} />{downloading === item.id ? 'Preparing…' : 'Download'}</button><Link className="button button-quiet" to={`/recordings/${item.id}`}>Transcript <ArrowRight size={14} /></Link><Link className="button button-quiet" to={sessionUrl(item)}>Open session <ArrowRight size={14} /></Link><button type="button" className="button button-danger-quiet" onClick={() => void deleteRecording(item.id)} disabled={deleting === item.id}><Trash2 size={14} />Delete</button></div></article>)}</section>)}{!result.loading && !result.error && !groups.length && <div className="flow-empty"><h2>{normalizedSearch ? 'No recordings found' : 'No recordings yet'}</h2><p>{normalizedSearch ? 'Try another name, phone number, or email.' : 'Record with your microphone or upload an audio file from a new enquiry.'}</p></div>}</section><footer className="list-pagination"><span>Page {cursors.length}</span><button type="button" disabled={cursors.length === 1 || result.loading} onClick={() => setCursors(value => value.slice(0, -1))}>Previous recordings</button><button type="button" disabled={!result.data?.next_cursor || result.loading} onClick={() => { if (result.data?.next_cursor) setCursors(value => [...value, result.data!.next_cursor!]) }}>Next recordings</button></footer></div>
}
