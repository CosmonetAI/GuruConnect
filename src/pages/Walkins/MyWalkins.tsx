import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { walkinPurposes, walkinStatuses, type WalkinAgent, type WalkinVisit } from '../../types/walkin'
import { getCurrentWalkinAgentId, isWalkinDemoMode, listWalkinAgents, listWalkinVisits, setCurrentDemoWalkinAgentId } from '../../services/walkinService'
import './Walkins.css'

function localDateKey(value: string) {
  const date = new Date(value)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export default function MyWalkins() {
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

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const currentAgent = await getCurrentWalkinAgentId()
      setAgentId(currentAgent)
      const [loadedAgents, loadedVisits] = await Promise.all([
        listWalkinAgents(),
        listWalkinVisits({ assignedAgentId: currentAgent }),
      ])
      setAgents(loadedAgents)
      setVisits(loadedVisits)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Assigned visits could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const filtered = visits.filter((visit) =>
    (!query || [visit.customerName, visit.mobileNumber, visit.visitNumber].some((value) => value.toLocaleLowerCase().includes(query.toLocaleLowerCase())))
    && (!purpose || visit.purpose === purpose)
    && (!status || visit.status === status)
    && (!from || localDateKey(visit.checkInAt) >= from)
    && (!to || localDateKey(visit.checkInAt) <= to),
  )

  return (
    <div className="walkin-page">
      <div className="walkin-back-row"><Link to="/walkins"><ArrowLeft size={16} /> Walk-in overview</Link></div>
      <header className="walkin-header">
        <div><div className="walkin-kicker">SALES WORKSPACE</div><h1>My walk-in customers</h1><p>Visitors assigned to your sales account.</p>
          {isWalkinDemoMode() && <label className="walkin-demo-agent">Demo agent
            <select value={agentId} onChange={(event) => {
              const nextAgentId = event.target.value
              setCurrentDemoWalkinAgentId(nextAgentId)
              setAgentId(nextAgentId)
              void listWalkinVisits({ assignedAgentId: nextAgentId })
                .then(setVisits)
                .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Assigned visits could not be loaded.'))
            }}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select>
          </label>}
        </div>
        <button className="walkin-button secondary" type="button" onClick={() => void refresh()} disabled={loading}><RefreshCw size={16} /> Refresh</button>
      </header>
      {error && <div className="walkin-alert error" role="alert">{error}</div>}
      <section className="walkin-card">
        <div className="walkin-filters">
          <label>Customer<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, mobile, or visit number" /></label>
          <label>Purpose<select value={purpose} onChange={(event) => setPurpose(event.target.value)}><option value="">All purposes</option>{walkinPurposes.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{walkinStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>To<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        </div>
        {loading ? <div className="walkin-empty">Loading your assigned visitors…</div> : filtered.length === 0 ? (
          <div className="walkin-empty">No visitors assigned to your account yet.</div>
        ) : (
          <div className="walkin-table-wrap">
            <table className="walkin-table">
              <thead><tr><th>Customer / reference</th><th>Purpose</th><th>Checked in</th><th>Assigned</th><th>Waiting</th><th>Status</th><th /></tr></thead>
              <tbody>{filtered.map((visit) => (
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
      </section>
      {agentId && <p className="walkin-field-hint">Access is limited to visits assigned to your account.</p>}
    </div>
  )
}
