import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import PageLoader from '../components/PageLoader'
interface AnalyticsData { total: number; organizationWide: boolean; channels: { channel: string; count: number }[] }
export default function Analytics() {
  const result = useScopedResource<AnalyticsData>('/analytics')
  const channels = result.data?.channels ?? []
  return <div><header className="flow-page-header"><div><div className="page-kicker">SALES PERFORMANCE</div><h1 className="page-title">Enquiry insights</h1><p>Understand where customer demand is coming from and how much activity is moving through the sales desk.</p></div></header>{result.loading && <PageLoader kind="analytics" label="Loading enquiry insights" detail="Calculating the latest sales activity and channel mix." />}{result.error && <p role="alert" className="flow-error">{result.error}</p>}
    <section className="flow-panel"><h2>Enquiry volume</h2><strong className="metric-value">{result.data?.total ?? '—'}</strong><p>{result.data?.organizationWide ? 'Organization-wide activity across every member workspace.' : 'Activity assigned to or captured in this member workspace.'}</p>{channels.map(channel => <div className="analytics-count" key={channel.channel}><span>{channel.channel}</span><strong>{channel.count}</strong></div>)}{!result.loading && !channels.length && <p>No enquiry activity yet. <Link to="/enquiries/new">Capture an enquiry</Link> to start building your sales view.</p>}</section></div>
}
