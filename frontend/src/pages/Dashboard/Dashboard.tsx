import { Link } from "react-router-dom";
import {
  AudioLines,
  ArrowRight,
  ClipboardList,
  Languages,
  MessageSquare,
} from "lucide-react";
import { useScopedResource } from "../../hooks/useScopedResource";
import {
  enquiryListPath,
  type EnquiryPage,
} from "../../services/enquiryService";
import PageLoader from "../../components/PageLoader";
export default function Dashboard() {
  const enquiries = useScopedResource<EnquiryPage>(
    enquiryListPath({ pageSize: 5 }),
  );
  return (
    <div className="dashboard-page">
      <header className="flow-page-header">
        <div>
          <h1 className="page-title">Sales intelligence at a glance</h1>
          <p>
            Capture every customer conversation, move opportunities forward, and
            keep the next action visible.
          </p>
        </div>
      </header>
      <section className="flow-overview">
        <div>
          <h2>From conversation to outcome</h2>
          <p>
            Turn calls and walk-in visits into structured enquiries, clear
            quotations, and accountable follow-up.
          </p>
        </div>
        <ol className="flow-steps">
          <li>
            <span>01</span>
            <AudioLines />
            <strong>Capture the conversation</strong>
            <small>Record with customer consent</small>
          </li>
          <li>
            <span>02</span>
            <ClipboardList />
            <strong>Structure the opportunity</strong>
            <small>Keep requirements and context together</small>
          </li>
          <li>
            <span>03</span>
            <Languages />
            <strong>Move the sale forward</strong>
            <small>Review, quote, and follow up</small>
          </li>
        </ol>
      </section>
      <div className="flow-summary-grid">
        <section className="flow-panel">
          <div className="flow-panel-heading">
            <h2>Enquiries</h2>
            <ClipboardList size={20} />
          </div>
          <strong className="metric-value">
            {enquiries.data?.total ?? "—"}
          </strong>
          <p>Customer opportunities in the workspace</p>
          <Link to="/enquiries">
            View enquiry inbox <ArrowRight size={14} />
          </Link>
        </section>
        <section className="flow-panel">
          <div className="flow-panel-heading">
            <h2>Session page</h2>
            <MessageSquare size={20} />
          </div>
          <p>
            Capture a customer conversation and keep the transcript, product
            recommendations, internal notes, and follow-up together in one
            workspace.
          </p>
          <Link className="button button-secondary" to="/ai-chats">
            Open session page <ArrowRight size={14} />
          </Link>
        </section>
      </div>
      <section className="flow-panel">
        <div className="flow-panel-heading">
          <h2>Recent enquiries</h2>
          <Link to="/enquiries">View inbox</Link>
        </div>
        {enquiries.error && (
          <p role="alert" className="flow-error">
            {enquiries.error}
          </p>
        )}
        {enquiries.loading && (
          <PageLoader
            kind="enquiries"
            label="Loading recent enquiries"
            detail="Bringing the latest customer opportunities into view."
          />
        )}
        {enquiries.data?.items.map((item) => (
          <Link
            className="recent-enquiry-row"
            key={item.id}
            to={`/enquiries/${item.id}`}
          >
            <span>
              <strong>{item.customerName}</strong>
              <small>
                {item.channel} · {item.branch}
              </small>
            </span>
            <span>
              {item.enquiryNumber}
              <ArrowRight size={16} />
            </span>
          </Link>
        ))}
        {!enquiries.loading &&
          !enquiries.error &&
          !enquiries.data?.items.length && (
            <p>
              No enquiries have been captured yet. Start with a customer
              conversation.
            </p>
          )}
      </section>
    </div>
  );
}
