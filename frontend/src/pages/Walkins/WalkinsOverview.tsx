import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock3, Plus, RefreshCw, UsersRound } from 'lucide-react'
import { walkinPurposes, walkinStatuses, type WalkinVisit } from '../../types/walkin'
import { isWalkinDemoMode, listWalkinVisitPage, type WalkinVisitPage } from '../../services/walkinService'
import { useOffice } from '../../contexts/OfficeContext'
import WalkinPagination from './WalkinPagination'
import './Walkins.css'

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function waitingTime(value: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60_000))
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

export default function WalkinsOverview() {
  const { workspace } = useOffice()
  const [visits, setVisits] = useState<WalkinVisit[]>([])
  const [query, setQuery] = useState('')
  const [purpose, setPurpose] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<WalkinVisitPage | null>(null)
  const sequence = useRef(0)

  const refresh = useCallback(async () => {
    const ticket = ++sequence.current
    if (!workspace) { setVisits([]); setResult(null); setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const response = await listWalkinVisitPage(workspace.id, { query, purpose, status }, page)
      if (ticket !== sequence.current) return
      setVisits(response.items)
      setResult(response)
    } catch (reason) {
      if (ticket !== sequence.current) return
      setError(reason instanceof Error ? reason.message : 'Walk-in visits could not be loaded.')
    } finally {
      if (ticket === sequence.current) setLoading(false)
    }
  }, [workspace?.id, query, purpose, status, page])

  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 200)
    return () => { clearTimeout(timer); ++sequence.current }
  }, [refresh])

  const counts = [
    ['Matching visits', result?.total ?? 0],
    ['Waiting for assignment', result?.summary.waiting ?? 0],
    ['Assigned', result?.summary.assigned ?? 0],
    ['In discussion', result?.summary.discussion ?? 0],
    ['Requirements captured', result?.summary.requirements ?? 0],
    ['Converted', result?.summary.converted ?? 0],
  ]

  return (
    <div className="walkin-page">
      <header className="walkin-header">
        <div>
          <div className="walkin-kicker"><UsersRound size={15} /> CUSTOMER VISITS</div>
          <h1>Walk-in overview</h1>
          <p>Reception queue, visitor assignments, and visit progress in one place.</p>
        </div>
        <div className="walkin-header-actions">
          <button className="walkin-button secondary" type="button" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw size={16} /> Refresh
          </button>
          <Link className="walkin-button primary" to="/walkins/new"><Plus size={17} /> Register visitor</Link>
        </div>
      </header>

      {isWalkinDemoMode() && <div className="walkin-notice">Demo mode — walk-in records are stored in this browser. No production authentication or database is configured.</div>}
      {error && <div className="walkin-alert error" role="alert">{error}</div>}

      <section className="walkin-stat-grid" aria-label="Walk-in summary">
        {counts.map(([label, value]) => <article className="walkin-stat-card" key={label}><span>{label}</span><strong>{value}</strong></article>)}
      </section>

      <section className="walkin-card">
        <div className="walkin-card-heading">
          <div><h2>Reception queue</h2><p>Waiting duration is calculated from the server check-in time.</p></div>
          <span className="walkin-count">{result?.total ?? 0} visits</span>
        </div>
        <div className="walkin-filters">
          <label>Search<input value={query} maxLength={120} onChange={(event) => { setQuery(event.target.value); setPage(1) }} placeholder="Customer, mobile, or visit number" /></label>
          <label>Purpose<select value={purpose} onChange={(event) => { setPurpose(event.target.value); setPage(1) }}><option value="">All purposes</option>{walkinPurposes.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>Status<select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1) }}><option value="">All statuses</option>{walkinStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
        </div>
        {loading ? <div className="walkin-empty">Loading visitor queue…</div> : visits.length === 0 ? (
          <div className="walkin-empty"><Clock3 size={21} /><strong>No walk-in visitors yet</strong><span>Register a visitor to start the queue.</span></div>
        ) : (
          <div className="walkin-table-wrap">
            <table className="walkin-table">
              <thead><tr><th>Visit / Customer</th><th>Purpose</th><th>Checked in</th><th>Waiting</th><th>Sales agent</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{visits.map((visit) => (
                <tr key={visit.id}>
                  <td><Link className="walkin-customer-link" to={`/walkins/${visit.id}`}>{visit.customerName}</Link><small>{visit.visitNumber} · {visit.mobileNumber}</small></td>
                  <td>{visit.purpose}</td>
                  <td>{formatDate(visit.checkInAt)}</td>
                  <td>{waitingTime(visit.checkInAt)}</td>
                  <td>{visit.assignedAgentName || <span className="walkin-unassigned">Unassigned</span>}</td>
                  <td><span className={`walkin-status status-${visit.status.toLowerCase().replaceAll(' ', '-')}`}>{visit.status}</span></td>
                  <td><Link className="walkin-table-action" to={`/walkins/${visit.id}`}>Open</Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <WalkinPagination page={page} total={result?.total ?? 0} loading={loading} onPage={setPage} />
      </section>
      <div className="walkin-footer-links"><Link to="/walkins/mine">My assigned visitors</Link><Link to="/walkins/history">Visit history</Link></div>
    </div>
  )
}
