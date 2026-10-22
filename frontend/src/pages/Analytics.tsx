import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import { enquiryListPath, type EnquiryPage } from '../services/enquiryService'
import PageLoader from '../components/PageLoader'
export default function Analytics() {
  const result = useScopedResource<EnquiryPage>(enquiryListPath({ pageSize: 100 }))
  const items = result.data?.items ?? []
  const channels = [...new Set(items.map(item => item.channel))]
  return <div><header className="flow-page-header"><div><div className="page-kicker">SALES PERFORMANCE</div><h1 className="page-title">Enquiry insights</h1><p>Understand where customer demand is coming from and how much activity is moving through the sales desk.</p></div></header>{result.loading && <PageLoader kind="analytics" label="Loading enquiry insights" detail="Calculating the latest sales activity and channel mix." />}{result.error && <p role="alert" className="flow-error">{result.error}</p>}
    <section className="flow-panel"><h2>Enquiry volume</h2><strong className="metric-value">{result.data?.total ?? '—'}</strong><p>Channel distribution across the latest enquiries available to you.</p>{channels.map(channel => <div className="analytics-count" key={channel}><span>{channel}</span><strong>{items.filter(item => item.channel === channel).length}</strong></div>)}{!result.loading && !items.length && <p>No enquiry activity yet. <Link to="/enquiries/new">Capture an enquiry</Link> to start building your sales view.</p>}</section></div>
}
