import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Plus, ArrowRight, RefreshCw, AudioLines } from 'lucide-react'
import FilterBar from '../../components/FilterBar/FilterBar'
import { useScopedResource } from '../../hooks/useScopedResource'
import { enquiryListPath, type EnquiryPage } from '../../services/enquiryService'
import './EnquiriesList.css'
export default function EnquiriesList() {
  const location = useLocation()
  const confirmation = (location.state as { confirmation?: string } | null)?.confirmation
  const [filters, setFilters] = useState<{ query?: string; channel?: string; branch?: string }>({})
  const [debounced, setDebounced] = useState(filters)
  const [page, setPage] = useState(1)
  useEffect(() => { const timer = setTimeout(() => setDebounced(filters), 250); return () => clearTimeout(timer) }, [filters])
  const result = useScopedResource<EnquiryPage>(enquiryListPath({ page, ...debounced }))
  const data = result.data
  return <div><header className="flow-page-header"><div><div className="page-kicker">YOUR WORKSPACE</div><h1 className="page-title">Enquiries</h1><p>Customer details, saved audio and transcripts stay together.</p></div><Link className="button button-primary" to="/enquiries/new"><Plus size={16} /> New enquiry</Link></header>
    {confirmation && <p role="status" className="flow-note">{confirmation}</p>}
    <section className="flow-panel"><FilterBar channels={['Incoming Call', 'Outgoing Call', 'Retail Walk-in', 'Dealership', 'Project', 'Supported WhatsApp', 'Email', 'Other']} branches={[...new Set(data?.items.map(item => item.branch) ?? [])]} onChange={next => { setFilters(previous => ({ ...previous, ...next })); setPage(1) }} /></section>
    {result.error && <div className="flow-error" role="alert">{result.error} <button type="button" onClick={() => void result.reload()}>Try again</button></div>}
    {result.loading && <p role="status">{data ? 'Refreshing enquiries…' : 'Loading enquiries…'}</p>}
    <div className="enquiries-grid">{data?.items.map(enquiry => <article key={enquiry.id} className="enquiry-card" aria-labelledby={`enquiry-${enquiry.id}`}>
      <div className="enquiry-card-header"><Link id={`enquiry-${enquiry.id}`} className="enquiry-id" to={`/enquiries/${enquiry.id}`}>{enquiry.enquiryNumber ?? enquiry.id}</Link>{enquiry.conversationId && <span className="recording-tag"><AudioLines size={14} /> Audio linked</span>}</div>
      <h2 className="enquiry-customer">{enquiry.customerName}</h2><p className="muted">{enquiry.product || 'Product details pending'}</p>
      <dl className="enquiry-list-meta"><div><dt>Channel</dt><dd>{enquiry.channel}</dd></div><div><dt>Branch</dt><dd>{enquiry.branch}</dd></div><div><dt>Assigned</dt><dd>{enquiry.assignedExecutive}</dd></div><div><dt>Created</dt><dd>{new Date(enquiry.createdAt).toLocaleDateString()}</dd></div></dl>
      <Link className="button button-secondary" to={`/enquiries/${enquiry.id}`}>Open enquiry <ArrowRight size={15} /></Link></article>)}</div>
    {!result.loading && !result.error && !data?.items.length && <div className="flow-empty"><h2>No enquiries found</h2><p>{filters.query || filters.channel || filters.branch ? 'Try adjusting the filters.' : 'Create your first enquiry and add a conversation.'}</p><Link to="/enquiries/new" className="button button-primary">Create an enquiry</Link></div>}
    <footer className="list-pagination"><span>{data?.total ?? 0} enquiries · Page {page} of {Math.max(1, data?.totalPages ?? 1)}</span><div><button disabled={page === 1 || result.loading} onClick={() => setPage(value => value - 1)}>Previous</button><button disabled={result.loading || !data || page >= data.totalPages} onClick={() => setPage(value => value + 1)}>Next</button><button aria-label="Refresh enquiries" disabled={result.loading} onClick={() => void result.reload()}><RefreshCw size={16} /></button></div></footer></div>
}
