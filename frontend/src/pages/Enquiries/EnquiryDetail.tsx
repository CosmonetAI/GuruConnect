import { Link, useLocation, useParams } from 'react-router-dom'
import { ArrowLeft, CalendarDays, ClipboardList, FileAudio2, PackageSearch, UserRound } from 'lucide-react'
import { useScopedResource } from '../../hooks/useScopedResource'
import { OfficeNotice, OfficeStatus } from '../../components/OfficeUi'
import ConversationTranscript from '../../components/ConversationTranscript'
import type { Enquiry } from '../../types/enquiry'
import { recordingStatusFromEnquiry } from '../../recording/status'
import './NewEnquiry.css'

function Details({ entries }: { entries: [string, string | number | undefined][] }) {
  return <dl className="summary-list">{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value !== undefined && value !== '' ? value : 'Not provided'}</dd></div>)}</dl>
}
function Notes({ label, value }: { label: string; value?: string }) {
  return value ? <section><h3>{label}</h3><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{value}</p></section> : null
}
function dateLabel(value: string) {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value
}

function startSessionUrl(enquiry: Enquiry) {
  const params = new URLSearchParams({ start: '1', name: enquiry.customerName, enquiryId: enquiry.id, stage: enquiry.stage || 'new' })
  if (enquiry.phone) params.set('phone', enquiry.phone)
  if (enquiry.email) params.set('email', enquiry.email)
  return `/ai-chats?${params}`
}

export default function EnquiryDetail() {
  const { id } = useParams()
  const location = useLocation()
  const resource = useScopedResource<Enquiry>(id ? `/enquiries/${encodeURIComponent(id)}` : null)
  const enquiry = resource.data
  const confirmation = typeof location.state?.confirmation === 'string' ? location.state.confirmation : ''

  if (!enquiry) return <div className="new-enquiry-page">
    <Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link>
    <h1>{resource.loading ? 'Loading enquiry…' : 'Enquiry unavailable'}</h1>
    {resource.loading ? <p role="status">Loading the saved customer details from your workspace…</p> : <OfficeNotice error>{resource.error || 'This enquiry was not found or you do not have access to it.'}</OfficeNotice>}
    {!resource.loading && <button type="button" className="button button-secondary" onClick={() => void resource.reload()}>Retry loading</button>}
  </div>

  return <div className="new-enquiry-page">
    <div className="enquiry-topline"><Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link><span className="enquiry-id-label">{enquiry.enquiryNumber || enquiry.id}</span></div>
    <header className="enquiry-page-header"><div><div className="page-kicker"><ClipboardList size={14} /> SAVED ENQUIRY</div><h1>{enquiry.customerName}</h1><p>{enquiry.product || 'Customer details and conversation'}</p></div><div><div className="required-note">Created {dateLabel(enquiry.createdAt)}</div><Link className="button button-primary" to={startSessionUrl(enquiry)}>Continue conversation</Link></div></header>
    {confirmation && <OfficeNotice>{confirmation}</OfficeNotice>}
    {resource.error && <OfficeNotice error>{resource.error} <button type="button" className="button button-secondary" onClick={() => void resource.reload()}>Retry</button></OfficeNotice>}
    <div className="enquiry-workspace">
      <div className="enquiry-main-column">
        <section className="enquiry-card" aria-labelledby="saved-customer-heading">
          <div className="enquiry-section-heading"><div className="section-icon"><UserRound size={20} /></div><div className="section-heading-copy"><span className="section-eyebrow">CUSTOMER</span><h2 id="saved-customer-heading">Customer information</h2></div><span className="section-step">01</span></div>
          <Details entries={[
            ['Phone', enquiry.phone], ['Email', enquiry.email], ['Source', enquiry.channel], ['Branch', enquiry.branch],
            ['Assigned executive', enquiry.assignedExecutive], ['Customer type', enquiry.customerType], ['Company', enquiry.company],
            ['Project / site', enquiry.projectName], ['Preferred contact', enquiry.preferredContactMethod],
          ]} />
          <Notes label="Customer notes" value={enquiry.customerNotes} />
          <Notes label="Salesperson notes" value={enquiry.salespersonNotes} />
          <Notes label="Requirements summary" value={enquiry.requirementsSummary} />
          <Notes label="Additional notes" value={enquiry.notes} />
        </section>
        <section className="enquiry-card" aria-labelledby="saved-requirements-heading">
          <div className="enquiry-section-heading"><div className="section-icon"><PackageSearch size={20} /></div><div className="section-heading-copy"><span className="section-eyebrow">WHAT THEY NEED</span><h2 id="saved-requirements-heading">Product requirements</h2></div></div>
          {enquiry.productRequirements?.length ? <div className="product-requirements">{enquiry.productRequirements.map((product, index) => <section className="product-row" key={index} aria-label={`Product requirement ${index + 1}`}>
            <h3>{product.product || product.sku}</h3>
            <Details entries={[
              ['Product', product.product], ['SKU', product.sku], ['Quantity', `${product.quantity} ${product.unit}`],
              ['Customer segment', product.customerSegment], ['Budget', product.budget], ['Payment preference', product.paymentPreference],
              ['Purchase timeline', product.timeline], ['Stock check requested', product.stockCheck ? 'Yes — verify availability' : 'No'],
            ]} />
            <Notes label="Product notes" value={product.notes} />
          </section>)}</div> : <p>No product requirements were entered.</p>}
        </section>
      </div>
      <aside className="enquiry-side-column" aria-label="Enquiry summary">
        <section className="enquiry-card side-card"><div className="side-card-title"><span className="side-icon amber-side-icon"><CalendarDays size={16} /></span><h2>Follow-up</h2></div><Details entries={[
          ['Priority', enquiry.priority || 'Normal'], ['Follow-up date', enquiry.followUpDate], ['Follow-up owner', enquiry.followUpOwner],
        ]} /></section>
        <section className="enquiry-card side-card"><div className="side-card-title"><span className="side-icon purple-side-icon"><FileAudio2 size={16} /></span><h2>Conversation</h2></div>
          {enquiry.conversationId ? <><p>The original audio is linked to this enquiry. Follow the actual transcription and English translation below.</p><span className={`recording-state-pill status-${recordingStatusFromEnquiry(enquiry.recording).key}`}>{recordingStatusFromEnquiry(enquiry.recording).label}</span><OfficeStatus value={enquiry.recording?.uploadState} /><Link to={`/recordings/${enquiry.conversationId}`}>Open recording and transcript</Link><p>Audio → original speech and speakers → English transcript.</p></> : <p>No recording was attached to this enquiry. Customer details and requirements are saved.</p>}
        </section>
      </aside>
    </div>
    {enquiry.conversationId && <ConversationTranscript conversationId={enquiry.conversationId} />}
  </div>
}
