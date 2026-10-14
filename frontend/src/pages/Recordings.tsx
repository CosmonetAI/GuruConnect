import { Link, useParams } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import { duration, type Conversation, type Page } from '../services/officeApi'
import ConversationTranscript from '../components/ConversationTranscript'
export default function Recordings() {
  const { id } = useParams()
  const result = useScopedResource<Page<Conversation>>(id ? null : '/conversations?limit=100')
  if (id) return <div><Link to="/recordings" className="back-link">← Back to recordings</Link><ConversationTranscript conversationId={id} /></div>
  return <div><header className="flow-page-header"><div><div className="page-kicker">PRIVATE AUDIO HISTORY</div><h1 className="page-title">Recordings</h1><p>Saved conversations remain available even before you link them to an enquiry.</p></div><Link to="/enquiries/new" className="button button-primary">Record a conversation</Link></header>
    <section className="flow-panel">{result.loading && <p role="status">Loading recordings…</p>}{result.error && <p role="alert" className="flow-error">{result.error} <button onClick={() => void result.reload()}>Try again</button></p>}{result.data?.items.map(item => <Link className="recent-enquiry-row" key={item.id} to={`/recordings/${item.id}`}><span><strong>{item.customer_name_snapshot}</strong><small>{new Date(item.created_at).toLocaleString()} · {duration(item.duration_seconds ?? (item.total_samples ?? 0) / 16000)}</small></span><span>{item.finalization_state.replaceAll('_', ' ')} →</span></Link>)}{!result.loading && !result.error && !result.data?.items.length && <div className="flow-empty"><h2>No recordings yet</h2><p>Record with your microphone or upload an audio file from a new enquiry.</p></div>}</section></div>
}
