import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { listWalkinHistoryPage, listWalkinVisits } from '../../services/walkinService'
import { useOffice } from '../../contexts/OfficeContext'
import PageLoader from '../../components/PageLoader'
import WalkinPagination from './WalkinPagination'
import type { WalkinHistoryEvent, WalkinVisit } from '../../types/walkin'
import './Walkins.css'

export default function WalkinHistory() {
  const { workspace } = useOffice()
  const [events, setEvents] = useState<WalkinHistoryEvent[]>([])
  const [visits, setVisits] = useState<WalkinVisit[]>([])
  const [visitId, setVisitId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    let active = true
    if (!workspace) { setEvents([]); setVisits([]); setLoading(false); return }
    setLoading(true)
    setError('')
    Promise.all([listWalkinHistoryPage(workspace.id, visitId || undefined, page), listWalkinVisits(workspace.id)])
      .then(([loadedEvents, loadedVisits]) => {
        if (!active) return
        setEvents(loadedEvents.items)
        setTotal(loadedEvents.total)
        setVisits(loadedVisits)
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Visit history could not be loaded.')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [workspace?.id, visitId, page])

  const visitMap = new Map(visits.map((visit) => [visit.id, visit]))

  return (
    <div className="walkin-page">
      <div className="walkin-back-row"><Link to="/walkins"><ArrowLeft size={16} /> Walk-in overview</Link></div>
      <header className="walkin-header"><div><div className="walkin-kicker">AUDIT TRAIL</div><h1>Walk-in visit history</h1><p>Registration, assignment, status, and requirement changes.</p></div></header>
      {error && <div className="walkin-alert error" role="alert">{error}</div>}
      <section className="walkin-card">
        <div className="walkin-filters"><label>Filter by customer (latest 100 visits)<select value={visitId} onChange={(event) => { setVisitId(event.target.value); setPage(1) }}><option value="">All visits</option>{visits.map((visit) => <option key={visit.id} value={visit.id}>{visit.visitNumber} · {visit.customerName}</option>)}</select></label></div>
        {loading ? <div className="walkin-empty"><PageLoader kind="walkins" label="Loading visit history" detail="Gathering the latest walk-in activity." /></div> : events.length === 0 ? <div className="walkin-empty">No visit history to show.</div> : (
          <div className="walkin-history-list">{events.map((event) => {
            const visit = visitMap.get(event.visitId)
            return <article className="walkin-history-row" key={event.id}>
              <span className="walkin-history-mark" />
              <div className="walkin-history-copy">
                <strong>{event.eventType} · <Link to={`/walkins/${event.visitId}`}>{visit?.customerName ?? `Visit ${event.visitId.slice(0, 8)}`}</Link></strong>
                <p>{event.oldValue && <>{event.oldValue} → </>}{event.newValue}{event.notes ? ` · ${event.notes}` : ''}</p>
                <small>{new Date(event.createdAt).toLocaleString()} · {event.performedBy || 'System'}{visit ? ` · ${visit.visitNumber}` : ''}</small>
              </div>
            </article>
          })}
          </div>
        )}
        <WalkinPagination page={page} total={total} loading={loading} onPage={setPage} />
      </section>
    </div>
  )
}
