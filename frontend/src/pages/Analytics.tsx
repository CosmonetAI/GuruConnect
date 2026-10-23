import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import PageLoader from '../components/PageLoader'
import StatCard from '../components/StatCard/StatCard'
import { useOffice } from '../contexts/OfficeContext'
interface AnalyticsData {
  total: number
  organizationWide: boolean
  channels: { channel: string; count: number }[]
  stages: { stage: string; count: number }[]
  latestVisitors: {
    id: string; customerName: string; createdAt: string; stage: string; channel: string
    branch: string; assignedTo: string | null; assignedName: string
  }[]
}
const stageLabels: Record<string, string> = { new: 'New', in_process: 'In progress', closed: 'Closed' }
function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}
export default function Analytics() {
  const { workspace } = useOffice()
  const result = useScopedResource<AnalyticsData>('/analytics')
  const channels = result.data?.channels ?? []
  const stages = result.data?.stages ?? []
  const latestVisitors = result.data?.latestVisitors ?? []
  const stageCount = (stage: string) => stages.find(item => item.stage === stage)?.count ?? (result.data ? 0 : '—')
  return <div><header className="flow-page-header"><div><div className="page-kicker">SALES PERFORMANCE</div><h1 className="page-title">Enquiry insights</h1><p>Understand where customer demand is coming from and how much activity is moving through the sales desk.</p></div></header>{result.loading && <PageLoader kind="analytics" label="Loading enquiry insights" detail="Calculating the latest sales activity and channel mix." />}{result.error && <p role="alert" className="flow-error">{result.error}</p>}
    <div className="analytics-stat-grid"><StatCard title="Total enquiries" value={result.data?.total ?? '—'} subtitle={result.data?.organizationWide ? `All activity in ${workspace?.name || 'this workspace'}` : 'Your workspace activity'} /><StatCard title="New" value={stageCount('new')} subtitle="Awaiting action" /><StatCard title="In progress" value={stageCount('in_process')} subtitle="Being followed up" /><StatCard title="Closed" value={stageCount('closed')} subtitle="Completed enquiries" /></div>
    <div className="analytics-grid">
      <section className="flow-panel"><h2>Pipeline status</h2>{stages.map(item => <div className="analytics-count" key={item.stage}><span>{stageLabels[item.stage] || item.stage}</span><strong>{item.count}</strong></div>)}{!result.loading && !stages.length && <p className="muted">No status activity yet.</p>}</section>
      <section className="flow-panel"><div className="flow-panel-heading"><h2>Enquiry channels</h2><span className="muted">Where demand is coming from</span></div>{channels.map(channel => <div className="analytics-count" key={channel.channel}><span>{channel.channel}</span><strong>{channel.count}</strong></div>)}{!result.loading && !channels.length && <p className="muted">No enquiry activity yet. <Link to="/enquiries/new">Capture an enquiry</Link> to start building your sales view.</p>}</section>
    </div>
    <section className="flow-panel"><div className="flow-panel-heading"><h2>Latest visitors</h2><span className="muted">Most recent enquiries</span></div>{latestVisitors.length ? <div className="analytics-visitors">{latestVisitors.map(visitor => <div className="analytics-visitor" key={visitor.id}><div><strong>{visitor.customerName}</strong><small>{visitor.channel} · {visitor.branch} · {formatDate(visitor.createdAt)}</small></div><div className="analytics-visitor-meta"><span className={`analytics-stage analytics-stage-${visitor.stage}`}>{stageLabels[visitor.stage] || visitor.stage}</span><small>Assigned to {visitor.assignedName}</small></div></div>)}</div> : !result.loading && <p className="muted">No visitors yet. <Link to="/enquiries/new">Capture an enquiry</Link> to start building your sales view.</p>}</section>
  </div>
}
