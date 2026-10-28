import { useEffect, useRef, useState } from "react";
import { useSearchParams, useBlocker } from "react-router-dom";
import {
  BookUser,
  ChevronDown,
  ChevronRight,
  Copy,
  Headphones,
  Mic2,
  MessageSquare,
  Plus,
  Search,
  ShoppingCart,
  Download,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { call } from "../zoho/shared/api.js";
import ConversationRecorder from "./Enquiries/ConversationRecorder";
import ConversationTranscript from "../components/ConversationTranscript";
import ProgressivePlayback from "../components/ProgressivePlayback";
import PageLoader from "../components/PageLoader";
import { request as recorderRequest } from "../services/officeApi";
import { useOffice } from "../contexts/OfficeContext";
import { supabase } from "../services/officeApi";
import { recordingStatus } from "../recording/status";
import "../zoho/styles.css";
import "../styles/voice-sessions.css";
import "../styles/session-workspace.css";

const newProduct = () => ({
  product: "",
  sku: "",
  item_id: "",
  quantity: 1,
  unit: "Nos",
  customerSegment: "",
  budget: "",
  paymentPreference: "Not discussed",
  timeline: "Not discussed",
  stockCheck: false,
  notes: "",
});
const customerFields = [
  ["Phone", "phone"],
  ["Email", "email"],
  ["Source", "source"],
  ["Branch", "branch"],
  ["Assigned executive", "assigned"],
  ["Customer type", "customerType"],
  ["Company", "company"],
  ["Project / site", "project"],
  ["Preferred contact", "preferredContact"],
];

function CustomerModal({ contact, onClose }) {
  if (!contact) return null;
  const values = {
    phone: contact.phone,
    email: contact.email,
    source: contact.sources?.join(" · ") || "Retail walk-in",
    branch: "Walk-in",
    assigned: "Unassigned",
    customerType: "Retail",
    company: contact.company,
    project: contact.project,
    preferredContact: contact.preferredContact,
  };
  return (
    <div className="session-modal-backdrop" onClick={onClose}>
      <section
        className="customer-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="customer-modal-heading">
          <div>
            <small>CUSTOMER</small>
            <h2>{contact.name}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close customer details"
          >
            <X size={20} />
          </button>
        </div>
        <div className="customer-fields">
          {customerFields.map(([label, key]) => (
            <div key={key}>
              <span>{label}</span>
              <strong>{values[key] || "Not provided"}</strong>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function SalesDocumentModal({
  open,
  contact,
  customerType,
  cart,
  subtotal,
  discount,
  documentType,
  setDocumentType,
  email,
  setEmail,
  subject,
  setSubject,
  message,
  setMessage,
  busy,
  onClose,
  onBack,
  onSubmit,
}) {
  if (!open || !contact) return null;
  const isVendor = customerType === "dealer";
  const availableTypes = isVendor
    ? [{ value: "purchase-order", title: "Create purchase order", copy: "Create a purchase order in Zoho Books" }]
    : [
        { value: "invoice", title: "Send invoice", copy: "Create invoice in Zoho Books and email customer" },
        { value: "quote", title: "Send quote", copy: "Create estimate / quotation and email customer" },
        { value: "sales-order", title: "Create sales order", copy: "Create sales order in Zoho Books" },
      ];
  const selected = availableTypes.find((item) => item.value === documentType) || availableTypes[0];
  const total = Math.max(0, subtotal - discount);
  return (
    <div className="session-modal-backdrop" onClick={onClose}>
      <section className="sales-document-modal" role="dialog" aria-modal="true" aria-labelledby="sales-document-title" onClick={(event) => event.stopPropagation()}>
        <header className="sales-document-heading">
          <div>
            <small>ZOHO BOOKS</small>
            <h2 id="sales-document-title">Create sales document</h2>
            <p>Create and send a document to the {isVendor ? "vendor" : "customer"} without leaving this session.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close document modal"><X size={20} /></button>
        </header>
        <div className="sales-document-body">
          <div className="sales-document-contact">
            <div className="sales-document-avatar">{(contact.name || "C").slice(0, 2).toUpperCase()}</div>
            <div><strong>{contact.name}</strong><span>{contact.phone || "Phone not provided"}</span><span>{contact.email || "Email not provided"}</span></div>
            <span className="session-status-pill">{isVendor ? "Vendor" : "Customer"}</span>
          </div>
          <section className="sales-order-summary">
            <div className="sales-document-section-heading"><strong>Order summary ({cart.length} {cart.length === 1 ? "product" : "products"})</strong><button type="button" className="link-button" onClick={onBack}>Edit cart</button></div>
            <div className="sales-summary-table">
              <div className="sales-summary-header"><span>Product</span><span>SKU</span><span>Price</span><span>Qty</span><span>Total</span></div>
              {cart.map((line, index) => {
                const lineSubtotal = Number(line.rate || 0) * Number(line.quantity || 0);
                const lineDiscount = line.discount_type === "fixed" ? Number(line.discount || 0) : lineSubtotal * Number(line.discount || 0) / 100;
                return <div className="sales-summary-row" key={line.item_id || line.sku || index}><span>{line.product || line.name}</span><span>{line.sku || "—"}</span><span>₹{Number(line.rate || 0).toLocaleString("en-IN")}</span><span>{line.quantity}</span><strong>₹{Math.max(0, lineSubtotal - lineDiscount).toLocaleString("en-IN")}</strong></div>;
              })}
            </div>
            <div className="sales-summary-totals"><span>Subtotal</span><strong>₹{subtotal.toLocaleString("en-IN")}</strong><span>Discount</span><strong className="discount-value">- ₹{discount.toLocaleString("en-IN")}</strong><span className="final-label">Total amount</span><strong className="final-total">₹{total.toLocaleString("en-IN")}</strong></div>
          </section>
          <section>
            <div className="sales-document-section-heading"><strong>Select document to create</strong></div>
            <div className={`document-type-grid ${isVendor ? "is-vendor" : ""}`}>
              {availableTypes.map((item) => <button type="button" key={item.value} className={`document-type-card ${selected.value === item.value ? "selected" : ""}`} onClick={() => setDocumentType(item.value)}><span className="document-type-radio" /> <span><strong>{item.title}</strong><small>{item.copy}</small></span></button>)}
            </div>
          </section>
          <section className="sales-email-section">
            <div className="sales-document-section-heading"><strong>Email details</strong><small className="zoho-sender-note">Sent through the connected Zoho Books account</small></div>
            <label>To ({isVendor ? "vendor" : "customer"} email)<input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Recipient email" type="email" />{!email.trim() && <small className="document-email-warning">No email is present for this contact. Add one to the Zoho contact to send the document, or download it from GuruConnect after creation.</small>}</label>
            <div className="sales-email-grid"><label>Email subject<input value={subject} onChange={(event) => setSubject(event.target.value)} /></label><label>Email message<textarea value={message} onChange={(event) => setMessage(event.target.value)} /></label></div>
          </section>
        </div>
        <footer className="sales-document-footer"><button type="button" className="button button-quiet" onClick={onBack}>Back to cart</button><button type="button" className="primary" onClick={() => onSubmit(selected.value)} disabled={busy || !cart.length}>{busy ? "Creating…" : email.trim() ? "Create & send" : "Create & download"}</button></footer>
      </section>
    </div>
  );
}

function ProductRequirement({
  line,
  index,
  products,
  onChange,
  onSearch,
  onSelect,
  onRemove,
}) {
  return (
    <article className="product-requirement">
      <div className="product-requirement-heading">
        <strong>Product {String(index + 1).padStart(2, "0")}</strong>
        {index > 0 && (
          <button type="button" className="link-button" onClick={onRemove}>
            Remove
          </button>
        )}
      </div>
      <div className="product-search-box">
        <input
          value={line.product}
          placeholder="Product / model requested"
          onChange={(event) => onChange("product", event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onSearch(line.product);
            }
          }}
        />
        <button
          type="button"
          onClick={() => onSearch(line.product)}
          disabled={!line.product.trim()}
        >
          Search
        </button>
        {products.length > 0 && (
          <div className="product-suggestions">
            {products.map((item) => (
              <button
                type="button"
                key={item.item_id}
                onClick={() => onSelect(item)}
              >
                <span>{item.name || item.item_name}</span>
                <small>
                  {item.sku || "No SKU"}
                  {item.stock_on_hand != null
                    ? ` · Stock ${item.stock_on_hand}`
                    : ""}
                </small>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="product-inline-fields">
        <input
          value={line.sku}
          placeholder="SKU (if known)"
          onChange={(event) => onChange("sku", event.target.value)}
        />
        <input
          type="number"
          min="1"
          value={line.quantity}
          placeholder="Quantity"
          onChange={(event) => onChange("quantity", event.target.value)}
        />
        <select
          value={line.unit}
          onChange={(event) => onChange("unit", event.target.value)}
        >
          <option>Nos</option>
          <option>Sets</option>
          <option>Units</option>
          <option>Sq ft</option>
          <option>Other</option>
        </select>
      </div>
      <div className="product-inline-fields">
        <input
          value={line.customerSegment}
          placeholder="Customer segment"
          onChange={(event) => onChange("customerSegment", event.target.value)}
        />
        <input
          value={line.budget}
          placeholder="Approximate budget (optional)"
          onChange={(event) => onChange("budget", event.target.value)}
        />
      </div>
      <div className="product-inline-fields">
        <select
          value={line.paymentPreference}
          onChange={(event) =>
            onChange("paymentPreference", event.target.value)
          }
        >
          <option>Not discussed</option>
          <option>Cash</option>
          <option>Finance</option>
          <option>Credit</option>
          <option>Other</option>
        </select>
        <select
          value={line.timeline}
          onChange={(event) => onChange("timeline", event.target.value)}
        >
          <option>Not discussed</option>
          <option>Immediately</option>
          <option>Within 1 month</option>
          <option>1–3 months</option>
          <option>More than 3 months</option>
          <option>Not sure</option>
        </select>
      </div>
      <label className="stock-check">
        <input
          type="checkbox"
          checked={line.stockCheck}
          onChange={(event) => onChange("stockCheck", event.target.checked)}
        />{" "}
        Customer requested an availability / stock check
      </label>
      <textarea
        value={line.notes}
        placeholder="Additional product requirements"
        onChange={(event) => onChange("notes", event.target.value)}
      />
    </article>
  );
}

export default function VoiceSessions() {
  const [searchParams] = useSearchParams();
  const { user, workspace, setCaptureBusy } = useOffice();
  const initialEnquiryId = searchParams.get("enquiryId");
  const initialIdentity = {
    name: searchParams.get("name") || "",
    phone: searchParams.get("phone") || "",
    email: searchParams.get("email") || "",
  };
  const initialContact = initialEnquiryId
    ? {
        ...initialIdentity,
        id: `enquiry:${initialEnquiryId}`,
        enquiry_id: initialEnquiryId,
        enquiry_stage: searchParams.get("stage") || "new",
        sources: ["Enquiry"],
        records: {},
      }
    : initialIdentity;
  const [chats, setChats] = useState([]),
    [contacts, setContacts] = useState([]),
    [contactSearch, setContactSearch] = useState(""),
    [contactFilter, setContactFilter] = useState("all");
  const [session, setSession] = useState(null),
    [messages, setMessages] = useState([]),
    [recorderSessions, setRecorderSessions] = useState([]),
    [identity, setIdentity] = useState(initialIdentity),
    [conversationId, setConversationId] = useState(null),
    [selectedContact, setSelectedContact] = useState(null);
  const [sessionBusy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [contactBusy, setContactBusy] = useState(false),
    [stageBusy, setStageBusy] = useState(false);
  const [needsCollapsed, setNeedsCollapsed] = useState(false),
    [notesCollapsed, setNotesCollapsed] = useState(false);
  const [notes, setNotes] = useState([]),
    [noteText, setNoteText] = useState(""),
    [noteBusy, setNoteBusy] = useState(false),
    [noteError, setNoteError] = useState(""),
    [products, setProducts] = useState([]),
    [recommendations, setRecommendations] = useState([]),
    [transcriptPreview, setTranscriptPreview] = useState([]),
    [insights, setInsights] = useState([]),
    [cart, setCart] = useState([]),
    [cartOpen, setCartOpen] = useState(false),
    [cartProductSearchOpen, setCartProductSearchOpen] = useState(false),
    [cartSearch, setCartSearch] = useState(""),
    [cartSearchResults, setCartSearchResults] = useState([]),
    [productRequirements, setProductRequirements] = useState([newProduct()]),
    [customerType, setCustomerType] = useState("unknown"),
    [documentContact, setDocumentContact] = useState(null),
    [customerOpen, setCustomerOpen] = useState(false),
    [documentBusy, setDocumentBusy] = useState(""),
    [documentModalOpen, setDocumentModalOpen] = useState(false),
    [documentType, setDocumentType] = useState("invoice"),
    [documentEmail, setDocumentEmail] = useState(""),
    [documentSubject, setDocumentSubject] = useState(""),
    [documentMessage, setDocumentMessage] = useState("");
  const [recorderGeneration, setRecorderGeneration] = useState(0);
  const [captureBusy, setRecorderBusy] = useState(false);
  const [captureState, setCaptureState] = useState({ capture: "idle", elapsedSeconds: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false });
  const [recordingSnapshot, setRecordingSnapshot] = useState(null);
  const busy = sessionBusy || captureBusy;
  const busyRef = useRef(false); busyRef.current = busy;
  const alive = useRef(true);
  const selection = useRef(0);
  const contactRequest = useRef(0);
  const controllers = useRef(new Set());
  const sessionRef = useRef(session); sessionRef.current = session;
  const blocker = useBlocker(busy);
  useEffect(() => { if (blocker.state === 'blocked') { setError('Finish saving or detach the audio before leaving this session.'); blocker.reset(); } }, [blocker]);
  useEffect(() => { setCaptureBusy(busy); return () => setCaptureBusy(false); }, [busy, setCaptureBusy]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; ++selection.current; controllers.current.forEach(controller => controller.abort()); }; }, []);
  const sessionCall = async (path, options = {}) => {
    const controller = new AbortController(); controllers.current.add(controller);
    try { return await call(path, { ...options, signal: controller.signal, headers: { ...options.headers, 'X-Workspace-Member-ID': workspace?.member_id || workspace?.id } }, workspace?.id); }
    finally { controllers.current.delete(controller); }
  };
  const startedFromEnquiry = useRef(false);
  const load = () =>
    sessionCall("/chats")
      .then(data => { if (alive.current) setChats(data); })
      .catch((e) => { if (alive.current && e.name !== 'AbortError') setError(e.message); });
  const loadContacts = async (value) => {
    const ticket = ++contactRequest.current; setContactBusy(true);
    try {
      const data = await sessionCall(`/session-contacts${value ? `?search=${encodeURIComponent(value)}` : ''}`);
      if (alive.current && ticket === contactRequest.current) setContacts(data.contacts || []);
    } catch (e) { if (alive.current && ticket === contactRequest.current && e.name !== 'AbortError') setError(e.message); }
    finally { if (alive.current && ticket === contactRequest.current) setContactBusy(false); }
  };
  const loadSessionExtras = async (id, ticket = selection.current) => {
    const [noteData, contextData] = await Promise.all([sessionCall(`/voice-sessions/${id}/notes`), sessionCall(`/voice-sessions/${id}/context`)]);
    if (!alive.current || ticket !== selection.current) return;
    setNotes(noteData.notes || []);
    setProductRequirements(contextData.product_requirements?.length ? contextData.product_requirements : [newProduct()]);
    setCart(contextData.cart || []);
  };
  const refreshRecordingStatus = async (id = conversationId) => {
    if (!id || !workspace) return;
    try {
      const data = await recorderRequest(`/conversations/${encodeURIComponent(id)}`, workspace.id, {}, workspace.member_id);
      if (alive.current && id === sessionRef.current || id === conversationId) setRecordingSnapshot(data);
    } catch (e) {
      if (alive.current && e?.name !== "AbortError") setError((current) => current || e.message);
    }
  };
  useEffect(() => {
    load();
    loadContacts("");
  }, []);
  const open = async (id) => {
    if (busyRef.current) return;
    const ticket = ++selection.current; busyRef.current = true; setBusy(true); setError('');
    try {
      const data = await sessionCall(`/voice-sessions/${id}/history`);
      if (!alive.current || ticket !== selection.current) return;
      setSession(id); sessionRef.current = id;
      setMessages(data.messages || []); setRecorderSessions(data.recorder_sessions || []);
      setTranscriptPreview(data.transcript_preview || []); setInsights(data.insights || []);
      setNoteText(""); setNoteError("");
      setConversationId(data.conversation_id || null);
      setRecordingSnapshot((data.recorder_sessions || []).find((item) => item.id === data.conversation_id) || null);
      setIdentity({ name: data.session?.name || '', phone: data.session?.phone || '', email: data.session?.email || '' });
      setSelectedContact({ id: `session:${id}`, name: data.session?.name || '', phone: data.session?.phone || '', email: data.session?.email || '', sources: ['Session'], enquiry_id: data.session?.enquiry_id, enquiry_stage: data.session?.enquiry_stage, records: { ...(data.session?.books_contact_id ? { books: data.session.books_contact_id } : {}), ...(data.session?.bigin_contact_id ? { bigin: data.session.bigin_contact_id } : {}) } });
      await loadSessionExtras(id, ticket);
    } catch (e) { if (alive.current && ticket === selection.current && e.name !== 'AbortError') setError(e.message); }
    finally { if (alive.current && ticket === selection.current) { setBusy(false); busyRef.current = false; } }
  };
  const prepareSession = async (contact = selectedContact) => {
    if (busyRef.current) return;
    const ticket = ++selection.current;
    const next = contact
      ? {
          name: contact.name,
          phone: contact.phone || "",
          email: contact.email || "",
        }
      : identity;
    if (!next.name.trim() || !next.phone.trim() || !next.email.trim()) {
      setError("Enter the customer’s name, phone number, and email address before starting the session.");
      return;
    }
    busyRef.current = true; setBusy(true);
    setError("");
    setIdentity(next);
    setNoteText(""); setNoteError("");
    // Do not let the previous contact's failed/pending recording block this
    // contact while the phone-based session lookup is in flight.
    setConversationId(null);
    setRecordingSnapshot(null);
    setCaptureState({ capture: "idle", elapsedSeconds: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false });
    try {
      const data = await sessionCall("/voice-sessions", {
        method: "POST",
        body: JSON.stringify(next),
      });
      if (!alive.current || ticket !== selection.current) return;
      setSelectedContact(
        contact || {
          id: `session:${data.session.id}`,
          name: next.name,
          phone: next.phone,
          email: next.email,
          sources: ["Session"],
          records: {},
        },
      );
      setSession(data.session.id); sessionRef.current = data.session.id;
      setMessages(data.messages || []);
      setRecorderSessions(data.recorder_sessions || []);
      setTranscriptPreview(data.transcript_preview || []); setInsights(data.insights || []);
      setConversationId(data.conversation_id || null);
      setRecordingSnapshot((data.recorder_sessions || []).find((item) => item.id === data.conversation_id) || null);
      await load();
      await loadContacts(contactSearch);
      await loadSessionExtras(data.session.id, ticket);
    } catch (e) {
      if (alive.current && ticket === selection.current && e.name !== 'AbortError') setError(e.message);
    } finally {
      if (alive.current && ticket === selection.current) { setBusy(false); busyRef.current = false; }
    }
  };
  useEffect(() => {
    if (
      searchParams.get("start") === "1" &&
      initialIdentity.name &&
      !startedFromEnquiry.current
    ) {
      startedFromEnquiry.current = true;
      void prepareSession(initialContact);
    }
  }, []);
  const chooseContact = (contact) => {
    if (busyRef.current) return;
    setSelectedContact(contact);
    setIdentity({
      name: contact.name,
      phone: contact.phone || "",
      email: contact.email || "",
    });
    if (contact.session_id) open(contact.session_id);
    else void prepareSession(contact);
  };
  const reset = () => {
    if (busyRef.current) return;
    ++selection.current; sessionRef.current = null;
    setSession(null);
    setMessages([]);
    setRecorderSessions([]);
    setConversationId(null);
    setSelectedContact(null);
    setDocumentContact(null);
    setIdentity({ name: "", phone: "", email: "" });
    setNotes([]);
    setNoteText(""); setNoteError("");
    setTranscriptPreview([]); setInsights([]); setRecommendations([]); setCart([]); setCartOpen(false);
    setProductRequirements([newProduct()]);
    setError("");
  };
  const linkConversation = async (id) => {
    const selectedSession = session;
    if (!alive.current || sessionRef.current !== selectedSession) throw new Error('The selected customer changed. Reopen the saved recording from Conversation history.');
    if (selectedSession) await sessionCall(`/voice-sessions/${selectedSession}/conversation`, { method: 'POST', body: JSON.stringify({ conversation_id: id }) });
    if (alive.current && sessionRef.current === selectedSession) setConversationId(id);
  };
  const recordingSaved = async () => {
    const id = sessionRef.current; const ticket = selection.current;
    try {
      const data = await sessionCall(`/voice-sessions/${id}/history`);
      if (alive.current && ticket === selection.current && id === sessionRef.current) { setRecorderSessions(data.recorder_sessions || []); setTranscriptPreview(data.transcript_preview || []); setInsights(data.insights || []); void refreshRecordingStatus(data.conversation_id || conversationId); }
    } catch (e) { if (alive.current && e.name !== 'AbortError') setError(e.message); }
  };
  const deleteRecorderSession = async (id) => {
    if (!workspace || !window.confirm("Delete this recording and its transcript?")) return;
    setBusy(true);
    setError("");
    try {
      await recorderRequest(`/conversations/${encodeURIComponent(id)}`, workspace.id, { method: "DELETE" }, workspace.member_id);
      setRecorderSessions((current) => current.filter((item) => item.id !== id));
      if (conversationId === id) {
        setConversationId(null); setRecorderGeneration(value => value + 1);
        if (session) await sessionCall(`/voice-sessions/${session}/conversation`, { method: "POST", body: JSON.stringify({ conversation_id: null }) });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const updateProduct = (index, key, value) => {
    const next = productRequirements.map((line, lineIndex) =>
      lineIndex === index ? { ...line, [key]: value } : line,
    );
    setProductRequirements(next);
    if (session)
      void sessionCall(`/voice-sessions/${session}/context`, {
        method: "PUT",
        body: JSON.stringify({ product_requirements: next, cart }),
      }).catch((e) => setError(e.message));
  };
  const searchProducts = async (value) => {
    if (!value.trim()) return;
    try {
      const data = await sessionCall(
        `/products?search=${encodeURIComponent(value.trim())}`,
      );
      setProducts(data.items || []);
    } catch (e) {
      setError(e.message);
    }
  };
  const searchCartProducts = async () => {
    if (!cartSearch.trim()) return;
    try {
      const data = await sessionCall(`/products?search=${encodeURIComponent(cartSearch.trim())}`);
      setCartSearchResults(data.items || []);
    } catch (e) { setError(e.message); }
  };
  const resolveDocumentContact = async () => {
    const ticket = selection.current;
    const query = (identity.phone || identity.email || "").trim();
    if (!query) return;
    try {
      const [customers, vendors] = await Promise.all([
        sessionCall(
          `/document-contacts?role=customer&search=${encodeURIComponent(query)}`,
        ),
        sessionCall(
          `/document-contacts?role=vendor&search=${encodeURIComponent(query)}`,
        ),
      ]);
      if (!alive.current || ticket !== selection.current) return;
      const same = (row) => {
        const value = String(
          row.phone || row.mobile || row.Email || row.email || "",
        ).replace(/\D/g, "");
        return value && identity.phone
          ? value.slice(-10) === identity.phone.replace(/\D/g, "").slice(-10)
          : String(row.Email || row.email || "").toLowerCase() ===
              identity.email.toLowerCase();
      };
      const customer = (customers.contacts || []).find(same);
      const vendor = (vendors.contacts || []).find(same);
      const match = vendor || customer;
      if (match) {
        setCustomerType(vendor ? "dealer" : "customer");
        setDocumentContact(match);
      } else {
        setCustomerType("unknown");
        setDocumentContact(null);
      }
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    document.body.dataset.documentParty =
      customerType === "dealer" ? "vendor" : customerType;
    return () => {
      delete document.body.dataset.documentParty;
    };
  }, [customerType]);
  useEffect(() => {
    if (session) void resolveDocumentContact();
  }, [session, identity.phone, identity.email]);
  useEffect(() => {
    if (!session || !conversationId || !supabase) return;
    const channel = supabase.channel(`voice-extraction-${conversationId}`)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'conversation_extractions', filter: `conversation_id=eq.${conversationId}` }, () => {
        void sessionCall(`/voice-sessions/${session}/history`).then((data) => {
          if (sessionRef.current !== session) return;
          setTranscriptPreview(data.transcript_preview || []); setInsights(data.insights || []);
          const names = (data.insights || []).flatMap((item) => item.data?.products || []).map((item) => typeof item === 'string' ? item : item.name || item.product || '').filter(Boolean).slice(-3);
          if (names.length) void Promise.all(names.map((name) => sessionCall(`/products?search=${encodeURIComponent(name)}`))).then((rows) => setRecommendations(rows.flatMap((row) => row.items || []).slice(0, 8))).catch(() => {});
        }).catch(() => {});
      })
      .on('postgres_changes', { event: '*', schema: 'public', table: 'conversations', filter: `id=eq.${conversationId}` }, () => { void refreshRecordingStatus(conversationId); })
      .subscribe();
    return () => { void supabase.removeChannel(channel); };
  }, [session, conversationId]);
  useEffect(() => { if (conversationId) void refreshRecordingStatus(conversationId); }, [conversationId, workspace?.id]);
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => { void sessionCall(`/voice-sessions/${session}/context`, { method: 'PUT', body: JSON.stringify({ product_requirements: productRequirements, cart }) }).catch(() => {}); }, 250);
    return () => window.clearTimeout(timer);
  }, [session, cart]);
  const selectProduct = (index, item) => {
    updateProduct(index, "product", item.name || item.item_name || "");
    updateProduct(index, "sku", item.sku || "");
    updateProduct(index, "item_id", item.item_id || "");
    setProducts([]);
  };
  const addToCart = (item) => {
    const id = item.item_id || item.id || item.sku;
    setCart((current) => {
      const found = current.find((line) => (line.item_id || line.id || line.sku) === id);
      if (found) return current.map((line) => line === found ? { ...line, quantity: Number(line.quantity || 1) + 1 } : line);
      return [...current, { ...item, item_id: item.item_id || item.id, product: item.name || item.item_name, quantity: 1, discount: 0, discount_type: 'percentage', rate: Number(item.rate ?? item.price ?? 0) }];
    });
    setCartOpen(true);
    setCartProductSearchOpen(false);
  };
  const updateCart = (index, key, value) => setCart((current) => current.map((line, i) => i === index ? { ...line, [key]: key === 'quantity' || key === 'discount' ? Math.max(0, Number(value) || 0) : value } : line));
  const cartSubtotal = cart.reduce((sum, line) => sum + Number(line.rate || 0) * Number(line.quantity || 0), 0);
  const cartDiscount = cart.reduce((sum, line) => sum + (line.discount_type === 'fixed' ? Number(line.discount || 0) : Number(line.rate || 0) * Number(line.quantity || 0) * Number(line.discount || 0) / 100), 0);
  const openDocumentModal = async () => {
    if (!cart.length) return;
    if (customerType === "unknown") { setError("Customer account details are still being checked."); return; }
    try {
      const zohoStatus = await sessionCall("/zoho/status");
      if (!zohoStatus?.connected || !zohoStatus?.organization_id) {
        setError("Connect the salesperson's Zoho Books account and select an organization before sending a document.");
        return;
      }
    } catch (e) {
      setError(e.message);
      return;
    }
    const vendor = customerType === "dealer";
    setDocumentType(vendor ? "purchase-order" : "invoice");
    setDocumentEmail(identity.email || documentContact?.email || documentContact?.Email || "");
    setDocumentSubject(`Your ${vendor ? "purchase order" : "invoice"} from GuruConnect`);
    setDocumentMessage(`Hi ${identity.name || "there"},\n\nPlease find your ${vendor ? "purchase order" : "document"} for the selected items. Let me know if you have any questions.`);
    setDocumentModalOpen(true);
  };
  const downloadTranscript = () => {
    if (!transcriptPreview.length) return;
    const safe = (identity.name || 'contact').replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'contact';
    const stamp = new Date(recorderSessions[0]?.created_at || Date.now()).toISOString().replace('T', '_').replace(/:/g, '-').slice(0, 19);
    const content = transcriptPreview.map((turn) => `[${turn.start_seconds ?? 0}] ${turn.english_text || turn.source_text || ''}`).join('\n');
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain' })); const a = document.createElement('a'); a.href = url; a.download = `${safe}_${stamp}_transcript.txt`; a.click(); URL.revokeObjectURL(url);
  };
  const addNote = async () => {
    if (!session || !noteText.trim() || noteBusy) return;
    setNoteBusy(true);
    setNoteError("");
    try {
      const data = await sessionCall(`/voice-sessions/${session}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: noteText.trim() }),
      });
      if (!data?.note) throw new Error("The note could not be saved.");
      setNotes((current) => [...current, data.note]);
      setNoteText("");
    } catch (e) {
      setNoteError(e?.message || "The note could not be saved. Please try again.");
    } finally {
      setNoteBusy(false);
    }
  };
  const booksContactId =
    documentContact?.contact_id ||
    documentContact?.id ||
    selectedContact?.records?.books ||
    selectedContact?.books_contact_id ||
    null;
  const biginContactId =
    selectedContact?.records?.bigin ||
    selectedContact?.bigin_contact_id ||
    null;
  const downloadSalesDocument = (type, lines, result) => {
    const record = result?.quote || result?.order || result?.invoice || result?.purchase_order || result?.response?.estimate || result?.response?.salesorder || result?.response?.invoice || result?.response?.purchaseorder || {};
    const documentNumber = record.estimate_number || record.salesorder_number || record.invoice_number || record.purchaseorder_number || record.document_number || 'Created in Zoho Books';
    const content = [
      `${type.replace('-', ' ').toUpperCase()}`,
      `Contact: ${identity.name}`,
      `Phone: ${identity.phone}`,
      `Email: No email present for this contact`,
      `Document: ${documentNumber}`,
      '',
      ...lines.map((line) => `${line.product || line.name || 'Product'} | SKU: ${line.sku || '—'} | Qty: ${line.quantity} | Unit price: ₹${Number(line.rate || 0).toLocaleString('en-IN')}`),
      '',
      `Subtotal: ₹${cartSubtotal.toLocaleString('en-IN')}`,
      `Discount: ₹${cartDiscount.toLocaleString('en-IN')}`,
      `Total: ₹${Math.max(0, cartSubtotal - cartDiscount).toLocaleString('en-IN')}`,
    ].join('\n');
    const safe = (identity.name || 'contact').replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'contact';
    const stamp = new Date().toISOString().replace('T', '_').replace(/:/g, '-').slice(0, 19);
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${safe}_${stamp}_${type.replace(/-/g, '_')}.txt`; anchor.click(); URL.revokeObjectURL(url);
  };
  const createDocument = async (type, emailDetails = {}) => {
    if (customerType === "unknown") {
      setError("Customer account details are still being checked.");
      return;
    }
    if (customerType === "dealer" && type !== "purchase-order") {
      setError(
        "Quotes, invoices, and sales orders can only be created for customers.",
      );
      return;
    }
    if (customerType === "customer" && type === "purchase-order") {
      setError("Purchase orders can only be created for vendors.");
      return;
    }
    const recipient = (emailDetails.email || identity.email || "").trim();
    const hasRecipient = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient);
    if (!hasRecipient && emailDetails.downloadOnly !== true) {
      setError("No email is present for this contact. Add an email to the Zoho contact to send this document, or choose Create & download.");
      return;
    }
    const lines = (cart.length ? cart : productRequirements).filter(
      (line) => line.item_id && Number(line.quantity) > 0,
    );
    if (!lines.length) {
      setError("Search and select at least one catalogue product first.");
      return;
    }
    const contactId = booksContactId || biginContactId;
    if (!contactId) {
      setError(
        "This customer has no Zoho contact record yet. Link or create the customer before generating a document.",
      );
      return;
    }
    const path =
      type === "quote"
        ? "/quotes"
        : type === "invoice"
          ? "/invoices"
          : type === "sales-order"
            ? "/orders"
            : "/purchase-orders";
    const key = type === "purchase-order" ? "vendor_id" : "customer_id";
    const sourceKey =
      type === "purchase-order" ? "vendor_source" : "customer_source";
    const recordKey =
      type === "purchase-order" ? "vendor_record" : "customer_record";
    const fromBigin = Boolean(biginContactId && !booksContactId);
    setDocumentBusy(type);
    setError("");
    try {
      const result = await sessionCall(path, {
        method: "POST",
        body: JSON.stringify({
          [key]: contactId,
          [sourceKey]: fromBigin ? "bigin" : "books",
          [recordKey]: fromBigin
              ? {
                  id: biginContactId,
                  Full_Name: identity.name,
                  Mobile: identity.phone,
                  Email: identity.email,
                }
              : null,
          date: new Date().toISOString().slice(0, 10),
          line_items: lines.map((line) => ({
            item_id: line.item_id || line.id,
            quantity: Number(line.quantity),
            rate: Number(line.rate || 0),
            ...(line.discount ? { discount: Number(line.discount), discount_type: line.discount_type || 'percentage' } : {}),
            description: line.notes || line.product,
          })),
          notes: "Generated from GuruConnect voice session",
        }),
      });
      if (type !== 'purchase-order' && result && hasRecipient) {
        const record = result.quote || result.order || result.invoice || result.purchase_order || result.response?.estimate || result.response?.salesorder || result.response?.invoice || result.response?.purchaseorder;
        const recordId = record?.estimate_id || record?.salesorder_id || record?.invoice_id || record?.purchaseorder_id || record?.id;
        if (recordId) await sessionCall(`${path}/${recordId}/email`, { method: 'POST', body: JSON.stringify({ to_mail_ids: [recipient], subject: emailDetails.subject || `Your ${type.replace('-', ' ')} from GuruConnect`, body: emailDetails.message || `Please find your ${type.replace('-', ' ')} attached.` }) });
      }
      if (!hasRecipient) downloadSalesDocument(type, lines, result);
      setDocumentModalOpen(false);
      setError(hasRecipient ? `${type.replace("-", " ")} created and sent through the connected Zoho Books account.` : `${type.replace("-", " ")} created. No email is present for this contact, so the document was downloaded from GuruConnect.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setDocumentBusy("");
    }
  };
  const openCustomer = () => setCustomerOpen(true);
  const copyPhone = async () => {
    if (!selectedContact?.phone) return;
    try { await navigator.clipboard?.writeText(selectedContact.phone); } catch { /* Clipboard access is optional. */ }
  };
  const changeEnquiryStage = async (stage) => {
    if (!workspace || !selectedContact?.enquiry_id) return;
    setStageBusy(true);
    setError("");
    try {
      await recorderRequest(
        `/enquiries/${encodeURIComponent(selectedContact.enquiry_id)}/state`,
        workspace.id,
        {
          method: "PATCH",
          body: JSON.stringify({ stage }),
        },
        workspace.member_id,
      );
      setSelectedContact((current) =>
        current ? { ...current, enquiry_stage: stage } : current,
      );
      setContacts((current) =>
        current.map((contact) =>
          contact.id === selectedContact.id
            ? { ...contact, enquiry_stage: stage }
            : contact,
        ),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setStageBusy(false);
    }
  };
  const currentRecordingSnapshot = recordingSnapshot || (recorderSessions.find((item) => item.id === conversationId) || null);
  const recordingUiStatus = captureState.capture === "recording" || captureState.capture === "paused"
    ? recordingStatus({ capture_state: captureState.capture })
    : recordingStatus(currentRecordingSnapshot);
  const transcriptionUiStatus = currentRecordingSnapshot
    ? recordingStatus(currentRecordingSnapshot)
    : captureBusy
      ? { key: "ready", label: "Waiting for first window", detail: "Recording continues while the first processing window is assembled." }
      : recordingStatus(null);
  const aiInsightStatus = insights.some((item) => item.status === "failed")
    ? { key: "failed", label: "AI analysis failed", detail: "Previously completed transcript content is preserved." }
    : insights.some((item) => item.status === "running" || item.status === "pending")
      ? { key: "translating", label: "Updating recommendations", detail: "New translated content is being analyzed." }
      : transcriptPreview.length
        ? { key: "complete", label: "Insights available", detail: "Requirements and recommendations reflect completed chunks." }
        : { key: "ready", label: "Waiting for translated chunk", detail: "AI insights begin only after usable translated text arrives." };
  const statusIsClosed = (item) => {
    const value = String(item.recording_status || item.status || item.enquiry_stage || "").toLowerCase();
    return value.includes("closed") || value.includes("complete") || value.includes("failed") || value.includes("cancel");
  };
  const statusIsActive = (item) => {
    const value = String(item.recording_status || item.status || item.enquiry_stage || "").toLowerCase();
    return Boolean(item.session_id || item.sessionId || value.includes("record") || value.includes("process") || value.includes("active") || value.includes("transcrib"));
  };
  const matchesContactFilter = (item) => contactFilter === "all" || (contactFilter === "closed" ? statusIsClosed(item) : statusIsActive(item) && !statusIsClosed(item));
  const visibleContacts = contacts.filter(matchesContactFilter);
  const visibleChats = chats.filter(matchesContactFilter);
  const retryRecordingStage = async () => {
    if (!conversationId || !workspace) return;
    const stage = currentRecordingSnapshot?.transcription_state && ["failed", "blocked", "uncertain"].includes(currentRecordingSnapshot.transcription_state) ? "transcription" : "translation";
    try {
      await recorderRequest(`/conversations/${encodeURIComponent(conversationId)}/retry`, workspace.id, { method: "POST", body: JSON.stringify({ stage }) }, workspace.member_id);
      setError("");
      await refreshRecordingStatus(conversationId);
    } catch (e) { setError(e.message); }
  };
  return (
    <section className="voice-sessions-page">
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <div className="chat-layout voice-sessions-layout">
        <section className="chat-list">
          <div className="list-head">
            <span>Contacts & sessions</span>
            <button type="button" onClick={reset} disabled={busy}>
              <Plus size={20} />
            </button>
          </div>
          <div className="contact-search">
            <Search size={15} />
            <input
              placeholder="Search Bigin, Books, enquiries…"
              value={contactSearch}
              onChange={(event) => {
                setContactSearch(event.target.value);
                loadContacts(event.target.value);
              }}
            />
          </div>
          <div className="session-contact-filters" role="tablist" aria-label="Contact filters">
            {[['all', `All (${contacts.length + chats.length})`], ['active', 'Active'], ['closed', 'Closed']].map(([value, label]) => (
              <button
                type="button"
                role="tab"
                aria-selected={contactFilter === value}
                className={contactFilter === value ? "selected" : ""}
                onClick={() => setContactFilter(value)}
                key={value}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="contact-heading">
            <BookUser size={15} /> Recent contacts
          </div>
          {contactBusy && <PageLoader kind="contacts" label="Loading contacts" detail="Finding recent customer records." compact />}
          {visibleContacts.map((contact) => (
            <button
              type="button"
              className={`contact-row ${selectedContact?.id === contact.id ? "selected" : ""}`}
              onClick={() => chooseContact(contact)}
              disabled={busy}
              key={contact.id}
            >
              <b>{contact.name}</b>
              <small>
                {contact.phone || contact.email || "No phone or email"}
              </small>
              <em>{contact.sources.join(" · ")}</em>
              {contact.recording_status && <span className="contact-status-pill">{contact.recording_status}</span>}
            </button>
          ))}
          <div className="contact-heading session-heading">
            <Headphones size={15} /> Recent sessions
          </div>
          {visibleChats.map((chat) => (
            <button
              type="button"
              className={`chat-row ${session === chat.id ? "selected" : ""}`}
              onClick={() => void open(chat.id)}
              disabled={busy}
              key={chat.id}
            >
              <b>{chat.name}</b>
              <small>{chat.phone || chat.email || "Voice session"}</small>
              <em>{new Date(chat.updated_at).toLocaleString()}</em>
            </button>
          ))}
          {!contactBusy && !visibleContacts.length && !visibleChats.length && (
            <p className="session-list-empty">No {contactFilter === "all" ? "contacts or sessions" : `${contactFilter} contacts`} found.</p>
          )}
        </section>
        <section className="conversation">
          {selectedContact && (
            <div className="contact-record">
              <div>
                <strong>{selectedContact.name}</strong>
                {selectedContact.phone && (
                  <button
                    type="button"
                    className="contact-phone-link"
                    onClick={openCustomer}
                  >
                    <UserRound size={13} /> {selectedContact.phone}
                  </button>
                )}
                {selectedContact.phone && <button type="button" className="copy-phone-button" onClick={() => void copyPhone()} aria-label="Copy phone number" title="Copy phone number"><Copy size={13} /></button>}
                {!identity.email && !captureState.capture.startsWith("record") && !captureState.capture.startsWith("paused") && <input className="contact-email-entry" type="email" aria-label="Customer email address" placeholder="Customer email *" value={identity.email} onChange={(event) => { const email = event.target.value; setIdentity((current) => ({ ...current, email })); setSelectedContact((current) => current ? { ...current, email } : current); }} />}
              </div>
              <div className="session-status-strip" aria-label="Conversation processing status">
                <span className={`status-indicator status-${recordingUiStatus.key}`}><i />Recording <b>{recordingUiStatus.label}</b>{captureState.capture === "recording" && <em>· {new Date(captureState.elapsedSeconds * 1000).toISOString().slice(11, 19)}</em>}</span>
                <span className={`status-indicator status-${transcriptionUiStatus.key}`}><i />Transcription &amp; translation <b>{transcriptionUiStatus.label}</b></span>
                <span className={`status-indicator status-${aiInsightStatus.key}`}><i />AI insights <b>{aiInsightStatus.label}</b></span>
              </div>
              {selectedContact.enquiry_id && (
                <label className="session-stage-control">
                  <span>Enquiry stage</span>
                  <select
                    value={selectedContact.enquiry_stage || "new"}
                    onChange={(event) => void changeEnquiryStage(event.target.value)}
                    disabled={stageBusy}
                  >
                    <option value="new">New enquiry</option>
                    <option value="in_process">In process</option>
                    <option value="closed">Closed</option>
                    <option value="customer_did_not_show">Customer didn’t show up</option>
                  </select>
                </label>
              )}
            </div>
          )}
          <div className="messages">
            {session && (
              <div className="conversation-panel-heading">
                <div>
                  <strong>Conversation</strong>
                  <small>{messages.length ? `${messages.length} message${messages.length === 1 ? "" : "s"}` : "Live customer conversation"}</small>
                </div>
                {selectedContact?.enquiry_stage && <span>{String(selectedContact.enquiry_stage).replaceAll("_", " ")}</span>}
              </div>
            )}
            {!session && (
              <div className="welcome">
                <Headphones size={34} />
                <h2>Start a voice session</h2>
                <p>
                  Select a contact from Bigin, Books, enquiries, or recent
                  sessions. Start the session when you are ready to speak with
                  the customer; processing continues in the background.
                </p>
                <div className="identity">
                  <input
                    placeholder="Customer name"
                    value={identity.name}
                    onChange={(event) =>
                      setIdentity({ ...identity, name: event.target.value })
                    }
                  />
                  <input
                    inputMode="numeric"
                    maxLength="15"
                    placeholder="Phone number"
                    value={identity.phone}
                    onChange={(event) =>
                      setIdentity({
                        ...identity,
                        phone: event.target.value
                          .replace(/\D/g, "")
                          .slice(0, 15),
                      })
                    }
                  />
                  <input
                    type="email"
                    placeholder="Email address *"
                    required
                    value={identity.email}
                    onChange={(event) =>
                      setIdentity({ ...identity, email: event.target.value })
                    }
                  />
                  <button
                    type="button"
                    className="primary voice-open-button"
                    onClick={() => void prepareSession()}
                    disabled={busy || !identity.name.trim() || !identity.phone.trim() || !identity.email.trim()}
                  >
                    {busy ? "Starting…" : "Start session"}
                  </button>
                </div>
              </div>
            )}
            {session &&
              messages.map((message, index) => (
                <div className={`bubble ${message.role}`} key={index}>
                  <span>{message.content}</span>
                  {message.created_at && (
                    <time>{new Date(message.created_at).toLocaleString()}</time>
                  )}
                </div>
              ))}
            {session && !messages.length && (
              <div className="conversation-empty-state">
                <MessageSquare size={22} />
                <strong>No conversation messages yet</strong>
                <p>Start the session when you are ready. Customer and salesperson messages will appear here as they are captured.</p>
              </div>
            )}
            {session && (
              <div className="voice-processing-note">
                <Mic2 size={15} />
                <span>{transcriptionUiStatus.detail} {aiInsightStatus.detail}</span>
                {recordingUiStatus.key === "failed" && <button type="button" className="link-button" onClick={() => void retryRecordingStage()}>Retry processing</button>}
              </div>
            )}
            {session && recorderSessions.length > 0 && (
              <section className="session-recording-history" aria-label="Past recordings for this customer">
                <div className="session-recording-history-heading">
                  <strong>Past recordings for this customer</strong>
                  <span>{recorderSessions.length}</span>
                </div>
                {recorderSessions.map((recording) => (
                  <article className="session-recording-history-row" key={recording.id}>
                    <div>
                      <strong>{new Date(recording.created_at).toLocaleString()}</strong>
                      <small>{recording.duration_seconds ? `${Math.round(recording.duration_seconds)} sec` : "Not saved yet"} · {String(recording.final_state || recording.capture_state || "pending").replaceAll("_", " ")}</small>
                    </div>
                    <div className="session-recording-history-actions">
                      {workspace && recording.upload_state === "saved" && (
                        <ProgressivePlayback
                          conversationId={recording.id}
                          workspaceId={workspace.id}
                          memberId={workspace.member_id}
                          compact
                        />
                      )}
                      <button type="button" className="button button-quiet" onClick={() => setConversationId(recording.id)} disabled={busy}>Open transcript</button>
                      <button type="button" className="button button-danger-quiet" onClick={() => void deleteRecorderSession(recording.id)} disabled={busy} aria-label="Delete recording"><Trash2 size={14} />Delete</button>
                    </div>
                  </article>
                ))}
              </section>
            )}
          </div>
          {session && (
            <div className="voice-composer">
              <div className="session-workspace">
                <div className="session-workspace-left">
                  <section className="session-insights-panel">
                    <div className="session-section-heading"><div><small>LIVE CONVERSATION WORKSPACE</small><h3>Transcript & recommendations</h3></div><span className="session-status-pill">{transcriptPreview.length ? 'Processing updates' : 'Ready to record'}</span></div>
                    <div className="compact-transcript" aria-label="Latest transcript">
                      <div className="compact-transcript-heading"><strong>Latest transcript</strong><button type="button" className="button button-quiet" onClick={downloadTranscript} disabled={!transcriptPreview.length}><Download size={14} /> Download transcript</button></div>
                      {transcriptPreview.length ? transcriptPreview.map((turn) => <p key={turn.id}><time>{new Date((recorderSessions[0]?.created_at || Date.now())).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time><strong>{turn.speaker_ref || 'Conversation'}:</strong> {turn.english_text || turn.source_text}</p>) : <p className="notes-empty">Transcript will appear here after each completed audio chunk.</p>}
                    </div>
                    <div className="recommendations-heading"><strong><ShoppingCart size={15} /> Recommended products</strong><button type="button" className="link-button" onClick={() => setCartOpen(true)}>Cart ({cart.length})</button></div>
                    <div className="recommendations-row">{(recommendations.length ? recommendations : products).slice(0, 6).map((item) => <article className="recommendation-card" key={item.item_id || item.id || item.sku}><div className="recommendation-image">{item.image_url ? <img src={item.image_url} alt="" /> : <ShoppingCart size={22} />}</div><strong>{item.name || item.item_name}</strong><small>SKU: {item.sku || 'Not provided'}</small><b>{item.rate != null ? `₹${Number(item.rate).toLocaleString('en-IN')}` : 'Price on request'}</b><button type="button" className="primary" onClick={() => addToCart(item)} disabled={!item.item_id && !item.id}>Add to cart</button></article>)}{!recommendations.length && !products.length && <p className="notes-empty">Recommendations will appear when the conversation identifies a catalogue match.</p>}</div>
                    {cartOpen && <section className="session-cart"><div className="session-section-heading"><h3><ShoppingCart size={15} /> Cart · {cart.length} item{cart.length === 1 ? '' : 's'}</h3><div className="cart-heading-actions"><button type="button" className="button button-quiet" onClick={() => setCartProductSearchOpen(true)}>+ Add another product</button><button type="button" className="link-button" onClick={() => setCartOpen(false)}>Hide cart</button></div></div>{cart.map((line, index) => <div className="cart-line" key={line.item_id || line.sku || index}><div><strong>{line.product || line.name}</strong><small>{line.sku || 'No SKU'} · ₹{Number(line.rate || 0).toLocaleString('en-IN')}</small></div><input aria-label="Quantity" type="number" min="1" value={line.quantity} onChange={(event) => updateCart(index, 'quantity', event.target.value)} /><input aria-label="Discount" type="number" min="0" value={line.discount} onChange={(event) => updateCart(index, 'discount', event.target.value)} /><select aria-label="Discount type" value={line.discount_type} onChange={(event) => updateCart(index, 'discount_type', event.target.value)}><option value="percentage">%</option><option value="fixed">₹</option></select><b>₹{Math.max(0, Number(line.rate || 0) * Number(line.quantity || 0) - (line.discount_type === 'fixed' ? Number(line.discount || 0) : Number(line.rate || 0) * Number(line.quantity || 0) * Number(line.discount || 0) / 100)).toLocaleString('en-IN')}</b><button type="button" className="link-button" onClick={() => setCart((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Remove</button></div>)}<div className="cart-total"><span>Subtotal</span><b>₹{cartSubtotal.toLocaleString('en-IN')}</b><span>Discount</span><b>- ₹{cartDiscount.toLocaleString('en-IN')}</b><span>Final total</span><strong>₹{Math.max(0, cartSubtotal - cartDiscount).toLocaleString('en-IN')}</strong></div><button type="button" className="primary complete-cart" onClick={() => void openDocumentModal()} disabled={!cart.length}>Complete cart <ChevronRight size={15} /></button></section>}
                    {cartProductSearchOpen && <div className="session-modal-backdrop cart-search-backdrop" onClick={() => setCartProductSearchOpen(false)}><section className="product-search-modal" role="dialog" aria-modal="true" aria-labelledby="cart-product-search-title" onClick={(event) => event.stopPropagation()}><div className="customer-modal-heading"><div><small>PRODUCT CATALOGUE</small><h2 id="cart-product-search-title">Add another product</h2></div><button type="button" onClick={() => setCartProductSearchOpen(false)} aria-label="Close product search"><X size={20} /></button></div><div className="cart-product-search-form"><input autoFocus placeholder="Search by product name or SKU" value={cartSearch} onChange={(event) => setCartSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void searchCartProducts(); } }} /><button type="button" className="primary" onClick={() => void searchCartProducts()} disabled={!cartSearch.trim()}>Search</button></div><div className="cart-product-results">{cartSearchResults.map((item) => <button type="button" className="cart-product-result" key={item.item_id || item.id || item.sku} onClick={() => addToCart(item)}><span className="cart-product-result-image">{item.image_url ? <img src={item.image_url} alt="" /> : <ShoppingCart size={18} />}</span><span><strong>{item.name || item.item_name}</strong><small>{item.sku || "No SKU"} · {item.rate != null ? `₹${Number(item.rate).toLocaleString("en-IN")}` : "Price on request"}</small></span><b>Add</b></button>)}{cartSearch && !cartSearchResults.length && <p className="notes-empty">No catalogue products found.</p>}</div></section></div>}
                  </section>
                  <ConversationRecorder
                    key={`${user?.id}:${workspace?.id}:${session}:${recorderGeneration}`}
                    customerName={identity.name}
                    customerPhone={identity.phone}
                    customerEmail={identity.email}
                    title="Voice session"
                    initialConversationId={conversationId}
                    onConversationChange={linkConversation}
                    onBusyChange={setRecorderBusy}
                    onCaptureChange={setCaptureState}
                    onSaved={() => void recordingSaved()}
                    chunkSeconds={180}
                    grouped
                    sessionOnly
                  />
                </div>
                <aside
                  className={`session-notes${notesCollapsed ? " is-collapsed" : ""}`}
                >
                  <div className="notes-heading">
                    <button
                      type="button"
                      className="session-panel-toggle"
                      title={notesCollapsed ? "Expand executive notes" : "Collapse executive notes to show more history"}
                      aria-expanded={!notesCollapsed}
                      aria-controls="session-notes-content"
                      onClick={() =>
                        setNotesCollapsed((collapsed) => !collapsed)
                      }
                    >
                      <h3>Internal notes</h3>
                      <span>{notes.length}</span>
                      {notesCollapsed ? (
                        <ChevronRight size={18} aria-hidden="true" />
                      ) : (
                        <ChevronDown size={18} aria-hidden="true" />
                      )}
                    </button>
                  </div>
                  <div
                    id="session-notes-content"
                    className="session-panel-content"
                    hidden={notesCollapsed}
                  >
                    <div className="notes-list" aria-label="Timestamped internal notes">
                      {notes.length ? notes.map((note) => (
                        <article key={note.id}>
                          <div className="note-meta"><span className="note-avatar">You</span><strong>You</strong><time>{new Date(note.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div>
                          <p>{note.content}</p>
                        </article>
                      )) : <p className="notes-empty">No internal notes yet.</p>}
                    </div>
                    <div className="note-editor">
                      <textarea
                        value={noteText}
                        onChange={(event) => setNoteText(event.target.value)}
                        placeholder="Add an internal note…"
                        onKeyDown={(event) => {
                          if (
                            (event.ctrlKey || event.metaKey) &&
                            event.key === "Enter"
                          )
                            void addNote();
                        }}
                      />
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void addNote()}
                        disabled={!noteText.trim() || noteBusy}
                      >
                        {noteBusy ? "Saving…" : "Add note"}
                      </button>
                      {noteError && <p className="note-editor-error" role="alert">{noteError}</p>}
                    </div>
                  </div>
                </aside>
              </div>
              <div id="voice-document-actions" className="document-actions session-document-actions">
                <div><strong>Ready to create a Zoho document?</strong><small>{cart.length ? `${cart.length} item${cart.length === 1 ? "" : "s"} in cart · ₹${Math.max(0, cartSubtotal - cartDiscount).toLocaleString("en-IN")}` : "Add products to the cart first."}</small></div>
                <button type="button" className="primary" onClick={() => void openDocumentModal()} disabled={!!documentBusy || !cart.length}>Complete cart <ChevronRight size={15} /></button>
              </div>
              {conversationId && (
                <div className="session-transcript">
                  <ConversationTranscript conversationId={conversationId} />
                </div>
              )}
            </div>
          )}
        </section>
      </div>
      <CustomerModal
        contact={customerOpen ? selectedContact : null}
        onClose={() => setCustomerOpen(false)}
      />
      <SalesDocumentModal
        open={documentModalOpen}
        contact={selectedContact}
        customerType={customerType}
        cart={cart}
        subtotal={cartSubtotal}
        discount={cartDiscount}
        documentType={documentType}
        setDocumentType={setDocumentType}
        email={documentEmail}
        setEmail={setDocumentEmail}
        subject={documentSubject}
        setSubject={setDocumentSubject}
        message={documentMessage}
        setMessage={setDocumentMessage}
        busy={!!documentBusy}
        onClose={() => setDocumentModalOpen(false)}
        onBack={() => setDocumentModalOpen(false)}
        onSubmit={(type) => void createDocument(type, { email: documentEmail, subject: documentSubject, message: documentMessage, downloadOnly: !documentEmail.trim() })}
      />
    </section>
  );
}
