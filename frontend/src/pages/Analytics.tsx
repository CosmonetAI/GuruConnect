import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import { enquiryListPath, type EnquiryPage } from '../services/enquiryService'
export default function Analytics() {
  const result = useScopedResource<EnquiryPage>(enquiryListPath({ pageSize: 100 }))
  const items = result.data?.items ?? []
  const channels = [...new Set(items.map(item => item.channel))]
  return <div><header className="flow-page-header"><div><div className="page-kicker">WORKSPACE ACTIVITY</div><h1 className="page-title">Enquiry overview</h1><p>Counts from your saved enquiries. Advanced sales analytics are not configured.</p></div></header>{result.loading && <p role="status">Loading overview…</p>}{result.error && <p role="alert" className="flow-error">{result.error}</p>}
    <section className="flow-panel"><h2>Total enquiries</h2><strong className="metric-value">{result.data?.total ?? '—'}</strong><p>Channel breakdown below covers the {items.length} most recent enquiries you can access.</p>{channels.map(channel => <div className="analytics-count" key={channel}><span>{channel}</span><strong>{items.filter(item => item.channel === channel).length}</strong></div>)}{!result.loading && !items.length && <p>No activity yet. <Link to="/enquiries/new">Create an enquiry</Link>.</p>}</section></div>
}
