import { Link, useLocation, useParams } from 'react-router-dom'
import { ArrowLeft, CalendarDays, ClipboardList, UserRound } from 'lucide-react'
import PageLoader from '../../components/PageLoader'
import { useScopedResource } from '../../hooks/useScopedResource'
import type { Enquiry } from '../../types/enquiry'
import WalkinDetailRecording from '../Walkins/WalkinDetailRecording'
import '../Walkins/Walkins.css'

function dateLabel(value?: string) {
  if (!value) return 'Not provided'
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value
}

function stageLabel(stage?: string) {
  if (stage === 'in_process') return 'In Discussion'
  if (stage === 'closed') return 'Completed'
  return 'New enquiry'
}

function stageClass(stage?: string) {
  return stageLabel(stage).toLowerCase().replaceAll(' ', '-')
}

function Info({ label, value }: { label: string; value?: string | number }) {
  return <div className="walkin-info"><dt>{label}</dt><dd>{value !== undefined && value !== '' ? value : '—'}</dd></div>
}

export default function EnquiryDetail() {
  const { id = '' } = useParams()
  const location = useLocation()
  const resource = useScopedResource<Enquiry>(id ? `/enquiries/${encodeURIComponent(id)}` : null)
  const enquiry = resource.data
  const confirmation = typeof location.state?.confirmation === 'string' ? location.state.confirmation : ''

  if (!enquiry) return <div className="walkin-page"><div className="walkin-back-row"><Link to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link></div>{resource.loading ? <div className="walkin-empty"><PageLoader kind="enquiries" label="Loading enquiry" detail="Preparing the saved customer details." /></div> : <><div className="walkin-alert error">{resource.error || 'This enquiry was not found or you do not have access to it.'}</div><button type="button" className="walkin-button secondary" onClick={() => void resource.reload()}>Retry loading</button></>}</div>

  const status = stageLabel(enquiry.stage)
  return <div className="walkin-page enquiry-detail-page">
    <div className="walkin-back-row"><Link to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link><span className="enquiry-id-label">{enquiry.enquiryNumber || enquiry.id}</span></div>
    <header className="walkin-header"><div><div className="walkin-kicker"><ClipboardList size={15} /> ENQUIRY {enquiry.enquiryNumber || enquiry.id}</div><h1>{enquiry.customerName}</h1><p>{enquiry.phone}{enquiry.product ? ` · ${enquiry.product}` : ''}</p></div><span className={`walkin-status large status-${stageClass(enquiry.stage)}`}>{status}</span></header>
    {confirmation && <div className="walkin-alert success" role="status">{confirmation}</div>}
    {resource.error && <div className="walkin-alert error" role="alert">{resource.error}</div>}

    <div className="walkin-detail-grid">
      <div className="walkin-detail-column walkin-timing-column">
        <section className="walkin-card walkin-summary-card"><h2><CalendarDays size={17} /> Timing</h2><Info label="Created" value={dateLabel(enquiry.createdAt)} /><Info label="Current stage" value={status} /><Info label="Follow-up" value={dateLabel(enquiry.followUpDate)} /></section>
        <WalkinDetailRecording registeredAt={enquiry.createdAt} />
      </div>

      <section className="walkin-card walkin-customer-column"><div className="walkin-card-heading"><div><h2>Customer information</h2><p>Details captured when the enquiry was registered.</p></div></div><dl className="walkin-info-grid"><Info label="Name (customer full name)" value={enquiry.customerName} /><Info label="Phone (mobile number)" value={enquiry.phone} /><Info label="Email address" value={enquiry.email?.toLowerCase()} /><Info label="Source" value={enquiry.channel} /><Info label="Branch" value={enquiry.branch} /><Info label="Assigned executive" value={enquiry.assignedExecutive || enquiry.assignedSalesperson} /><Info label="Customer type" value={enquiry.customerType} /><Info label="Company" value={enquiry.company} /><Info label="Project / site" value={enquiry.projectName} /><Info label="Preferred contact" value={enquiry.preferredContactMethod} /></dl></section>

      <section className="walkin-card walkin-assignment-column"><div className="walkin-card-heading"><div><h2>Sales assignment</h2><p>Enquiry ownership and next action.</p></div><span className="walkin-card-icon"><UserRound size={18} /></span></div><div className="walkin-side-content"><div className="walkin-assignment-status"><span>Current status</span><strong className={`walkin-status status-${stageClass(enquiry.stage)}`}>{status}</strong></div><div className="walkin-field"><span>Sales agent</span><div className="walkin-agent-picker-trigger enquiry-static-agent"><span>{enquiry.assignedExecutive || enquiry.assignedSalesperson || 'Unassigned'}</span><span className="walkin-agent-picker-meta">Assigned</span></div></div></div></section>
    </div>
  </div>
}
