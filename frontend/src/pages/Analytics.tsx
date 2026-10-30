import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import PageLoader from '../components/PageLoader'
import StatCard from '../components/StatCard/StatCard'
import { useOffice } from '../contexts/OfficeContext'

interface AnalyticsData {
  total?: number
  enquiries?: number
  channels?: { channel: string; count: number }[]
  stages?: { stage: string; count: number }[]
  latestVisitors?: { id: string; customerName: string; createdAt: string; stage: string; channel: string; branch: string; assignedName: string }[]
}

const stageLabels: Record<string, string> = { new: 'New', in_process: 'In progress', closed: 'Closed' }
const reps = [
  { name: 'Ravi Kumar', calls: 34, conversations: 26, pending: 6, visitors: 4, active: '6h 42m', conversion: '24%' },
  { name: 'Sumanth Reddy', calls: 29, conversations: 21, pending: 8, visitors: 3, active: '5h 58m', conversion: '19%' },
  { name: 'Priya Gupta', calls: 41, conversations: 33, pending: 4, visitors: 5, active: '7h 16m', conversion: '31%' },
  { name: 'Sneha Kapoor', calls: 25, conversations: 19, pending: 7, visitors: 2, active: '5h 21m', conversion: '17%' },
]

function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) }
function ActivityBars() { const bars = [38, 55, 44, 68, 86, 72, 94, 61, 48, 30]; return <div className="analytics-bars" aria-label="Calls attended by time of day">{bars.map((height, index) => <div className="analytics-bar-item" key={index}><div className="analytics-bar" style={{ height: `${height}%` }} /><small>{['9', '10', '11', '12', '1', '2', '3', '4', '5', '6'][index]}</small></div>)}</div> }

export default function Analytics() {
  const { workspace } = useOffice()
  const result = useScopedResource<AnalyticsData>('/analytics')
  const isOwner = workspace?.role === 'owner'
  const stages = result.data?.stages ?? []
  const channels = result.data?.channels ?? []
  const latestVisitors = result.data?.latestVisitors ?? []
  const stageCount = (stage: string) => stages.find(item => item.stage === stage)?.count ?? 0
  const currentRep = reps.find(item => item.name === workspace?.display_name) ?? reps[0]
  const totalEnquiries = result.data?.total ?? result.data?.enquiries ?? 40

  return <div className="analytics-page">
    <header className="flow-page-header"><div><div className="page-kicker">{isOwner ? 'TEAM PERFORMANCE' : 'MY SALES PERFORMANCE'}</div><h1 className="page-title">{isOwner ? 'Sales team analytics' : 'My activity dashboard'}</h1><p>{isOwner ? 'See calls, conversations, pipeline movement, and follow-up coverage across the sales team.' : 'Keep your calls, pending enquiries, visitors, and next actions visible throughout the day.'}</p></div></header>
    {result.loading && <PageLoader kind="analytics" label="Loading sales analytics" detail="Calculating the latest activity and conversion signals." />}
    {result.error && <p role="alert" className="flow-error">{result.error}</p>}
    {isOwner ? <>
      <div className="analytics-stat-grid"><StatCard title="Calls attended" value="129" subtitle="Across the sales team today" /><StatCard title="Active conversations" value="99" subtitle="76% of attended calls" /><StatCard title="Pending enquiries" value="25" subtitle="Need a next action" /><StatCard title="Visitors remaining" value="14" subtitle="Across all branches" /></div>
      <div className="analytics-grid analytics-owner-grid"><section className="flow-panel"><div className="flow-panel-heading"><h2>Salesperson performance</h2><span className="muted">Today</span></div><div className="analytics-team-table"><div className="analytics-team-row analytics-team-head"><span>Salesperson</span><span>Calls</span><span>Conversations</span><span>Pending</span><span>Active time</span><span>Win rate</span></div>{reps.map(person => <div className="analytics-team-row" key={person.name}><strong>{person.name}</strong><span>{person.calls}</span><span>{person.conversations}</span><span className="analytics-alert-value">{person.pending}</span><span>{person.active}</span><span className="analytics-success-value">{person.conversion}</span></div>)}</div></section><section className="flow-panel"><div className="flow-panel-heading"><h2>Call activity</h2><span className="muted">Team calls by hour</span></div><ActivityBars /><div className="analytics-legend"><span><i className="analytics-dot analytics-dot-blue" />Peak window: 12–3 PM</span><strong>129 calls</strong></div></section></div>
      <div className="analytics-grid"><section className="flow-panel"><div className="flow-panel-heading"><h2>Pipeline snapshot</h2><span className="muted">All salespeople</span></div>{[['New enquiries', 18], ['In progress', 31], ['Quoted', 22], ['Won', 12]].map(([label, value]) => <div className="analytics-progress-row" key={String(label)}><div><span>{label}</span><strong>{value}</strong></div><span className="analytics-progress-track"><i style={{ width: `${Number(value) * 2.5}%` }} /></span></div>)}</section><section className="flow-panel"><div className="flow-panel-heading"><h2>Follow-up coverage</h2><span className="muted">Next actions due today</span></div><div className="analytics-followup-summary"><strong>78%</strong><span>of open enquiries have a scheduled next action</span></div><div className="analytics-count"><span>Due today</span><strong>14</strong></div><div className="analytics-count"><span>Overdue</span><strong className="analytics-alert-value">5</strong></div><div className="analytics-count"><span>Completed today</span><strong className="analytics-success-value">21</strong></div></section></div>
    </> : <>
      <div className="analytics-stat-grid"><StatCard title="Calls attended" value={currentRep.calls} subtitle="Today" /><StatCard title="Peak call window" value="12–3 PM" subtitle="Most active period" /><StatCard title="Pending enquiries" value={currentRep.pending} subtitle="Need your next action" /><StatCard title="Visitors remaining" value={currentRep.visitors} subtitle="Assigned to you today" /></div>
      <div className="analytics-grid analytics-rep-grid"><section className="flow-panel"><div className="flow-panel-heading"><h2>My call activity</h2><span className="muted">Today</span></div><ActivityBars /><div className="analytics-legend"><span><i className="analytics-dot analytics-dot-blue" />Active time: {currentRep.active}</span><strong>{currentRep.conversations} conversations</strong></div></section><section className="flow-panel"><div className="flow-panel-heading"><h2>My pipeline</h2><span className="muted">Current workload</span></div><div className="analytics-count"><span>New enquiries</span><strong>3</strong></div><div className="analytics-count"><span>In progress</span><strong>8</strong></div><div className="analytics-count"><span>Quotes awaiting reply</span><strong>4</strong></div><div className="analytics-count"><span>Conversion rate</span><strong className="analytics-success-value">{currentRep.conversion}</strong></div></section></div>
      <section className="flow-panel analytics-next-actions"><div className="flow-panel-heading"><h2>Next actions</h2><Link to="/enquiries">Open enquiry inbox →</Link></div><div className="analytics-action-list"><div><strong>Follow up with Rahul Sharma</strong><small>Quote sent · due in 20 minutes</small></div><span className="analytics-stage analytics-stage-in_process">Due soon</span><div><strong>Call Priya Gupta about installation</strong><small>Customer requested a callback today</small></div><span className="analytics-stage">Scheduled</span><div><strong>Visit assigned to you</strong><small>Walk-in customer waiting at Bengaluru Central</small></div><span className="analytics-stage analytics-stage-closed">Ready</span></div></section>
    </>}
    <div className="analytics-existing-summary"><section className="flow-panel"><div className="flow-panel-heading"><h2>{isOwner ? 'Workspace enquiry summary' : 'My enquiry summary'}</h2><span className="muted">Live mock records</span></div><div className="analytics-count"><span>Total enquiries</span><strong>{totalEnquiries}</strong></div><div className="analytics-count"><span>New</span><strong>{stageCount('new') || (isOwner ? 18 : 3)}</strong></div><div className="analytics-count"><span>In progress</span><strong>{stageCount('in_process') || (isOwner ? 31 : 8)}</strong></div></section><section className="flow-panel"><div className="flow-panel-heading"><h2>Enquiry channels</h2><span className="muted">Where demand is coming from</span></div>{channels.length ? channels.map(channel => <div className="analytics-count" key={channel.channel}><span>{channel.channel}</span><strong>{channel.count}</strong></div>) : <><div className="analytics-count"><span>Retail walk-in</span><strong>{isOwner ? 18 : 5}</strong></div><div className="analytics-count"><span>Incoming call</span><strong>{isOwner ? 14 : 7}</strong></div><div className="analytics-count"><span>Referral</span><strong>{isOwner ? 8 : 2}</strong></div></>}</section></div>
    {latestVisitors.length > 0 && <section className="flow-panel"><div className="flow-panel-heading"><h2>Latest enquiries</h2><span className="muted">Most recent mock records</span></div><div className="analytics-visitors">{latestVisitors.slice(0, 5).map(visitor => <div className="analytics-visitor" key={visitor.id}><div><strong>{visitor.customerName}</strong><small>{visitor.channel} · {visitor.branch} · {formatDate(visitor.createdAt)}</small></div><div className="analytics-visitor-meta"><span className={`analytics-stage analytics-stage-${visitor.stage}`}>{stageLabels[visitor.stage] || visitor.stage}</span><small>Assigned to {visitor.assignedName}</small></div></div>)}</div></section>}
  </div>
}
