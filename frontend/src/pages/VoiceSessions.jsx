import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BookUser,
  ChevronDown,
  ChevronRight,
  Headphones,
  Mic2,
  Plus,
  Search,
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
  const { workspace } = useOffice();
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
    [contactSearch, setContactSearch] = useState("");
  const [session, setSession] = useState(null),
    [messages, setMessages] = useState([]),
    [recorderSessions, setRecorderSessions] = useState([]),
    [identity, setIdentity] = useState(initialIdentity),
    [conversationId, setConversationId] = useState(null),
    [selectedContact, setSelectedContact] = useState(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [contactBusy, setContactBusy] = useState(false),
    [stageBusy, setStageBusy] = useState(false);
  const [needsCollapsed, setNeedsCollapsed] = useState(false),
    [notesCollapsed, setNotesCollapsed] = useState(false);
  const [notes, setNotes] = useState([]),
    [noteText, setNoteText] = useState(""),
    [products, setProducts] = useState([]),
    [productRequirements, setProductRequirements] = useState([newProduct()]),
    [customerType, setCustomerType] = useState("unknown"),
    [documentContact, setDocumentContact] = useState(null),
    [customerOpen, setCustomerOpen] = useState(false),
    [documentBusy, setDocumentBusy] = useState("");
  const startedFromEnquiry = useRef(false);
  const load = () =>
    call("/chats")
      .then(setChats)
      .catch((e) => setError(e.message));
  const loadContacts = (value) => {
    setContactBusy(true);
    call(
      `/session-contacts${value ? `?search=${encodeURIComponent(value)}` : ""}`,
    )
      .then((data) => setContacts(data.contacts || []))
      .catch((e) => setError(e.message))
      .finally(() => setContactBusy(false));
  };
  const loadSessionExtras = async (id) => {
    try {
      const [noteData, contextData] = await Promise.all([
        call(`/voice-sessions/${id}/notes`),
        call(`/voice-sessions/${id}/context`),
      ]);
      setNotes(noteData.notes || []);
      setProductRequirements(
        contextData.product_requirements?.length
          ? contextData.product_requirements
          : [newProduct()],
      );
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
    loadContacts("");
  }, []);
  const open = (id) => {
    setSession(id);
    setError("");
    call(`/voice-sessions/${id}/history`)
      .then((data) => {
        setMessages(data.messages || []);
        setRecorderSessions(data.recorder_sessions || []);
        setConversationId(data.conversation_id || null);
        setIdentity({
          name: data.session?.name || "",
          phone: data.session?.phone || "",
          email: data.session?.email || "",
        });
        setSelectedContact({
          id: `session:${id}`,
          name: data.session?.name || "",
          phone: data.session?.phone || "",
          email: data.session?.email || "",
          sources: ["Session"],
          records: {
            ...(data.session?.books_contact_id
              ? { books: data.session.books_contact_id }
              : {}),
            ...(data.session?.bigin_contact_id
              ? { bigin: data.session.bigin_contact_id }
              : {}),
          },
        });
        void loadSessionExtras(id);
      })
      .catch((e) => setError(e.message));
  };
  const prepareSession = async (contact = selectedContact) => {
    const next = contact
      ? {
          name: contact.name,
          phone: contact.phone || "",
          email: contact.email || "",
        }
      : identity;
    if (!next.name.trim() || (!next.phone.trim() && !next.email.trim())) {
      setError("Enter a customer name and phone number or email first.");
      return;
    }
    setBusy(true);
    setError("");
    setIdentity(next);
    // Do not let the previous contact's failed/pending recording block this
    // contact while the phone-based session lookup is in flight.
    setConversationId(null);
    try {
      const data = await call("/voice-sessions", {
        method: "POST",
        body: JSON.stringify(next),
      });
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
      setSession(data.session.id);
      setMessages(data.messages || []);
      setRecorderSessions(data.recorder_sessions || []);
      setConversationId(data.conversation_id || null);
      await load();
      await loadContacts(contactSearch);
      await loadSessionExtras(data.session.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
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
    setSession(null);
    setMessages([]);
    setRecorderSessions([]);
    setConversationId(null);
    setSelectedContact(null);
    setDocumentContact(null);
    setIdentity({ name: "", phone: "", email: "" });
    setNotes([]);
    setProductRequirements([newProduct()]);
    setError("");
  };
  const linkConversation = async (id) => {
    setConversationId(id);
    if (session)
      await call(`/voice-sessions/${session}/conversation`, {
        method: "POST",
        body: JSON.stringify({ conversation_id: id }),
      });
  };
  const deleteRecorderSession = async (id) => {
    if (!workspace || !window.confirm("Delete this recording and its transcript?")) return;
    setBusy(true);
    setError("");
    try {
      await recorderRequest(`/conversations/${encodeURIComponent(id)}`, workspace.id, { method: "DELETE" });
      setRecorderSessions((current) => current.filter((item) => item.id !== id));
      if (conversationId === id) {
        setConversationId(null);
        if (session) await call(`/voice-sessions/${session}/conversation`, { method: "POST", body: JSON.stringify({ conversation_id: null }) });
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
      void call(`/voice-sessions/${session}/context`, {
        method: "PUT",
        body: JSON.stringify({ product_requirements: next }),
      }).catch((e) => setError(e.message));
  };
  const searchProducts = async (value) => {
    if (!value.trim()) return;
    try {
      const data = await call(
        `/products?search=${encodeURIComponent(value.trim())}`,
      );
      setProducts(data.items || []);
    } catch (e) {
      setError(e.message);
    }
  };
  const resolveDocumentContact = async () => {
    const query = (identity.phone || identity.email || "").trim();
    if (!query) return;
    try {
      const [customers, vendors] = await Promise.all([
        call(
          `/document-contacts?role=customer&search=${encodeURIComponent(query)}`,
        ),
        call(
          `/document-contacts?role=vendor&search=${encodeURIComponent(query)}`,
        ),
      ]);
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
  const selectProduct = (index, item) => {
    updateProduct(index, "product", item.name || item.item_name || "");
    updateProduct(index, "sku", item.sku || "");
    updateProduct(index, "item_id", item.item_id || "");
    setProducts([]);
  };
  const addNote = async () => {
    if (!session || !noteText.trim()) return;
    setBusy(true);
    try {
      const data = await call(`/voice-sessions/${session}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: noteText.trim() }),
      });
      setNotes((current) => [...current, data.note]);
      setNoteText("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
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
  const createDocument = async (type) => {
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
    const lines = productRequirements.filter(
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
      await call(path, {
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
            item_id: line.item_id,
            quantity: Number(line.quantity),
            rate: Number(line.rate || 0),
            description: line.notes || line.product,
          })),
          notes: "Generated from GuruConnect voice session",
        }),
      });
      setError(`${type.replace("-", " ")} generated successfully.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setDocumentBusy("");
    }
  };
  const openCustomer = () => setCustomerOpen(true);
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
            <button type="button" onClick={reset}>
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
          <div className="contact-heading">
            <BookUser size={15} /> Recent contacts
          </div>
          {contactBusy && <PageLoader kind="contacts" label="Loading contacts" detail="Finding recent customer records." compact />}
          {contacts.map((contact) => (
            <button
              type="button"
              className={`contact-row ${selectedContact?.id === contact.id ? "selected" : ""}`}
              onClick={() => chooseContact(contact)}
              key={contact.id}
            >
              <b>{contact.name}</b>
              <small>
                {contact.phone || contact.email || "No phone or email"}
              </small>
              <em>{contact.sources.join(" · ")}</em>
            </button>
          ))}
          <div className="contact-heading session-heading">
            <Headphones size={15} /> Recent sessions
          </div>
          {chats.map((chat) => (
            <button
              type="button"
              className={`chat-row ${session === chat.id ? "selected" : ""}`}
              onClick={() => open(chat.id)}
              key={chat.id}
            >
              <b>{chat.name}</b>
              <small>{chat.phone || chat.email || "Voice session"}</small>
              <em>{new Date(chat.updated_at).toLocaleString()}</em>
            </button>
          ))}
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
            {!session && (
              <div className="welcome">
                <Headphones size={34} />
                <h2>Start a voice session</h2>
                <p>
                  Select a contact from Bigin, Books, enquiries, or recent
                  sessions. Audio uses GuruConnect’s managed WAV pipeline for
                  ElevenLabs transcription and English translation.
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
                    placeholder="Email fallback"
                    value={identity.email}
                    onChange={(event) =>
                      setIdentity({ ...identity, email: event.target.value })
                    }
                  />
                  <button
                    type="button"
                    className="primary voice-open-button"
                    onClick={() => void prepareSession()}
                    disabled={busy}
                  >
                    {busy ? "Opening…" : "Open recorder"}
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
            {session && (
              <div className="voice-processing-note">
                <Mic2 size={15} />
                <span>
                  Saved audio is sent to ElevenLabs Scribe v2. Non-English
                  source turns are translated to English by the configured
                  OpenAI Luna path.
                </span>
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
                  <section
                    className={`needs-panel${needsCollapsed ? " is-collapsed" : ""}`}
                  >
                    <div className="needs-heading">
                      <button
                        type="button"
                        className="session-panel-toggle"
                        title={needsCollapsed ? "Expand product requirements" : "Collapse product requirements to show more history"}
                        aria-expanded={!needsCollapsed}
                        aria-controls="session-needs-content"
                        onClick={() =>
                          setNeedsCollapsed((collapsed) => !collapsed)
                        }
                      >
                        <div>
                          <small>WHAT THEY NEED</small>
                          <h3>Product requirements</h3>
                          <p>
                            Add one or more products the customer is interested
                            in.
                          </p>
                        </div>
                        <span>03</span>
                        {needsCollapsed ? (
                          <ChevronRight size={18} aria-hidden="true" />
                        ) : (
                          <ChevronDown size={18} aria-hidden="true" />
                        )}
                      </button>
                    </div>
                    <div
                      id="session-needs-content"
                      className="session-panel-content"
                      hidden={needsCollapsed}
                    >
                      <p className="catalogue-note">
                        Search the product catalogue, then capture the
                        requested quantity, pricing context, and stock needs.
                      </p>
                      {productRequirements.map((line, index) => (
                        <ProductRequirement
                          key={index}
                          line={line}
                          index={index}
                          products={products}
                          onChange={(key, value) =>
                            updateProduct(index, key, value)
                          }
                          onSearch={searchProducts}
                          onSelect={(item) => selectProduct(index, item)}
                          onRemove={() =>
                            setProductRequirements((current) =>
                              current.filter(
                                (_, itemIndex) => itemIndex !== index,
                              ),
                            )
                          }
                        />
                      ))}
                      <button
                        type="button"
                        className="add-product-button"
                        onClick={() =>
                          setProductRequirements((current) => [
                            ...current,
                            newProduct(),
                          ])
                        }
                      >
                        + Add another product
                      </button>
                      <label className="customer-type-select">
                        Document party
                        <select
                          value={customerType}
                          onChange={(event) =>
                            setCustomerType(event.target.value)
                          }
                        >
                          <option value="customer">Customer</option>
                          <option value="dealer">Dealer</option>
                        </select>
                      </label>
                      <div className="document-actions">
                        <button
                          type="button"
                          onClick={() => void createDocument("quote")}
                          disabled={!!documentBusy}
                        >
                          Generate quote
                        </button>
                        <button
                          type="button"
                          onClick={() => void createDocument("invoice")}
                          disabled={!!documentBusy}
                        >
                          Generate invoice
                        </button>
                        <button
                          type="button"
                          onClick={() => void createDocument("sales-order")}
                          disabled={!!documentBusy}
                        >
                          Generate sales order
                        </button>
                        {customerType === "dealer" && (
                          <button
                            type="button"
                            onClick={() =>
                              void createDocument("purchase-order")
                            }
                            disabled={!!documentBusy}
                          >
                            Generate purchase order
                          </button>
                        )}
                      </div>
                    </div>
                  </section>{" "}
                  <ConversationRecorder
                    customerName={identity.name}
                    customerPhone={identity.phone}
                    customerEmail={identity.email}
                    title="Voice session"
                    initialConversationId={conversationId}
                    onConversationChange={linkConversation}
                    onBusyChange={setBusy}
                    grouped
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
                      <h3>Executive notes</h3>
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
                    <div className="notes-list" aria-label="Timestamped executive notes">
                      {notes.length ? notes.map((note) => (
                        <article key={note.id}>
                          <p>{note.content}</p>
                          <time>{new Date(note.created_at).toLocaleString()}</time>
                        </article>
                      )) : <p className="notes-empty">No executive notes yet.</p>}
                    </div>
                    <div className="note-editor">
                      <textarea
                        value={noteText}
                        onChange={(event) => setNoteText(event.target.value)}
                        placeholder="Write a timestamped note…"
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
                        disabled={!noteText.trim() || busy}
                      >
                        Save note
                      </button>
                    </div>
                  </div>
                </aside>
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
    </section>
  );
}
