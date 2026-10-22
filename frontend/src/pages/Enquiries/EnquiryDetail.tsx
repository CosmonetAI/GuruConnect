import { Link, useLocation, useParams } from 'react-router-dom'
import { ArrowLeft, ClipboardList, UserRound } from 'lucide-react'
import { useScopedResource } from '../../hooks/useScopedResource'
import { OfficeNotice } from '../../components/OfficeUi'
import PageLoader from '../../components/PageLoader'
import type { Enquiry } from '../../types/enquiry'
import './NewEnquiry.css'

function Details({ entries }: { entries: [string, string | number | undefined][] }) {
  return <dl className="summary-list">{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value !== undefined && value !== '' ? value : 'Not provided'}</dd></div>)}</dl>
}
function startSessionUrl(enquiry: Enquiry) {
  const params = new URLSearchParams({ start: '1', name: enquiry.customerName })
  if (enquiry.phone) params.set('phone', enquiry.phone)
  if (enquiry.email) params.set('email', enquiry.email)
  return `/ai-chats?${params}`
}

export default function EnquiryDetail() {
  const { id } = useParams(); const location = useLocation(); const resource = useScopedResource<Enquiry>(id ? `/enquiries/${encodeURIComponent(id)}` : null); const enquiry = resource.data
  const confirmation = typeof location.state?.confirmation === 'string' ? location.state.confirmation : ''
  if (!enquiry) return <div className="new-enquiry-page"><Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link><h1>{resource.loading ? 'Loading enquiry…' : 'Enquiry unavailable'}</h1>{resource.loading ? <PageLoader kind="enquiries" label="Loading enquiry details" detail="Preparing the customer record and conversation history." /> : <><OfficeNotice error>{resource.error || 'This enquiry was not found or you do not have access to it.'}</OfficeNotice><button type="button" className="button button-secondary" onClick={() => void resource.reload()}>Retry loading</button></>}</div>
  return <div className="new-enquiry-page">
    <div className="enquiry-topline"><Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link><span className="enquiry-id-label">{enquiry.enquiryNumber || enquiry.id}</span></div>
    <header className="enquiry-page-header"><div><div className="page-kicker"><ClipboardList size={14} /> SAVED ENQUIRY</div><h1>{enquiry.customerName}</h1><p>{enquiry.enquiryNumber || 'Customer enquiry'}</p></div><Link className="button button-primary" to={startSessionUrl(enquiry)}>Continue conversation</Link></header>
    {confirmation && <OfficeNotice>{confirmation}</OfficeNotice>}{resource.error && <OfficeNotice error>{resource.error}</OfficeNotice>}
    <section className="enquiry-card" aria-labelledby="saved-customer-heading"><div className="enquiry-section-heading"><div className="section-icon"><UserRound size={20} /></div><div className="section-heading-copy"><span className="section-eyebrow">CUSTOMER</span><h2 id="saved-customer-heading">Visitor information</h2></div><span className="section-step">01</span></div><Details entries={[
      ['Name', enquiry.customerName], ['Phone', enquiry.phone], ['Email', enquiry.email], ['Source', enquiry.channel || 'Retail walk-in'], ['Branch', enquiry.branch || 'Walk-in'],
      ['Assigned executive', enquiry.assignedExecutive], ['Customer type', enquiry.customerType || 'Retail'], ['Company', enquiry.company], ['Project / site', enquiry.projectName], ['Preferred contact', enquiry.preferredContactMethod],
    ]} /></section>
  </div>
}
