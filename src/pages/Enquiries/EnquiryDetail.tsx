import { Link, useNavigate, useParams } from 'react-router-dom'
import { getEnquiryById } from '../../services/demoService'
import { ArrowLeft, CalendarDays, ClipboardList, FileAudio2, UserRound } from 'lucide-react'
import './NewEnquiry.css'

export default function EnquiryDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const enquiry = id ? getEnquiryById(id) : null

  if (!enquiry) {
    return (
      <div style={{ padding: 20 }}>
        <h2>Enquiry not found</h2>
        <p>The requested enquiry could not be found in demo data.</p>
        <div style={{ marginTop: 12 }}>
          <button onClick={() => navigate('/enquiries')} className="button button-secondary">Back to list</button>
        </div>
      </div>
    )
  }

  return (
    <div className="new-enquiry-page">
      <div className="enquiry-topline">
        <Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link>
        <span className="enquiry-id-label">{enquiry.id}</span>
      </div>

      <header className="enquiry-page-header">
        <div>
          <div className="page-kicker"><ClipboardList size={14} /> ENQUIRY</div>
          <h1>{enquiry.customerName}</h1>
          <p>{enquiry.product || 'No product specified'}</p>
        </div>
        <div className="required-note">Created on {enquiry.createdAt}</div>
      </header>

      <div className="enquiry-workspace">
        <div className="enquiry-main-column">
          <section className="enquiry-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <div style={{ width: 46, height: 46, borderRadius: 9, background: '#e8f5f3', display: 'grid', placeItems: 'center' }}><UserRound size={20} /></div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{enquiry.customerName}</div>
                <div style={{ color: '#6b7280' }}>{enquiry.company || ''}</div>
              </div>
            </div>

            <dl style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}><dt style={{ width: 120, color: '#6b7280' }}>Phone</dt><dd>{enquiry.phone || '—'}</dd></div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}><dt style={{ width: 120, color: '#6b7280' }}>Email</dt><dd>{enquiry.email || '—'}</dd></div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}><dt style={{ width: 120, color: '#6b7280' }}>Channel</dt><dd>{enquiry.channel}</dd></div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}><dt style={{ width: 120, color: '#6b7280' }}>Branch</dt><dd>{enquiry.branch}</dd></div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}><dt style={{ width: 120, color: '#6b7280' }}>Assigned</dt><dd>{enquiry.assignedExecutive}</dd></div>
            </dl>

            <div style={{ marginTop: 12 }}>
              <h3 style={{ margin: 0 }}>Notes</h3>
              <p style={{ color: '#374151' }}>{enquiry.notes || 'No notes provided.'}</p>
            </div>

            <div style={{ marginTop: 12 }}>
              <h3 style={{ margin: 0 }}>Product requirements</h3>
              {enquiry.productRequirements && enquiry.productRequirements.length ? (
                <ul style={{ marginTop: 8 }}>
                  {enquiry.productRequirements.map((p, i) => (
                    <li key={i} style={{ marginBottom: 8 }}>
                      <strong>{p.product}</strong> — {p.quantity} {p.unit} {p.budget ? `· Budget: ${p.budget}` : ''}
                      <div style={{ color: '#6b7280' }}>{p.notes}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div style={{ color: '#6b7280', marginTop: 8 }}>No product requirements recorded.</div>
              )}
            </div>

          </section>
        </div>

        <aside className="enquiry-side-column">
          <section className="enquiry-card side-card">
            <div className="side-card-title"><span className="side-icon teal-side-icon"><CalendarDays size={16} /></span><h2>Summary</h2></div>
            <dl style={{ marginTop: 8 }}>
              <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}><dt style={{ width: 110, color: '#6b7280' }}>Priority</dt><dd>{enquiry.priority || 'Normal'}</dd></div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}><dt style={{ width: 110, color: '#6b7280' }}>Follow-up</dt><dd>{enquiry.followUpDate ? `${enquiry.followUpDate} · ${enquiry.followUpOwner || ''}` : 'Not scheduled'}</dd></div>
            </dl>
          </section>

          <section className="enquiry-card side-card">
            <div className="side-card-title"><span className="side-icon purple-side-icon"><FileAudio2 size={16} /></span><h2>Recording</h2></div>
            <div style={{ marginTop: 8, color: '#6b7280' }}>Audio preview is not stored in demo mode. In a real system this would show stored audio and transcript.</div>
          </section>
        </aside>
      </div>
    </div>
  )
}
