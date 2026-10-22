import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { walkinPurposes, walkinStatuses, type WalkinAgent, type WalkinVisit } from '../../types/walkin'
import { getCurrentWalkinAgentId, isWalkinDemoMode, listWalkinAgents, listWalkinVisitPage, setCurrentDemoWalkinAgentId } from '../../services/walkinService'
import { useOffice } from '../../contexts/OfficeContext'
import PageLoader from '../../components/PageLoader'
import WalkinPagination from './WalkinPagination'
import './Walkins.css'

export default function MyWalkins() {
  const { workspace } = useOffice()
  const [visits, setVisits] = useState<WalkinVisit[]>([])
  const [agentId, setAgentId] = useState('')
  const [agents, setAgents] = useState<WalkinAgent[]>([])
  const [query, setQuery] = useState('')
  const [purpose, setPurpose] = useState('')
  const [status, setStatus] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const sequence = useRef(0)

  const refresh = useCallback(async () => {
    const ticket = ++sequence.current
    if (!workspace) { setVisits([]); setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const currentAgent = await getCurrentWalkinAgentId(workspace.id)
      if (ticket !== sequence.current) return
      setAgentId(currentAgent)
      const [loadedAgents, loadedVisits] = await Promise.all([
        listWalkinAgents(workspace.id),
        listWalkinVisitPage(workspace.id, { assignedAgentId: currentAgent, query, purpose, status, from, to }, page),
      ])
      if (ticket !== sequence.current) return
      setAgents(loadedAgents)
      setVisits(loadedVisits.items)
      setTotal(loadedVisits.total)
    } catch (reason) {
      if (ticket !== sequence.current) return
      setError(reason instanceof Error ? reason.message : 'Assigned visits could not be loaded.')
    } finally {
      if (ticket === sequence.current) setLoading(false)
    }
  }, [workspace?.id, query, purpose, status, from, to, page])

  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 200)
    return () => { clearTimeout(timer); ++sequence.current }
  }, [refresh])

  return (
    <div className="walkin-page">
      <div className="walkin-back-row"><Link to="/walkins"><ArrowLeft size={16} /> Walk-in overview</Link></div>
      <header className="walkin-header">
        <div><div className="walkin-kicker">SALES WORKSPACE</div><h1>My walk-in customers</h1><p>Visitors assigned to your sales account.</p>
          {isWalkinDemoMode() && <label className="walkin-demo-agent">Demo agent
            <select value={agentId} onChange={(event) => {
              const nextAgentId = event.target.value
              if (!workspace) return
              setCurrentDemoWalkinAgentId(workspace.id, nextAgentId)
              setAgentId(nextAgentId)
              setPage(1)
              void refresh()
            }}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select>
          </label>}
        </div>
        <button className="walkin-button secondary" type="button" onClick={() => void refresh()} disabled={loading}><RefreshCw size={16} /> Refresh</button>
      </header>
      {error && <div className="walkin-alert error" role="alert">{error}</div>}
      <section className="walkin-card">
        <div className="walkin-filters">
          <label>Customer<input value={query} maxLength={120} onChange={(event) => { setQuery(event.target.value); setPage(1) }} placeholder="Name, mobile, or visit number" /></label>
          <label>Purpose<select value={purpose} onChange={(event) => { setPurpose(event.target.value); setPage(1) }}><option value="">All purposes</option>{walkinPurposes.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>Status<select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1) }}><option value="">All statuses</option>{walkinStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>From<input type="date" value={from} onChange={(event) => { setFrom(event.target.value); setPage(1) }} /></label>
          <label>To<input type="date" value={to} onChange={(event) => { setTo(event.target.value); setPage(1) }} /></label>
        </div>
        {loading ? <div className="walkin-empty"><PageLoader kind="walkins" label="Loading assigned visitors" detail="Preparing your customer visits and follow-up priorities." /></div> : visits.length === 0 ? (
          <div className="walkin-empty">No visitors assigned to your account yet.</div>
        ) : (
          <div className="walkin-table-wrap">
            <table className="walkin-table">
              <thead><tr><th>Customer / reference</th><th>Purpose</th><th>Checked in</th><th>Assigned</th><th>Waiting</th><th>Status</th><th /></tr></thead>
              <tbody>{visits.map((visit) => (
                <tr key={visit.id}>
                  <td><Link className="walkin-customer-link" to={`/walkins/${visit.id}`}>{visit.customerName}</Link><small>{visit.visitNumber} · {visit.mobileNumber}</small></td>
                  <td>{visit.purpose}</td>
                  <td>{new Date(visit.checkInAt).toLocaleString()}</td>
                  <td>{visit.assignedAt ? new Date(visit.assignedAt).toLocaleString() : '—'}</td>
                  <td>{Math.max(0, Math.floor((Date.now() - new Date(visit.checkInAt).getTime()) / 60_000))}m</td>
                  <td><span className={`walkin-status status-${visit.status.toLowerCase().replaceAll(' ', '-')}`}>{visit.status}</span></td>
                  <td><Link className="walkin-table-action" to={`/walkins/${visit.id}`}>Open</Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <WalkinPagination page={page} total={total} loading={loading} onPage={setPage} />
      </section>
      {agentId && <p className="walkin-field-hint">Access is limited to visits assigned to your account.</p>}
    </div>
  )
}
