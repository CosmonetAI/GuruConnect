import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import PageLoader from '../components/PageLoader'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'

interface AnalyticsData {
  total: number
  organizationWide: boolean
  range: { start: string; end: string; groupBy: string }
  channels: { channel: string; count: number }[]
  stages: { stage: string; count: number }[]
  trend: { period: string; enquiries: number }[]
  visitors: { total: number; converted: number; pending: number }
  conversations: { total: number; duration: number }
  followUps: { pending: number; overdue: number }
  followUpCoverage: { due: number; overdue: number; completed: number | null; coverage: number | null }
  salespersonPerformance: { userId: string; name: string; calls: number; conversations: number; pending: number; activeTime: number; won: number }[]
  callActivity: { period: string; calls: number; conversations: number; duration: number; visitors: number }[]
  latestActivities: { id: string; customerName: string; createdAt: string; stage: string; channel: string; branch: string; assignedName: string }[]
}

const colors = ['#1769ed', '#18a36b', '#d08a19', '#8b5cf6', '#e05252', '#62738c']
const dateValue = (date: Date) => date.toISOString().slice(0, 10)

export default function Analytics() {
  const { workspace } = useOffice()
  const [period, setPeriod] = useState('30')
  const [groupBy, setGroupBy] = useState('day')
  const [detail, setDetail] = useState<'performance' | 'calls' | null>(null)
  const dates = useMemo(() => {
    const end = new Date()
    const start = new Date(end)
    start.setDate(start.getDate() - Number(period))
    return { start: dateValue(start), end: dateValue(end) }
  }, [period])
  const path = `/analytics?start=${encodeURIComponent(`${dates.start}T00:00:00Z`)}&end=${encodeURIComponent(`${dates.end}T23:59:59Z`)}&groupBy=${groupBy}`
  const result = useScopedResource<AnalyticsData>(path)
  const data = result.data
  const stages = data?.stages ?? []
  const stage = (name: string) => stages.find(item => item.stage === name)?.count ?? 0
  const title = data?.organizationWide ? 'Sales & Activity Analytics' : 'My Sales & Activity Analytics'
  const subtitle = data?.organizationWide
    ? 'Complete visibility of your team’s sales and activity performance.'
    : 'Track your sales performance, activities and next actions.'
  const formatDuration = (seconds: number) => `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`

  return <div className="analytics-page">
    <header className="analytics-header">
      <div><div className="page-kicker">{data?.organizationWide ? 'TEAM PERFORMANCE' : 'MY SALES PERFORMANCE'}</div><h1 className="page-title">{title}</h1><p>{subtitle}</p></div>
      <div className="analytics-filters" aria-label="Analytics filters">
        <label>Period<select value={period} onChange={event => setPeriod(event.target.value)}><option value="1">Today</option><option value="7">This week</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="365">This year</option></select></label>
        <label>Group by<select value={groupBy} onChange={event => setGroupBy(event.target.value)}><option value="day">Day</option><option value="week">Week</option><option value="month">Month</option><option value="quarter">Quarter</option><option value="year">Year</option></select></label>
        <button className="button button-secondary" type="button" onClick={() => result.reload()}>Apply filters</button>
      </div>
    </header>
    {result.loading && <PageLoader kind="analytics" label="Loading analytics" detail="Calculating activity, pipeline, and enquiry insights." />}
    {result.error && <p role="alert" className="flow-error">{result.error}</p>}
    <div className="analytics-stat-grid analytics-stat-grid-eight">
      {[
        ['Total enquiries', data?.total ?? '—', 'Enquiries received'],
        ['Calls attended', data?.conversations.total ?? '—', 'Recorded conversations'],
        ['Active conversations', data?.conversations.total ?? '—', 'In selected period'],
        ['Visitors', data?.visitors.total ?? '—', 'Walk-in visits'],
        ['Quotes generated', '—', 'Not available in current source'],
        ['Deals won', stage('closed'), 'Closed records'],
        ['Pending follow-ups', data?.followUps.pending ?? '—', 'Requires next action'],
        ['Order value', '—', 'Not available in current source'],
      ].map(([label, value, note]) => <Link className="analytics-kpi" to={label === 'Total enquiries' ? '/enquiries' : '/analytics'} key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></Link>)}
    </div>
    <div className="analytics-grid analytics-main-grid analytics-section-gap">
      <section className="analytics-card analytics-reference-card"><div className="analytics-card-heading"><h2>Salesperson performance</h2><div className="analytics-card-actions"><span>{groupBy}</span><button className="analytics-view-button" type="button" onClick={() => setDetail('performance')}>View all</button></div></div>{data?.salespersonPerformance.length ? <div className="analytics-table-wrap"><table className="analytics-table analytics-preview-table"><thead><tr><th>Salesperson</th><th>Calls</th><th>Conversations</th><th>Pending</th><th>Active time</th></tr></thead><tbody>{data.salespersonPerformance.slice(0, 6).map(item => <tr key={item.userId}><td>{item.name}</td><td>{item.calls.toLocaleString()}</td><td>{item.conversations.toLocaleString()}</td><td className="analytics-warning">{item.pending.toLocaleString()}</td><td>{formatDuration(item.activeTime)}</td></tr>)}</tbody></table></div> : <p className="muted">No salesperson activity in this period.</p>}</section>
      <section className="analytics-card analytics-reference-card"><div className="analytics-card-heading"><h2>Call activity</h2><div className="analytics-card-actions"><span>{groupBy}</span><button className="analytics-view-button" type="button" onClick={() => setDetail('calls')}>View all</button></div></div><div className="analytics-chart"><ResponsiveContainer width="100%" height={220}><BarChart data={data?.callActivity ?? []}><CartesianGrid stroke="#e6edf6" vertical={false} /><XAxis dataKey="period" tickFormatter={value => new Date(value).toLocaleDateString(undefined, { month: 'short', day: '2-digit' })} /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="calls" name="Calls" fill="#397fe8" radius={[8, 8, 0, 0]} /></BarChart></ResponsiveContainer></div><div className="analytics-chart-footer"><span><i /> Peak activity in selected range</span><strong>{(data?.callActivity.reduce((sum, item) => sum + item.calls, 0) ?? 0).toLocaleString()} calls</strong></div></section>
    </div>
    <div className="analytics-grid analytics-main-grid">
      <section className="analytics-card analytics-chart-card"><div className="analytics-card-heading"><h2>Enquiry trend</h2><span>{data?.range.groupBy ?? groupBy}</span></div><div className="analytics-chart"><ResponsiveContainer width="100%" height={220}><LineChart data={data?.trend ?? []}><CartesianGrid stroke="#e6edf6" vertical={false} /><XAxis dataKey="period" tickFormatter={value => new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} /><YAxis allowDecimals={false} /><Tooltip /><Line type="monotone" dataKey="enquiries" name="Enquiries" stroke="#1769ed" strokeWidth={3} dot={false} /></LineChart></ResponsiveContainer></div></section>
      <section className="analytics-card analytics-chart-card"><div className="analytics-card-heading"><h2>Enquiry channels</h2><span>{data?.total ?? '—'} total</span></div>{data?.channels.length ? <div className="analytics-chart"><ResponsiveContainer width="100%" height={220}><PieChart><Pie data={data.channels} dataKey="count" nameKey="channel" innerRadius={58} outerRadius={84} paddingAngle={3}>{data.channels.map((item, index) => <Cell key={item.channel} fill={colors[index % colors.length]} />)}</Pie><Tooltip /></PieChart></ResponsiveContainer></div> : <p className="muted">No enquiry activity in this period.</p>}</section>
    </div>
    <div className="analytics-grid analytics-main-grid">
      <section className="analytics-card"><div className="analytics-card-heading"><h2>Sales funnel</h2><Link to="/enquiries">View details →</Link></div><div className="analytics-funnel">{['new', 'in_process', 'proposal', 'negotiation', 'closed', 'lost'].map(name => <div key={name} className="analytics-funnel-row"><span>{name.replace('_', ' ')}</span><div><i style={{ width: `${data?.total ? Math.max((stage(name) / data.total) * 100, 4) : 0}%` }} /></div><strong>{stage(name)}</strong><small>{data?.total ? `${Math.round(stage(name) / data.total * 100)}%` : '0%'}</small></div>)}</div></section>
      <section className="analytics-card analytics-followup-card"><div className="analytics-card-heading"><h2>Follow-up coverage</h2><span>Selected period</span></div><div className="analytics-followup-summary"><strong>{data?.followUpCoverage.coverage == null ? '—' : `${data.followUpCoverage.coverage}%`}</strong><span>{data?.followUpCoverage.coverage == null ? 'Completion tracking is not available in the current data source' : 'of due follow-ups were completed'}</span></div><div className="analytics-followup-list"><div><span>Due</span><strong>{data?.followUpCoverage.due ?? '—'}</strong></div><div><span>Overdue</span><strong className="analytics-warning">{data?.followUpCoverage.overdue ?? '—'}</strong></div><div><span>Completed</span><strong className="analytics-success">{data?.followUpCoverage.completed ?? '—'}</strong></div></div></section>
    </div>
    <div className="analytics-grid analytics-main-grid analytics-section-gap">
      <section className="analytics-card analytics-chart-card"><div className="analytics-card-heading"><h2>Pipeline summary</h2><span>Current period</span></div><div className="analytics-chart"><ResponsiveContainer width="100%" height={220}><BarChart data={stages}><CartesianGrid stroke="#e6edf6" vertical={false} /><XAxis dataKey="stage" /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="count" name="Records" fill="#1769ed" radius={[5, 5, 0, 0]} /></BarChart></ResponsiveContainer></div></section>
      <section className="analytics-card"><div className="analytics-card-heading"><h2>Activity overview</h2><span>Real records only</span></div><div className="analytics-list"><div><span>Walk-in visitors</span><strong>{data?.visitors.total ?? '—'}</strong></div><div><span>Converted visitors</span><strong className="analytics-success">{data?.visitors.converted ?? '—'}</strong></div><div><span>Pending visitors</span><strong>{data?.visitors.pending ?? '—'}</strong></div><div><span>Conversation time</span><strong>{data ? `${Math.round(data.conversations.duration / 60)} min` : '—'}</strong></div><div><span>Overdue follow-ups</span><strong className="analytics-danger">{data?.followUps.overdue ?? '—'}</strong></div></div></section>
    </div>
    <section className="analytics-card"><div className="analytics-card-heading"><h2>Latest activity</h2><Link to="/enquiries">Open enquiry inbox →</Link></div>{data?.latestActivities.length ? <div className="analytics-table-wrap"><table className="analytics-table"><thead><tr><th>Customer</th><th>Activity</th><th>Channel</th><th>Branch</th><th>Assigned to</th><th>Date</th></tr></thead><tbody>{data.latestActivities.map(item => <tr key={item.id}><td>{item.customerName}</td><td>{item.stage}</td><td>{item.channel}</td><td>{item.branch}</td><td>{item.assignedName}</td><td>{new Date(item.createdAt).toLocaleString()}</td></tr>)}</tbody></table></div> : !result.loading && <p className="muted">No activity in this period.</p>}</section>
    {detail && <div className="analytics-modal-backdrop" role="presentation" onClick={() => setDetail(null)}><section className="analytics-modal" role="dialog" aria-modal="true" aria-labelledby="analytics-detail-title" onClick={event => event.stopPropagation()}><div className="analytics-modal-header"><div><div className="page-kicker">{detail === 'performance' ? 'TEAM DETAIL' : 'ACTIVITY DETAIL'}</div><h2 id="analytics-detail-title">{detail === 'performance' ? 'Salesperson performance' : 'Call activity list'}</h2><p>{dates.start} – {dates.end} · {groupBy}</p></div><button className="analytics-modal-close" type="button" aria-label="Close details" onClick={() => setDetail(null)}>×</button></div>{detail === 'performance' ? <div className="analytics-table-wrap"><table className="analytics-table"><thead><tr><th>Salesperson</th><th>Calls</th><th>Conversations</th><th>Pending</th><th>Active time</th><th>Win rate</th></tr></thead><tbody>{data?.salespersonPerformance.map(item => <tr key={item.userId}><td>{item.name}</td><td>{item.calls.toLocaleString()}</td><td>{item.conversations.toLocaleString()}</td><td className="analytics-warning">{item.pending.toLocaleString()}</td><td>{formatDuration(item.activeTime)}</td><td className="analytics-success">{item.calls ? `${Math.round(item.won / item.calls * 100)}%` : '0%'}</td></tr>)}</tbody></table></div> : <div className="analytics-table-wrap"><table className="analytics-table"><thead><tr><th>Period</th><th>Calls</th><th>Conversations</th><th>Visitors</th></tr></thead><tbody>{data?.callActivity.map(item => <tr key={item.period}><td>{new Date(item.period).toLocaleDateString(undefined, { year: 'numeric', month: '2-digit' })}</td><td>{item.calls.toLocaleString()} calls</td><td>{item.conversations.toLocaleString()} conversations</td>    <td>{item.visitors.toLocaleString()} visitors</td></tr>)}</tbody></table></div>}</section></div>}
    <span className="sr-only">{workspace?.name ? `Workspace: ${workspace.name}` : ''}</span>
  </div>
}
