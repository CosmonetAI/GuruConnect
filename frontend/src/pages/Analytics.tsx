import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import PageLoader from '../components/PageLoader'
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
const activityBars = [4, 6, 5, 8, 11, 9, 12, 7, 6, 3]
const activityLabels = ['9', '10', '11', '12', '1', '2', '3', '4', '5', '6']

export default function Analytics() {
  const { workspace } = useOffice()
  const result = useScopedResource<AnalyticsData>('/analytics')
  const channels = result.data?.channels ?? []
  const stages = result.data?.stages ?? []
  const latestVisitors = result.data?.latestVisitors ?? []
  const stageCount = (stage: string) => stages.find(item => item.stage === stage)?.count ?? (result.data ? 0 : '—')
  const total = result.data?.total ?? '—'
  const followUps = latestVisitors.slice(0, 3)
  return <div className="analytics-page">
    <header className="analytics-header"><div><div className="page-kicker">MY SALES PERFORMANCE</div><h1 className="page-title">My activity dashboard</h1><p>Keep your calls, pending enquiries, visitors, and next actions visible throughout the day.</p></div></header>
    {result.loading && <PageLoader kind="analytics" label="Loading enquiry insights" detail="Calculating the latest sales activity and channel mix." />}
    {result.error && <p role="alert" className="flow-error">{result.error}</p>}
    <div className="analytics-stat-grid">
      <div className="analytics-kpi"><span>Calls attended</span><strong>{total}</strong><small>Today</small></div>
      <div className="analytics-kpi"><span>Peak call window</span><strong>12–3 PM</strong><small>Most active period</small></div>
      <div className="analytics-kpi"><span>Pending enquiries</span><strong>{stageCount('new')}</strong><small>Need your next action</small></div>
      <div className="analytics-kpi"><span>Visitors remaining</span><strong>{latestVisitors.length || (result.data ? 0 : '—')}</strong><small>Assigned to you today</small></div>
    </div>
    <div className="analytics-grid analytics-main-grid">
      <section className="analytics-card activity-card"><div className="analytics-card-heading"><h2>My call activity</h2><span>Today</span></div><div className="activity-chart" aria-label="Call activity from 9 AM to 6 PM">{activityBars.map((height, index) => <div className="activity-bar-wrap" key={activityLabels[index]}><div className="activity-bar" style={{ height: `${height * 8}px` }} /><small>{activityLabels[index]}</small></div>)}</div><div className="activity-footer"><span><i /> Active time: 6h 42m</span><strong>{total === '—' ? '—' : `${total} conversations`}</strong></div></section>
      <section className="analytics-card pipeline-card"><div className="analytics-card-heading"><h2>My pipeline</h2><span>Current workload</span></div><div className="analytics-list"><div><span>New enquiries</span><strong>{stageCount('new')}</strong></div><div><span>In progress</span><strong>{stageCount('in_process')}</strong></div><div><span>Quotes awaiting reply</span><strong>{channels.find(item => item.channel.toLowerCase().includes('quote'))?.count ?? 0}</strong></div><div><span>Conversion rate</span><strong className="analytics-success">{total === 0 ? '0%' : total === '—' ? '—' : `${Math.round((Number(stageCount('closed')) / Math.max(Number(total), 1)) * 100)}%`}</strong></div></div></section>
    </div>
    <section className="analytics-card next-actions"><div className="analytics-card-heading"><h2>Next actions</h2><Link to="/enquiries">Open enquiry inbox →</Link></div>{followUps.length ? followUps.map((visitor, index) => <div className="next-action" key={visitor.id}><div><strong>{index === 0 ? `Follow up with ${visitor.customerName}` : index === 1 ? `Call ${visitor.customerName}` : `Visit assigned to you`}</strong><small>{visitor.channel} · {visitor.branch}</small></div><span className={`action-status action-status-${index}`}>{index === 0 ? 'Due soon' : index === 1 ? 'Scheduled' : 'Ready'}</span></div>) : <p className="muted">No next actions yet. <Link to="/enquiries/new">Capture an enquiry</Link> to start building your sales view.</p>}</section>
    <div className="analytics-grid analytics-bottom-grid">
      <section className="analytics-card"><div className="analytics-card-heading"><h2>My enquiry summary</h2><span>Live records</span></div><div className="analytics-list"><div><span>Total enquiries</span><strong>{total}</strong></div><div><span>New</span><strong>{stageCount('new')}</strong></div><div><span>In progress</span><strong>{stageCount('in_process')}</strong></div></div></section>
      <section className="analytics-card"><div className="analytics-card-heading"><h2>Enquiry channels</h2><span>Where demand is coming from</span></div>{channels.length ? <div className="analytics-list">{channels.slice(0, 4).map(channel => <div key={channel.channel}><span>{channel.channel}</span><strong>{channel.count}</strong></div>)}</div> : !result.loading && <p className="muted">No enquiry activity yet. <Link to="/enquiries/new">Capture an enquiry</Link> to start building your sales view.</p>}</section>
    </div>
    <span className="sr-only">{workspace?.name ? `Workspace: ${workspace.name}` : ''}</span>
  </div>
}
