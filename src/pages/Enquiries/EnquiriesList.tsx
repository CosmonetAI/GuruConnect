import { useEffect, useState } from 'react'
import { getDemoSalesOptions, listEnquiries } from '../../services/demoService'
import FilterBar from '../../components/FilterBar/FilterBar'
import { Link, useLocation } from 'react-router-dom'
import './EnquiriesList.css'

export default function EnquiriesList() {
  const location = useLocation()
  const confirmation = (location.state as { confirmation?: string } | null)?.confirmation
  const salesOptions = getDemoSalesOptions()
  const [filters, setFilters] = useState<{ query?: string; channel?: string; branch?: string }>({})
  const [page, setPage] = useState(1)
  const [data, setData] = useState<{ items: any[]; total: number }>({ items: [], total: 0 })

  useEffect(() => {
    const res = listEnquiries({ page, pageSize: 10, query: filters.query, channel: filters.channel, branch: filters.branch })
    setData(res)
  }, [filters, page])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 className="page-title">Enquiries</h1>
        <Link to="/enquiries/new"><button className="create-button">New Enquiry</button></Link>
      </div>

      {confirmation && <div role="status" style={{ marginBottom: 14, padding: '12px 14px', border: '1px solid #cfe5dd', borderRadius: 8, background: '#f0f8f4', color: '#356d58', fontSize: 13 }}>{confirmation}</div>}

      <div className="filter-row">
        <FilterBar channels={["Incoming Call","Outgoing Call","Retail Walk-in","Dealership","Project","Supported WhatsApp","Email","Other","Call","Retail walk-in","Chat"]} branches={salesOptions.branches} onChange={(f) => { setFilters(prev => ({ ...prev, ...f })); setPage(1) }} />
      </div>

      <div className="enquiries-grid">
        {data.items.map((r) => (
          <article key={r.id} className="enquiry-card" aria-labelledby={`enquiry-${r.id}`}>
            <div className="enquiry-card-header">
              <a id={`enquiry-${r.id}`} className="enquiry-id" href={`/enquiries/${r.id}`}>{r.id}</a>
              <div className="enquiry-actions"><Link to={`/enquiries/${r.id}`}><button className="view-button">View</button></Link></div>
            </div>

            <div style={{ fontSize: 16, fontWeight: 600 }}>{r.customerName}</div>
            <div className="enquiry-meta">
              <div><strong>Phone:</strong><span>{r.phone || '-'}</span></div>
              <div><strong>Channel:</strong><span>{r.channel}</span></div>
              <div><strong>Branch:</strong><span>{r.branch || '—'}</span></div>
              <div><strong>Assigned:</strong><span>{r.assignedExecutive || '—'}</span></div>
              <div><strong>Date:</strong><span>{r.createdAt}</span></div>
            </div>

          </article>
        ))}
      </div>

      <div style={{ marginTop: 18, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>Prev</button>
        <div>Page {page}</div>
        <button onClick={() => setPage((p) => p + 1)} disabled={data.items.length === 0}>Next</button>
      </div>
    </div>
  )
}
