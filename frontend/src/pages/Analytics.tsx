import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useScopedResource } from '../hooks/useScopedResource'
import PageLoader from '../components/PageLoader'
import StatCard from '../components/StatCard/StatCard'
import { useOffice } from '../contexts/OfficeContext'

type AnalyticsGranularity = 'day' | 'week' | 'month' | 'quarter' | 'half-year' | 'year'
type AnalyticsRange = { from: string; to: string; granularity: AnalyticsGranularity }
type TeamRow = { id: string; name: string; calls: number; conversations: number; pending: number; visitors: number; active: string; conversion: string }
type AnalyticsData = {
  year?: number
  range?: AnalyticsRange
  totals?: { calls: number; conversations: number; pending: number; visitors: number; activeMinutes: number; followUpsDue: number; followUpsOverdue: number; followUpsCompleted: number; pipelineValue: number }
  total?: number
  enquiries?: number
  channels?: { channel: string; count: number }[]
  stages?: { stage: string; count: number }[]
  team?: TeamRow[]
  callActivity?: { label: string; calls: number; conversations: number; visitors: number; enquiries: number }[]
  pipeline?: { new: number; inProcess: number; quoted: number; won: number }
  followUp?: { due: number; overdue: number; completed: number; coverage: number }
  latestVisitors?: { id: string; customerName: string; createdAt: string; stage: string; channel: string; branch: string; assignedName: string }[]
}

const stageLabels: Record<string, string> = { new: 'New', in_process: 'In progress', quoted: 'Quoted', closed: 'Won' }
const stageClass: Record<string, string> = { new: 'new', in_process: 'in_process', quoted: 'quoted', closed: 'closed' }
const granularityLabels: Record<AnalyticsGranularity, string> = { day: 'Day', week: 'Week', month: 'Month', quarter: 'Quarter', 'half-year': 'Half-year', year: 'Year' }
const presentDate = new Date().toISOString().slice(0, 10)
const defaultRange: AnalyticsRange = { from: '2025-01-01', to: presentDate, granularity: 'month' }
const numberFormat = new Intl.NumberFormat('en-IN')

function formatNumber(value: number) { return numberFormat.format(Math.round(value || 0)) }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) }
function rangeLabel(range: AnalyticsRange) { const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }); return `${formatter.format(new Date(`${range.from}T00:00:00`))} – ${formatter.format(new Date(`${range.to}T00:00:00`))}` }
function safeRange(next: AnalyticsRange): AnalyticsRange { const from = next.from < '2025-01-01' ? '2025-01-01' : next.from > presentDate ? presentDate : next.from; const to = next.to < '2025-01-01' ? '2025-01-01' : next.to > presentDate ? presentDate : next.to; return { ...next, from: from <= to ? from : to, to: from <= to ? to : from } }

function ActivityBars({ items }: { items: AnalyticsData['callActivity'] }) {
  const visible = (items || []).slice(-24)
  const peak = Math.max(...visible.map(item => item.calls), 1)
  return <div className="analytics-bars-scroll"><div className="analytics-bars" aria-label="Calls attended over the selected period">{visible.map(item => <div className="analytics-bar-item" key={item.label} title={`${item.label}: ${formatNumber(item.calls)} calls`}><div className="analytics-bar" style={{ height: `${Math.max(8, (item.calls / peak) * 100)}%` }} /><small>{item.label}</small></div>)}</div></div>
}

function AnalyticsFilters({ value, onChange }: { value: AnalyticsRange; onChange: (value: AnalyticsRange) => void }) {
  const [draft, setDraft] = useState(value)
  const update = (key: keyof AnalyticsRange, next: string) => setDraft(current => ({ ...current, [key]: next }))
  const apply = () => onChange(safeRange(draft))
  const setPreset = (from: string, to: string) => { const next = { ...draft, from, to }; setDraft(next); onChange(next) }
  return <section className="analytics-filter-panel" aria-label="Analytics calendar filters">
    <div className="analytics-filter-heading"><div><span className="page-kicker">CALENDAR FILTER</span><h2>Analytics calendar</h2><p>Choose the date range and grouping used by every chart, card, and salesperson row.</p></div><span className="analytics-year-lock">Jan 2025 – present</span></div>
    <div className="analytics-filter-grid">
      <label>Group by<select value={draft.granularity} onChange={event => update('granularity', event.target.value)}>{Object.entries(granularityLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <label>From<input type="date" min="2025-01-01" max={presentDate} value={draft.from} onChange={event => update('from', event.target.value)} /></label>
      <label>To<input type="date" min="2025-01-01" max={presentDate} value={draft.to} onChange={event => update('to', event.target.value)} /></label>
      <button type="button" className="button button-primary analytics-filter-apply" onClick={apply}>Apply to all charts</button>
    </div>
    <div className="analytics-preset-row"><span>Quick calendar:</span><button type="button" onClick={() => setPreset('2025-01-01', '2025-03-31')}>Q1 2025</button><button type="button" onClick={() => setPreset('2025-04-01', '2025-06-30')}>Q2 2025</button><button type="button" onClick={() => setPreset('2025-07-01', '2025-09-30')}>Q3 2025</button><button type="button" onClick={() => setPreset('2025-10-01', '2025-12-31')}>Q4 2025</button><button type="button" onClick={() => setPreset('2025-01-01', presentDate)}>2025–present</button></div>
  </section>
}

export default function Analytics() {
  const { workspace } = useOffice()
  const [range, setRange] = useState(defaultRange)
  const [expandedView, setExpandedView] = useState<'team' | 'activity' | null>(null)
  const path = `/analytics?from=${range.from}&to=${range.to}&groupBy=${range.granularity}`
  const result = useScopedResource<AnalyticsData>(path)
  const data = result.data
  const isTeamView = workspace?.role === 'owner' || workspace?.role === 'receptionist'
  const totals = data?.totals ?? { calls: 0, conversations: 0, pending: 0, visitors: 0, activeMinutes: 0, followUpsDue: 0, followUpsOverdue: 0, followUpsCompleted: 0, pipelineValue: 0 }
  const team = data?.team ?? []
  const currentRep = team[0] ?? { id: workspace?.member_id ?? '', name: workspace?.display_name ?? 'Sales account', calls: 0, conversations: 0, pending: 0, visitors: 0, active: '0h 0m', conversion: '0%' }
  const pipeline = data?.pipeline ?? { new: 0, inProcess: 0, quoted: 0, won: 0 }
  const followUp = data?.followUp ?? { due: 0, overdue: 0, completed: 0, coverage: 0 }
  const stages = data?.stages ?? []
  const stageCount = (stage: string) => stages.find(item => item.stage === stage)?.count ?? 0
  const totalEnquiries = data?.total ?? data?.enquiries ?? 0
  const maxPipeline = Math.max(pipeline.new, pipeline.inProcess, pipeline.quoted, pipeline.won, 1)

  return <div className="analytics-page">
    <header className="flow-page-header"><div><div className="page-kicker">{isTeamView ? 'TEAM PERFORMANCE' : 'MY SALES PERFORMANCE'}</div><h1 className="page-title">{isTeamView ? 'Sales team analytics' : 'My activity dashboard'}</h1><p>{isTeamView ? 'Compare calls, conversations, pipeline movement, and follow-up coverage across the demo team.' : 'Keep your calls, pending enquiries, visitors, and next actions visible for your sales account.'}</p></div></header>
    <AnalyticsFilters value={range} onChange={setRange} />
    {result.loading && <PageLoader kind="analytics" label="Loading sales analytics" detail="Calculating the selected activity window." />}
    {result.error && <p role="alert" className="flow-error">{result.error}</p>}
    {isTeamView ? <>
      <div className="analytics-stat-grid"><StatCard title="Calls attended" value={formatNumber(totals.calls)} subtitle="Across the selected period" /><StatCard title="Active conversations" value={formatNumber(totals.conversations)} subtitle={`${totals.calls ? Math.round((totals.conversations / totals.calls) * 100) : 0}% of attended calls`} /><StatCard title="Pending enquiries" value={formatNumber(totals.pending)} subtitle="Need a next action" /><StatCard title="Visitors" value={formatNumber(totals.visitors)} subtitle="Across the selected period" /></div>
      <div className="analytics-grid analytics-owner-grid"><section className="flow-panel analytics-fixed-panel"><div className="flow-panel-heading"><h2>Salesperson performance</h2><div className="analytics-panel-actions"><span className="muted">{granularityLabels[range.granularity]}</span><button type="button" className="analytics-expand-button" onClick={() => setExpandedView('team')}>View all</button></div></div><div className="analytics-team-table"><div className="analytics-team-row analytics-team-head"><span>Salesperson</span><span>Calls</span><span>Conversations</span><span>Pending</span><span>Active time</span><span>Win rate</span></div>{team.slice(0, 6).map(person => <div className="analytics-team-row" key={person.id}><strong>{person.name}</strong><span>{formatNumber(person.calls)}</span><span>{formatNumber(person.conversations)}</span><span className="analytics-alert-value">{formatNumber(person.pending)}</span><span>{person.active}</span><span className="analytics-success-value">{person.conversion}</span></div>)}</div></section><section className="flow-panel analytics-fixed-panel"><div className="flow-panel-heading"><h2>Call activity</h2><div className="analytics-panel-actions"><span className="muted">{granularityLabels[range.granularity]}</span><button type="button" className="analytics-expand-button" onClick={() => setExpandedView('activity')}>View all</button></div></div><ActivityBars items={data?.callActivity} /><div className="analytics-legend"><span><i className="analytics-dot analytics-dot-blue" />Peak activity in the selected range</span><strong>{formatNumber(totals.calls)} calls</strong></div></section></div>
      <div className="analytics-grid"><section className="flow-panel"><div className="flow-panel-heading"><h2>Pipeline snapshot</h2><span className="muted">All visible accounts</span></div>{[['New enquiries', pipeline.new], ['In progress', pipeline.inProcess], ['Quoted', pipeline.quoted], ['Won', pipeline.won]].map(([label, value]) => <div className="analytics-progress-row" key={String(label)}><div><span>{label}</span><strong>{formatNumber(Number(value))}</strong></div><span className="analytics-progress-track"><i style={{ width: `${Math.round((Number(value) / maxPipeline) * 100)}%` }} /></span></div>)}</section><section className="flow-panel"><div className="flow-panel-heading"><h2>Follow-up coverage</h2><span className="muted">Selected period</span></div><div className="analytics-followup-summary"><strong>{followUp.coverage}%</strong><span>of due follow-ups were completed</span></div><div className="analytics-count"><span>Due</span><strong>{formatNumber(followUp.due)}</strong></div><div className="analytics-count"><span>Overdue</span><strong className="analytics-alert-value">{formatNumber(followUp.overdue)}</strong></div><div className="analytics-count"><span>Completed</span><strong className="analytics-success-value">{formatNumber(followUp.completed)}</strong></div></section></div>
    </> : <>
      <div className="analytics-stat-grid"><StatCard title="Calls attended" value={formatNumber(currentRep.calls)} subtitle="Selected period" /><StatCard title="Active conversations" value={formatNumber(currentRep.conversations)} subtitle={`${currentRep.active} active time`} /><StatCard title="Pending enquiries" value={formatNumber(currentRep.pending)} subtitle="Need your next action" /><StatCard title="Visitors" value={formatNumber(currentRep.visitors)} subtitle="Assigned to this account" /></div>
      <div className="analytics-grid analytics-rep-grid"><section className="flow-panel"><div className="flow-panel-heading"><h2>My call activity</h2><span className="muted">{granularityLabels[range.granularity]}</span></div><ActivityBars items={data?.callActivity} /><div className="analytics-legend"><span><i className="analytics-dot analytics-dot-blue" />Account activity</span><strong>{formatNumber(currentRep.conversations)} conversations</strong></div></section><section className="flow-panel"><div className="flow-panel-heading"><h2>My pipeline</h2><span className="muted">Selected period</span></div><div className="analytics-count"><span>New enquiries</span><strong>{formatNumber(pipeline.new)}</strong></div><div className="analytics-count"><span>In progress</span><strong>{formatNumber(pipeline.inProcess)}</strong></div><div className="analytics-count"><span>Quotes awaiting reply</span><strong>{formatNumber(pipeline.quoted)}</strong></div><div className="analytics-count"><span>Conversion rate</span><strong className="analytics-success-value">{currentRep.conversion}</strong></div></section></div>
      <section className="flow-panel analytics-next-actions"><div className="flow-panel-heading"><h2>Next actions</h2><Link to="/enquiries">Open enquiry inbox →</Link></div><div className="analytics-action-list"><div><strong>Follow up with a priority account</strong><small>Selected period contains {formatNumber(followUp.due)} due follow-ups.</small></div><span className="analytics-stage analytics-stage-in_process">Due soon</span><div><strong>Review your quoted opportunities</strong><small>{formatNumber(pipeline.quoted)} quotes are in the selected window.</small></div><span className="analytics-stage">Scheduled</span><div><strong>Keep visitor follow-up moving</strong><small>{formatNumber(currentRep.visitors)} visitors are assigned to this account.</small></div><span className="analytics-stage analytics-stage-closed">Ready</span></div></section>
    </>}
    <div className="analytics-existing-summary"><section className="flow-panel"><div className="flow-panel-heading"><h2>{isTeamView ? 'Workspace enquiry summary' : 'My enquiry summary'}</h2><span className="muted">All available records</span></div><div className="analytics-count"><span>Total enquiries</span><strong>{formatNumber(totalEnquiries)}</strong></div>{['new', 'in_process', 'quoted', 'closed'].map(stage => <div className="analytics-count" key={stage}><span>{stageLabels[stage]}</span><strong className={stageClass[stage] === 'closed' ? 'analytics-success-value' : ''}>{formatNumber(stageCount(stage))}</strong></div>)}</section><section className="flow-panel"><div className="flow-panel-heading"><h2>Enquiry channels</h2><span className="muted">Calls and conversations</span></div>{(data?.channels || []).map(channel => <div className="analytics-count" key={channel.channel}><span>{channel.channel}</span><strong>{formatNumber(channel.count)}</strong></div>)}</section></div>
    {!!data?.latestVisitors?.length && <section className="flow-panel"><div className="flow-panel-heading"><h2>Latest activity</h2><span className="muted">Selected calendar window</span></div><div className="analytics-visitors">{data.latestVisitors.map(visitor => <div className="analytics-visitor" key={visitor.id}><div><strong>{visitor.customerName}</strong><small>{visitor.channel} · {visitor.branch} · {formatDate(visitor.createdAt)}</small></div><div className="analytics-visitor-meta"><span className={`analytics-stage analytics-stage-${visitor.stage}`}>{stageLabels[visitor.stage] || visitor.stage}</span><small>Assigned to {visitor.assignedName}</small></div></div>)}</div></section>}
    {expandedView && <div className="analytics-modal-backdrop" role="presentation" onClick={() => setExpandedView(null)}><section className="analytics-detail-modal" role="dialog" aria-modal="true" aria-labelledby="analytics-detail-title" onClick={event => event.stopPropagation()}><div className="analytics-modal-heading"><div><span className="page-kicker">{expandedView === 'team' ? 'TEAM DETAIL' : 'ACTIVITY DETAIL'}</span><h2 id="analytics-detail-title">{expandedView === 'team' ? 'Salesperson performance' : 'Call activity list'}</h2><p>{rangeLabel(range)} · {granularityLabels[range.granularity]}</p></div><button type="button" className="analytics-modal-close" aria-label="Close analytics detail" onClick={() => setExpandedView(null)}>×</button></div>{expandedView === 'team' ? <div className="analytics-modal-list"><div className="analytics-team-row analytics-team-head"><span>Salesperson</span><span>Calls</span><span>Conversations</span><span>Pending</span><span>Active time</span><span>Win rate</span></div>{team.map(person => <div className="analytics-team-row" key={person.id}><strong>{person.name}</strong><span>{formatNumber(person.calls)}</span><span>{formatNumber(person.conversations)}</span><span className="analytics-alert-value">{formatNumber(person.pending)}</span><span>{person.active}</span><span className="analytics-success-value">{person.conversion}</span></div>)}</div> : <div className="analytics-activity-list">{(data?.callActivity || []).map(item => <div className="analytics-activity-row" key={item.label}><strong>{item.label}</strong><span>{formatNumber(item.calls)} calls</span><span>{formatNumber(item.conversations)} conversations</span><span>{formatNumber(item.visitors)} visitors</span></div>)}</div>}</section></div>}
  </div>
}
