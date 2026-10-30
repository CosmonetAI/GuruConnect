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
import LiveSessionWorkspace, { ProductCartAction, ProductMatchDetails, Stock, productNeedsConfirmation, productOutOfStock, productPriceAvailable, productReviewFingerprint } from "../components/LiveSessionWorkspace";
import SalesDocumentModal from "../components/SalesDocumentModal";
import { request as recorderRequest } from "../services/officeApi";
import { useOffice } from "../contexts/OfficeContext";
import { supabase } from "../services/officeApi";
import "../zoho/styles.css";
import "../styles/voice-sessions.css";
import "../styles/session-workspace.css";
import "../styles/live-session-page.css";

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

// Capture and speech processing have independent lifecycles. A running microphone
// is not evidence that a provider has received or translated an audio window.
/**
 * @param {object} [options]
 * @param {import('../recording/status').RecordingSnapshot | null} [options.snapshot]
 * @param {{capture?: string}} [options.captureState]
 * @param {{speech_configured?: boolean, translation_configured?: boolean} | null} [options.providerReadiness]
 * @param {{stage?: string, message?: string, code?: string} | null} [options.processingStatus]
 * @param {string | null} [options.processingMode]
 * @param {Array<{english_text?: string | null, source_text?: string | null}>} [options.turns]
 */
export function liveProcessingStatus({ snapshot = null, captureState = {}, providerReadiness = null, processingStatus = null, processingMode = null, turns = [] } = {}) {
  const transcription = snapshot?.transcription_state ?? snapshot?.transcriptionState;
  const english = snapshot?.english_state ?? snapshot?.englishState;
  const finalState = snapshot?.final_state ?? snapshot?.finalState;
  const capture = ["recording", "paused"].includes(captureState.capture) ? captureState.capture : snapshot?.capture_state;
  const capturing = ["recording", "paused"].includes(capture);
  const hasEnglish = turns.some((turn) => Boolean(turn.english_text?.trim()));
  const status = (key, label, detail, available = hasEnglish) => ({ key, label, detail, available });
  if (finalState === "complete" && english === "complete") return status("complete", "Processing complete", "The recording transcript and English translation are available.", true);

  const stage = processingStatus?.stage;
  const failed = ["failed", "blocked", "uncertain"];
  // The live endpoint can see per-window jobs that a conversation snapshot does
  // not include, including a retry of a previously blocked window.
  if (stage === "transcribing") return status("transcribing", "Transcribing", "The speech service is processing an audio window.");
  if (stage === "translating") return status("translating", "Translating", "The English translation is being prepared; completed text remains visible.");
  if (failed.includes(transcription)) return status("failed", transcription === "blocked" ? "Transcription unavailable" : "Transcription needs attention", snapshot?.processing_error_code || "Saved audio is retained. Retry transcription when the speech service is available.");
  if (failed.includes(english)) return status("failed", english === "blocked" ? "Translation unavailable" : "Translation needs attention", snapshot?.processing_error_code || "The source transcript is retained. Retry translation when the service is available.");
  if (failed.includes(stage)) return status("failed", "Speech processing needs attention", processingStatus.message || processingStatus.code || "Saved audio and completed text are retained.");
  if (providerReadiness?.speech_configured === false) return status("failed", "Transcription unavailable", "Audio capture and saving remain available. Configure the speech service to process saved audio.");
  if (providerReadiness?.translation_configured === false) return status("failed", "Translation unavailable", "Audio capture and saving remain available. Configure the translation service for English text.");
  if (stage === "up_to_date") return status("ready", "Live translation up to date", "Showing processed speech. New text follows as the next audio window is ready.");

  if (stage === "complete" && !capturing) return status("complete", "Processing complete", "The recording transcript and English translation are available.", true);
  if (processingMode === "finalized_only" && capturing) return status("ready", "Translation starts after recording ends", "This recording is configured for processing after the audio is saved.");

  if (["running", "processing"].includes(transcription)) return status("transcribing", "Transcribing", "The speech service is processing the latest audio.");
  if (["running", "processing"].includes(english)) return status("translating", "Translating", "Preparing the English version of the latest speech.");
  if (capturing && hasEnglish && !turns.some((turn) => turn.source_text && turn.english_text == null)) return status("ready", "Live translation up to date", "Showing processed speech. New text follows as the next audio window is ready.");
  if (capturing && (stage === "waiting" || !snapshot || transcription === "pending" || !transcription)) return status("ready", "Waiting for first audio window", "Audio capture is active. Text appears when the first short audio window has been processed.");
  if (transcription === "queued") return status("transcribing", "Transcription queued", "Saved audio is waiting for the speech service.");
  if (["pending", "queued"].includes(english) && (["complete", "available"].includes(transcription) || turns.length)) return status("translating", "Translation queued", "The source transcript is available; its English translation is waiting to be processed.");
  if (capturing) return status("ready", "Waiting for next audio window", "Audio capture is active. Completed speech and translation remain visible.");
  if (snapshot?.upload_state && snapshot.upload_state !== "saved") return status("uploading", "Saving audio", "Audio is being saved for processing.");
  if (snapshot) return status("ready", "Waiting for processing", "Audio is saved. Waiting for the next processing update.");
  return status("ready", "Ready to record", "No recording is active.");
}

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
  const { user, workspace, localMode, setCaptureBusy } = useOffice();
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
  const [documentOptions, setDocumentOptions] = useState(null), [documentCart, setDocumentCart] = useState([]), [documentError, setDocumentError] = useState(""), [documentFeedback, setDocumentFeedback] = useState(""), [documentDelivery, setDocumentDelivery] = useState("email"), [documentResult, setDocumentResult] = useState(null);
  const [documentPendingCreation, setDocumentPendingCreation] = useState(false);
  const documentSnapshot = useRef(null), documentInFlight = useRef(false), currentDocumentScope = useRef("");
  const documentDrafts = useRef(new Map());
  currentDocumentScope.current = `${user?.id}:${workspace?.id}:${workspace?.member_id || user?.id}:${session}`;
  const [recorderGeneration, setRecorderGeneration] = useState(0);
  const [captureBusy, setRecorderBusy] = useState(false);
  const [captureState, setCaptureState] = useState({ capture: "idle", elapsedSeconds: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false });
  const [recordingSnapshot, setRecordingSnapshot] = useState(null);
  const [liveProcessing, setLiveProcessing] = useState(null);
  const [liveAnalysis, setLiveAnalysis] = useState("waiting_for_translation");
  const [liveKeywords, setLiveKeywords] = useState([]);
  const [liveGuidance, setLiveGuidance] = useState({ requirements: [], clarificationSuggestions: [], currentIntent: '', agentGuidance: [], catalogueStatus: '' });
  const [catalogueNotices, setCatalogueNotices] = useState([]);
  const [liveNotice, setLiveNotice] = useState("");
  const [customIntents, setCustomIntents] = useState([]);
  const [hiddenIntents, setHiddenIntents] = useState([]);
  const [activeTab, setActiveTab] = useState("conversation");
  const [archivedConversationId, setArchivedConversationId] = useState(null);
  const [viewProduct, setViewProduct] = useState(null);
  useEffect(() => {
    setViewProduct((current) => {
      if (!current) return current;
      const latest = recommendations.find((item) => (item.item_id || item.id || item.sku) === (current.item_id || current.id || current.sku));
      return latest ? { ...current, ...latest } : current;
    });
  }, [recommendations]);
  const [cartSearchBusy, setCartSearchBusy] = useState(false);
  const [cartSearchError, setCartSearchError] = useState("");
  const [intentSearch, setIntentSearch] = useState(false);
  const [contextHydrated, setContextHydrated] = useState(null);
  const [cartSaveError, setCartSaveError] = useState("");
  const [cartSaving, setCartSaving] = useState(false);
  const busy = sessionBusy || captureBusy || cartSaving || Boolean(documentBusy);
  const busyRef = useRef(false); busyRef.current = busy;
  const alive = useRef(true);
  const selection = useRef(0);
  const contactRequest = useRef(0);
  const controllers = useRef(new Set());
  const sessionRef = useRef(session); sessionRef.current = session;
  const conversationRef = useRef(conversationId); conversationRef.current = conversationId;
  const captureRef = useRef(captureState); captureRef.current = captureState;
  const catalogueSearch = useRef(0);
  const refreshLive = useRef(null);
  const contextSaves = useRef(new Map());
  const blocker = useBlocker(busy);
  useEffect(() => { if (blocker.state === 'blocked') { setError('Finish recording and saving this session before leaving.'); blocker.reset(); } }, [blocker]);
  useEffect(() => {
    if (!cartSaving && !documentBusy) return;
    const preventLoss = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', preventLoss);
    return () => window.removeEventListener('beforeunload', preventLoss);
  }, [cartSaving, documentBusy]);
  useEffect(() => { setCaptureBusy(busy); return () => setCaptureBusy(false); }, [busy, setCaptureBusy]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; ++selection.current; controllers.current.forEach(controller => controller.abort()); }; }, []);
  const sessionCall = async (path, options = {}) => {
    const controller = new AbortController(); controllers.current.add(controller);
    try { return await call(path, { ...options, signal: options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal, headers: { ...options.headers, 'X-Workspace-Member-ID': workspace?.member_id || user?.id } }, workspace?.id); }
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
    const requirements = contextData.product_requirements?.length ? contextData.product_requirements : [newProduct()];
    const savedCart = contextData.cart || [];
    contextSaves.current.set(id, { saved: JSON.stringify({ product_requirements: requirements, cart: savedCart }), running: false, pending: null });
    setProductRequirements(requirements); setCart(savedCart); setCartOpen(savedCart.length > 0); setContextHydrated(id);
  };
  const clearLiveWorkspace = () => {
    documentSnapshot.current = null; setDocumentModalOpen(false); setDocumentOptions(null); setDocumentCart([]); setDocumentError(""); setDocumentFeedback(""); setDocumentResult(null); setDocumentBusy(""); setDocumentPendingCreation(false);
    setContextHydrated(null); setCartSaveError("");
    setNoteBusy(false);
    setTranscriptPreview([]); setInsights([]); setRecommendations([]); setProducts([]);
    setLiveKeywords([]); setCustomIntents([]); setHiddenIntents([]);
    setLiveGuidance({ requirements: [], clarificationSuggestions: [], currentIntent: '', agentGuidance: [], catalogueStatus: '' });
    setLiveAnalysis("waiting_for_translation"); setLiveProcessing(null); setCatalogueNotices([]); setLiveNotice("");
    setCart([]); setCartOpen(false); setDocumentContact(null); setCustomerType("unknown");
    setActiveTab("conversation"); setArchivedConversationId(null);
    setCartProductSearchOpen(false); setViewProduct(null); setCartSearchError("");
    setCaptureState({ capture: "idle", elapsedSeconds: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false });
  };
  const refreshRecordingStatus = async (id = conversationId) => {
    if (!id || !workspace) return;
    try {
      const data = await recorderRequest(`/conversations/${encodeURIComponent(id)}`, workspace.id, {}, workspace.member_id);
      if (alive.current && id === conversationRef.current) setRecordingSnapshot(data);
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
    setContextHydrated(null);
    const ticket = ++selection.current; busyRef.current = true; setBusy(true); setError('');
    try {
      const data = await sessionCall(`/voice-sessions/${id}/history`);
      if (!alive.current || ticket !== selection.current) return;
      clearLiveWorkspace();
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
    if (!next.name.trim() || !next.phone.trim()) {
      setError("Enter the customer’s name and phone number before starting the session.");
      return;
    }
    busyRef.current = true; setBusy(true);
    setError("");
    clearLiveWorkspace();
    setIdentity(next);
    setNoteText(""); setNoteError("");
    // Do not let the previous contact's failed/pending recording block this
    // contact while the phone-based session lookup is in flight.
    setConversationId(null);
    conversationRef.current = null;
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
    clearLiveWorkspace();
    setProductRequirements([newProduct()]);
    setError("");
  };
  const linkConversation = async (id) => {
    const selectedSession = session;
    if (!alive.current || sessionRef.current !== selectedSession) throw new Error('The selected customer changed. Reopen the saved recording from Conversation history.');
    if (selectedSession) await sessionCall(`/voice-sessions/${selectedSession}/conversation`, { method: 'POST', body: JSON.stringify({ conversation_id: id }) });
    if (alive.current && sessionRef.current === selectedSession) {
      const changed = conversationRef.current !== id;
      conversationRef.current = id;
      setConversationId(id);
      if (changed) {
        setTranscriptPreview([]); setInsights([]); setRecommendations([]); setLiveKeywords([]);
        setLiveGuidance({ requirements: [], clarificationSuggestions: [], currentIntent: '', agentGuidance: [], catalogueStatus: '' });
        setRecordingSnapshot(null); setLiveProcessing(null); setLiveAnalysis("waiting_for_translation"); setCatalogueNotices([]);
      }
    }
  };
  const recordingSaved = async () => {
    const id = sessionRef.current; const ticket = selection.current;
    try {
      const data = await sessionCall(`/voice-sessions/${id}/history`);
      if (alive.current && ticket === selection.current && id === sessionRef.current) { setRecorderSessions(data.recorder_sessions || []); void refreshRecordingStatus(data.conversation_id || conversationId); void refreshLive.current?.(); }
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
    const query = cartSearch.trim();
    if (!query) { setCartSearchResults([]); return; }
    const ticket = ++catalogueSearch.current;
    const selected = sessionRef.current;
    setCartSearchBusy(true); setCartSearchError("");
    try {
      const data = await sessionCall(`/products?search=${encodeURIComponent(query)}`);
      if (!alive.current || ticket !== catalogueSearch.current || selected !== sessionRef.current) return;
      setCartSearchResults(data.items || []);
      if (intentSearch) {
        setCustomIntents((current) => [...new Set([...current, query])]);
        setProducts((current) => [...new Map([...current, ...(data.items || [])].map((item) => [item.item_id || item.id, item])).values()]);
      }
    } catch (e) {
      if (alive.current && ticket === catalogueSearch.current && selected === sessionRef.current) setCartSearchError(e.message);
    } finally {
      if (alive.current && ticket === catalogueSearch.current && selected === sessionRef.current) setCartSearchBusy(false);
    }
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
      if (alive.current && ticket === selection.current) {
        setCustomerType("unknown"); setDocumentContact(null);
      }
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
    if (!session || !conversationId || !workspace) return;
    const controller = new AbortController();
    let timer, inFlight = false, latest = null;
    const isCurrent = () => !controller.signal.aborted && alive.current && sessionRef.current === session && conversationRef.current === conversationId;
    const refresh = async () => {
      if (!isCurrent() || inFlight) return;
      window.clearTimeout(timer); inFlight = true;
      try {
        const data = await sessionCall(`/voice-sessions/${session}/live`, { signal: controller.signal });
        if (!isCurrent() || (data.conversation_id && data.conversation_id !== conversationId)) return;
        latest = data;
        if (Array.isArray(data.transcript_preview)) setTranscriptPreview(data.transcript_preview);
        if (Array.isArray(data.insights)) setInsights(data.insights);
        if (Array.isArray(data.keywords)) setLiveKeywords(data.keywords);
        if (Array.isArray(data.recommendations)) setRecommendations(data.recommendations);
        setLiveGuidance({ requirements: Array.isArray(data.requirements) ? data.requirements : [], clarificationSuggestions: Array.isArray(data.clarification_suggestions) ? data.clarification_suggestions : [], currentIntent: data.current_intent || '', agentGuidance: data.agent_guidance || [], catalogueStatus: data.catalogue_status || '' });
        if (data.recording) setRecordingSnapshot(data.recording);
        setLiveProcessing({ providerReadiness: data.provider_readiness, processingStatus: data.processing_status, processingMode: data.processing_mode });
        if (data.analysis_status) setLiveAnalysis(data.analysis_status);
        setCatalogueNotices(data.notices || []); setLiveNotice("");
      } catch (e) {
        if (isCurrent() && e.name !== "AbortError") setLiveNotice("Live updates are reconnecting. Recording and saved audio are preserved.");
      } finally {
        inFlight = false;
        if (isCurrent()) {
          const capturing = ["recording", "paused"].includes(captureRef.current.capture);
          const processing = latest && (latest.recording?.final_state === "queued" || latest.recording?.final_state === "running" || ["pending", "running"].includes(latest.analysis_status) || latest.catalogue_status === "searching");
          timer = window.setTimeout(refresh, capturing || processing ? 2000 : 8000);
        }
      }
    };
    refreshLive.current = refresh;
    const onVisible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    void refresh();
    const channel = supabase?.channel(`voice-live-${conversationId}`)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'conversations', filter: `id=eq.${conversationId}` }, () => { void refresh(); })
      .on('postgres_changes', { event: '*', schema: 'public', table: 'conversation_extractions', filter: `conversation_id=eq.${conversationId}` }, () => { void refresh(); })
      .subscribe();
    return () => { controller.abort(); window.clearTimeout(timer); refreshLive.current = null; document.removeEventListener("visibilitychange", onVisible); if (channel) void supabase.removeChannel(channel); };
  }, [session, conversationId, workspace?.id, workspace?.member_id]);
  useEffect(() => {
    if (!session || contextHydrated !== session || sessionBusy) return;
    const entry = contextSaves.current.get(session);
    if (!entry) return;
    const body = JSON.stringify({ product_requirements: productRequirements, cart });
    if ((!entry.running && body === entry.saved) || body === entry.pending) return;
    entry.pending = body;
    if (entry.running) return;
    entry.running = true; setCartSaving(true);
    void (async () => {
        try {
          while (entry.pending) {
            const pendingBody = entry.pending; entry.pending = null;
            if (pendingBody === entry.saved) continue;
            await sessionCall(`/voice-sessions/${session}/context`, { method: 'PUT', body: pendingBody });
            entry.saved = pendingBody;
            if (alive.current && sessionRef.current === session) setCartSaveError("");
          }
        } catch (e) {
          if (alive.current && sessionRef.current === session) setCartSaveError("Your latest cart changes could not be saved. Keep this session open and retry by updating the cart.");
        } finally { entry.running = false; if (alive.current && sessionRef.current === session) setCartSaving(false); }
    })();
  }, [session, cart, productRequirements, contextHydrated, sessionBusy]);
  const selectProduct = (index, item) => {
    updateProduct(index, "product", item.name || item.item_name || "");
    updateProduct(index, "sku", item.sku || "");
    updateProduct(index, "item_id", item.item_id || "");
    setProducts([]);
  };
  const addToCart = (item) => {
    if (sessionBusy || contextHydrated !== session) return;
    const id = item.item_id || item.id || item.sku;
    const latest = recommendations.find((candidate) => (candidate.item_id || candidate.id || candidate.sku) === id);
    if (latest) item = { ...item, ...latest, match_confirmation_acknowledged: item.match_confirmation_acknowledged && productReviewFingerprint(item) === productReviewFingerprint(latest) };
    const rate = Number(item.rate ?? item.price);
    if (!id || !productPriceAvailable(item) || productOutOfStock(item) || (productNeedsConfirmation(item) && !item.match_confirmation_acknowledged)) return;
    setCart((current) => {
      const found = current.find((line) => (line.item_id || line.id || line.sku) === id);
      if (found) return current.map((line) => line === found ? { ...line, quantity: Number(line.quantity || 1) + 1 } : line);
      return [...current, { ...item, item_id: item.item_id || item.id, product: item.name || item.item_name, quantity: 1, discount: 0, discount_type: 'percentage', rate }];
    });
    setCartOpen(true);
    setCartProductSearchOpen(false);
  };
  const updateCart = (index, key, value) => setCart((current) => current.map((line, i) => {
    if (sessionBusy || contextHydrated !== session) return line;
    if (i !== index) return line;
    const next = { ...line, [key]: key === 'quantity' ? Math.max(1, Math.floor(Number(value) || 1)) : key === 'discount' ? Math.max(0, Number(value) || 0) : value };
    next.discount = Math.min(Number(next.discount) || 0, next.discount_type === 'fixed' ? Number(next.rate) * Number(next.quantity) : 100);
    return next;
  }));
  const removeCartItem = (index) => { if (sessionBusy || contextHydrated !== session) return; setCart((current) => current.filter((_, i) => i !== index)); if (cart.length <= 1) setCartOpen(false); };
  const cartSubtotal = cart.reduce((sum, line) => sum + Number(line.rate || 0) * Number(line.quantity || 0), 0);
  const cartDiscount = cart.reduce((sum, line) => sum + (line.discount_type === 'fixed' ? Number(line.discount || 0) : Number(line.rate || 0) * Number(line.quantity || 0) * Number(line.discount || 0) / 100), 0);
  const openDocumentModal = async () => {
    if (!session || !cart.length || documentInFlight.current || contextHydrated !== session) return;
    if (!cart.every(productPriceAvailable)) { setCartSaveError('Confirm catalogue prices before completing this cart.'); return; }
    const scope = currentDocumentScope.current, selectedSession = session, ticket = selection.current;
    const unresolved = documentDrafts.current.get(scope);
    const sameCart = unresolved && JSON.stringify(unresolved.cart.map((line) => [line.item_id || line.id, line.quantity, line.rate, line.discount, line.discount_type])) === JSON.stringify(cart.map((line) => [line.item_id || line.id, line.quantity, line.rate, line.discount, line.discount_type]));
    if (unresolved?.pendingCreation || (sameCart && unresolved?.result && ["failed", "uncertain"].includes(unresolved.result.delivery?.status))) {
      documentSnapshot.current = unresolved;
      setDocumentOptions(unresolved.options); setDocumentCart(unresolved.cart); setDocumentResult(unresolved.result); setDocumentPendingCreation(Boolean(unresolved.pendingCreation)); setDocumentError(unresolved.error || "");
      const original = unresolved.createAction.body, fields = unresolved.emailFields || original;
      setDocumentType(original.type); setDocumentEmail(fields.recipient || ""); setDocumentSubject(fields.subject); setDocumentMessage(fields.message); setDocumentDelivery(original.delivery); setDocumentModalOpen(true);
      return;
    }
    const lines = cart.map((line) => ({ ...line }));
    documentInFlight.current = true; setDocumentBusy("options"); setError(""); setDocumentFeedback("");
    try {
      const options = await sessionCall(`/voice-sessions/${selectedSession}/sales-documents/options`);
      if (!alive.current || currentDocumentScope.current !== scope || sessionRef.current !== selectedSession || ticket !== selection.current) return;
      const supportedTypes = (options.document_types || []).filter((type) => ["invoice", "quote", "sales-order"].includes(type));
      if (!supportedTypes.length) throw new Error("No sales document types are available for this customer.");
      const customer = { ...identity, ...(options.customer || {}) };
      const snapshot = { scope, session: selectedSession, cart: lines, options: { ...options, customer, document_types: supportedTypes }, result: null };
      documentSnapshot.current = snapshot;
      setDocumentOptions(snapshot.options); setDocumentCart(lines); setDocumentResult(null); setDocumentError(""); setDocumentPendingCreation(false);
      setDocumentType(supportedTypes[0]); setDocumentEmail(customer.email || "");
      setDocumentDelivery(options.mode === "local-demo" || !options.email?.configured || !customer.email ? "download" : "email");
      setDocumentSubject("Your document from GuruConnect");
      setDocumentMessage(`Hi ${customer.name || "there"},\n\nPlease find your document for the selected items attached. Let us know if you have any questions.`);
      setDocumentModalOpen(true);
    } catch (error) { if (alive.current && currentDocumentScope.current === scope && ticket === selection.current && error.name !== "AbortError") setError(error.message); }
    finally { documentInFlight.current = false; if (alive.current && currentDocumentScope.current === scope && ticket === selection.current) setDocumentBusy(""); }
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
    const ticket = selection.current;
    setNoteBusy(true);
    setNoteError("");
    try {
      const data = await sessionCall(`/voice-sessions/${session}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: noteText.trim() }),
      });
      if (!data?.note) throw new Error("The note could not be saved.");
      if (!alive.current || sessionRef.current !== session || ticket !== selection.current) return;
      setNotes((current) => [...current, data.note]);
      setNoteText("");
    } catch (e) {
      if (alive.current && sessionRef.current === session && ticket === selection.current) setNoteError(e?.message || "The note could not be saved. Please try again.");
    } finally {
      if (alive.current && sessionRef.current === session && ticket === selection.current) setNoteBusy(false);
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
  const downloadDocumentPdf = (result) => {
    const pdf = result?.pdf;
    if (pdf?.mime_type !== "application/pdf" || typeof pdf.base64 !== "string") throw new Error("The document PDF is unavailable. Keep this session open and retry.");
    const binary = atob(pdf.base64);
    if (!binary.startsWith("%PDF-")) throw new Error("The server did not return a valid PDF document.");
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const anchor = document.createElement("a"); anchor.href = url;
    anchor.download = String(pdf.filename || "GuruConnect_document.pdf").replace(/[\\/]/g, "_");
    anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const closeDocumentModal = () => { if (!documentInFlight.current) setDocumentModalOpen(false); };
  const documentScopeCurrent = (snapshot) => alive.current && documentSnapshot.current === snapshot && currentDocumentScope.current === snapshot.scope && sessionRef.current === snapshot.session;
  const createDocument = async (type) => {
    const snapshot = documentSnapshot.current;
    if (!snapshot || documentInFlight.current || !documentScopeCurrent(snapshot)) return;
    const recipient = documentEmail.trim();
    const downloadOnly = snapshot.options.mode === "local-demo" || !snapshot.options.email?.configured || !recipient || documentDelivery === "download";
    if (!downloadOnly && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient)) { setDocumentError("Enter a valid recipient email, or choose Download PDF instead of emailing."); return; }
    const existing = snapshot.result;
    if (existing && downloadOnly) {
      try { downloadDocumentPdf(existing); setDocumentFeedback("Document PDF downloaded."); setDocumentModalOpen(false); } catch (error) { setDocumentError(error.message); }
      return;
    }
    if (existing?.delivery?.status === "uncertain") { setDocumentError("Email submission is uncertain. Check its status before retrying; your document PDF is available to download."); return; }
    const lines = snapshot.cart.map((line) => ({ item_id: line.item_id || line.id, quantity: Number(line.quantity), rate: Number(line.rate || 0), discount: Number(line.discount || 0), discount_type: line.discount_type || "percentage", description: line.notes || line.product || line.name || "" }));
    if (!lines.length || lines.some((line) => !line.item_id || !Number.isFinite(line.quantity) || line.quantity <= 0)) { setDocumentError("Select at least one catalogue product with a valid quantity."); return; }
    const body = snapshot.pendingCreation ? snapshot.createAction.body : existing ? { recipient, subject: documentSubject, message: documentMessage } : { type, delivery: downloadOnly ? "download" : "email", recipient: downloadOnly ? "" : recipient, subject: documentSubject, message: documentMessage, line_items: lines };
    const signature = JSON.stringify(body);
    const actionName = existing ? "emailAction" : "createAction";
    if (snapshot[actionName]?.signature !== signature) snapshot[actionName] = { signature, requestId: crypto.randomUUID(), body };
    const requestId = snapshot[actionName].requestId;
    documentInFlight.current = true; setDocumentBusy(type); setDocumentError(""); setDocumentFeedback("");
    try {
      const path = `/voice-sessions/${snapshot.session}/sales-documents${existing ? `/${encodeURIComponent(existing.document.id)}/email` : ""}`;
      const response = await sessionCall(path, { method: "POST", timeoutMs: 90000, headers: { "Idempotency-Key": requestId }, body: JSON.stringify({ ...body, request_id: requestId }) });
      if (!documentScopeCurrent(snapshot)) return;
      const result = existing ? { ...existing, ...response, document: response.document || existing.document, pdf: response.pdf || existing.pdf } : response;
      if (!result?.document?.id || !result.pdf) throw new Error("The document response is incomplete. Retry this action using the same session.");
      snapshot.result = result; snapshot.pendingCreation = false; documentDrafts.current.delete(snapshot.scope); setDocumentPendingCreation(false); setDocumentResult(result);
      const deliveryStatus = result.delivery?.status;
      // A confirmed rejection can be retried as a new delivery. A lost response
      // keeps its action key so checking it cannot submit the email twice.
      if (existing && deliveryStatus === "failed") snapshot.emailAction = null;
      if (downloadOnly) {
        downloadDocumentPdf(result); setDocumentModalOpen(false);
        setDocumentFeedback(`${result.document.demo ? "Sample document" : "Document"} created and downloaded as PDF.`);
      } else if (deliveryStatus === "accepted") {
        setDocumentModalOpen(false); setDocumentFeedback("Document created. Submitted for delivery via SMTP2GO.");
      } else {
        snapshot.error = result.delivery?.message || (deliveryStatus === "uncertain" ? "Email submission is uncertain. Check its status before retrying. You can download the created PDF." : "The document was created, but email submission failed. Retry email or download the PDF.");
        snapshot.emailFields = { recipient, subject: documentSubject, message: documentMessage };
        documentDrafts.current.set(snapshot.scope, snapshot); setDocumentError(snapshot.error);
      }
    } catch (error) {
      if (documentScopeCurrent(snapshot)) {
        const message = error.message || "The document could not be created. Retry this action.";
        if (!existing && !snapshot.result && (error.details?.document_started === true || (error.details?.document_started !== false && (!error.status || error.status >= 500)))) {
          snapshot.pendingCreation = true; snapshot.error = message; documentDrafts.current.set(snapshot.scope, snapshot); setDocumentPendingCreation(true);
        } else if (!existing && !snapshot.result) {
          snapshot.pendingCreation = false; documentDrafts.current.delete(snapshot.scope); setDocumentPendingCreation(false);
        }
        setDocumentError(message);
      }
    }
    finally { documentInFlight.current = false; if (documentScopeCurrent(snapshot)) setDocumentBusy(""); }
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
  const transcriptionUiStatus = liveProcessingStatus({ snapshot: currentRecordingSnapshot, captureState, turns: transcriptPreview, ...liveProcessing });
  const demoCatalogue = localMode && (catalogueNotices.some((notice) => notice.code === "local_demo_catalogue") || recommendations.some((item) => item.catalogue_source === "local-demo") || cart.some((item) => item.catalogue_source === "local-demo"));
  const demoCatalogueNotice = demoCatalogue ? "Demo catalogue: products, prices and stock are sample data. Zoho Books is not connected." : "";
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
  const uniqueChats = chats.filter((chat) => !contacts.some((contact) => contact.session_id === chat.id));
  const visibleChats = uniqueChats.filter(matchesContactFilter);
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
      {documentFeedback && <p className="session-document-feedback" role="status">{documentFeedback}</p>}
      <div className={`chat-layout voice-sessions-layout${cart.length && cartOpen ? " is-cart-focused" : ""}`}>
        <section className="chat-list" inert={cart.length && cartOpen ? true : undefined} aria-hidden={cart.length && cartOpen ? true : undefined}>
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
            {[['all', `All (${contacts.length + uniqueChats.length})`], ['active', 'Active'], ['closed', 'Closed']].map(([value, label]) => (
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
              className={`contact-row ${selectedContact?.id === contact.id || session === contact.session_id ? "selected" : ""}`}
              onClick={() => chooseContact(contact)}
              disabled={busy}
              key={contact.id}
            >
              <span className="session-contact-avatar" aria-hidden="true">{String(contact.name || "Customer").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()}</span>
              <b>{contact.name}</b>
              <small>
                {contact.phone || contact.email || "No phone or email"}
              </small>
              <em>{contact.sources.join(" · ")}</em>
              {contact.recording_status && <span className="contact-status-pill">{contact.recording_status}</span>}
            </button>
          ))}
          {visibleChats.length > 0 && <div className="contact-heading session-heading">
            <Headphones size={15} /> Recent sessions
          </div>}
          {visibleChats.map((chat) => (
            <button
              type="button"
              className={`chat-row ${session === chat.id ? "selected" : ""}`}
              onClick={() => void open(chat.id)}
              disabled={busy}
              key={chat.id}
            >
              <span className="session-contact-avatar" aria-hidden="true">{String(chat.name || "Customer").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()}</span>
              <b>{chat.name}</b>
              <small>{chat.phone || chat.email || "Voice session"}</small>
              <em>{new Date(chat.updated_at).toLocaleString()}</em>
            </button>
          ))}
          {!contactBusy && !visibleContacts.length && !visibleChats.length && (
            <p className="session-list-empty">No {contactFilter === "all" ? "contacts or sessions" : `${contactFilter} contacts`} found.</p>
          )}
        </section>
        <section className={`conversation${session ? " live-session-open" : ""}`}>
          {session && selectedContact && (
            <>
              <div className="contact-record live-session-header">
                <div className="session-customer-heading">
                  <strong>{identity.name || selectedContact.name}</strong>
                  <div className="session-customer-contact">
                    <button type="button" className="contact-phone-link" onClick={openCustomer}><UserRound size={14} />{identity.phone || "Customer details"}</button>
                    {identity.phone && <button type="button" className="copy-phone-button" onClick={() => void copyPhone()} aria-label="Copy phone number"><Copy size={13} /></button>}
                  </div>
                </div>
                <div className="live-header-status" aria-label="Conversation processing status">
                  <span className={`live-header-pill status-${transcriptionUiStatus.key}`} title={transcriptionUiStatus.detail}>
                    <i />{transcriptionUiStatus.label}
                  </span>
                  {demoCatalogue && <span className="live-header-pill status-ready" title={demoCatalogueNotice}>Demo catalogue</span>}
                  {transcriptionUiStatus.key === "failed" && <button type="button" className="link-button" onClick={() => void retryRecordingStage()}>Retry processing</button>}
                </div>
                {selectedContact.enquiry_id && <label className="session-stage-control"><span>Enquiry stage</span><select value={selectedContact.enquiry_stage || "new"} onChange={(event) => void changeEnquiryStage(event.target.value)} disabled={stageBusy}><option value="new">New enquiry</option><option value="in_process">In process</option><option value="closed">Closed</option><option value="customer_did_not_show">Customer didn’t show up</option></select></label>}
                {cart.length > 0 && <button type="button" className="session-contacts-toggle" onClick={() => setCartOpen(!cartOpen)} aria-pressed={!cartOpen}><BookUser size={16} />{cartOpen ? "Show contacts" : "Focus cart"}</button>}
              </div>
              <nav className="live-session-tabs" aria-label="Customer session views">
                <button type="button" className={activeTab === "conversation" ? "selected" : ""} onClick={() => setActiveTab("conversation")}><MessageSquare size={15} />Conversation</button>
                <button type="button" onClick={() => setCustomerOpen(true)}><UserRound size={15} />Customer details</button>
                <button type="button" className={activeTab === "context" ? "selected" : ""} onClick={() => setActiveTab(activeTab === "context" ? "conversation" : "context")}><BookUser size={15} />Enquiry context</button>
                <button type="button" className={activeTab === "history" ? "selected" : ""} onClick={() => setActiveTab(activeTab === "history" ? "conversation" : "history")}><Headphones size={15} />Recordings ({recorderSessions.length})</button>
              </nav>
            </>
          )}
          {!session && <div className="messages session-welcome"><div className="welcome">
            <span className="session-welcome-icon"><Headphones size={32} /></span>
            <small>YOUR LIVE SALES WORKSPACE</small><h2>Start a customer conversation</h2>
            <p>Select a recent contact or enter customer details. Capture the conversation, see its English translation, and find matching inventory as you speak.</p>
            <div className="identity">
              <label>Customer name<input placeholder="Customer name" value={identity.name} onChange={(event) => setIdentity({ ...identity, name: event.target.value })} /></label>
              <label>Phone number<input inputMode="tel" maxLength="15" placeholder="Phone number" value={identity.phone} onChange={(event) => setIdentity({ ...identity, phone: event.target.value.replace(/\D/g, "").slice(0, 15) })} /></label>
              <label>Email address (optional)<input aria-label="Email address" type="email" placeholder="Email address" value={identity.email} onChange={(event) => setIdentity({ ...identity, email: event.target.value })} /></label>
              <button type="button" className="primary voice-open-button" onClick={() => void prepareSession()} disabled={busy || !identity.name.trim() || !identity.phone.trim()}>{busy ? "Opening…" : "Open customer session"}<ChevronRight size={16} /></button>
            </div>
            <small className="session-welcome-footnote">You’ll start the microphone in the next step.</small>
          </div></div>}
          {session && <>
            {activeTab !== "conversation" && <section className="session-view-drawer" aria-label={activeTab === "history" ? "Past recordings for this customer" : "Enquiry context"}>
              <div className="session-drawer-heading"><strong>{activeTab === "history" ? "Past recordings" : "Customer requirements"}</strong><button type="button" onClick={() => setActiveTab("conversation")} aria-label="Close session panel"><X size={16} /></button></div>
              {activeTab === "context" ? <div className="session-context-summary"><p>{selectedContact?.enquiry_id ? `Linked enquiry · ${String(selectedContact.enquiry_stage || "new").replaceAll("_", " ")}` : "This conversation is linked to the customer’s session."}</p><div>{liveKeywords.length ? liveKeywords.map((keyword) => <span key={keyword}>{keyword}</span>) : <p>Product requirements will appear as the conversation is translated.</p>}</div></div> : recorderSessions.length ? recorderSessions.map((recording) => <article className="session-recording-history-row" key={recording.id}><div><strong>{new Date(recording.created_at).toLocaleString()}</strong><small>{Math.round(recording.duration_seconds || 0)} sec · {String(recording.final_state || "pending").replaceAll("_", " ")}</small></div><div className="session-recording-history-actions">{recording.upload_state === "saved" && <ProgressivePlayback conversationId={recording.id} workspaceId={workspace.id} memberId={workspace.member_id} compact />}<button type="button" className="button button-quiet" onClick={() => setArchivedConversationId(recording.id)}>Open transcript</button><button type="button" className="button button-danger-quiet" onClick={() => void deleteRecorderSession(recording.id)} disabled={busy} aria-label="Delete recording"><Trash2 size={14} /></button></div></article>) : <p>No saved recordings for this customer yet.</p>}
            </section>}
            <LiveSessionWorkspace
              identity={identity}
              messages={messages}
              transcript={transcriptPreview}
              recommendations={[...new Map([...products, ...recommendations].map((item) => [item.item_id || item.id || item.sku, item])).values()]}
              intents={[...new Set([...liveKeywords, ...customIntents])].filter((keyword) => !hiddenIntents.includes(keyword))}
              {...liveGuidance}
              cart={cart}
              cartOpen={cartOpen}
              onCartOpenChange={setCartOpen}
              onAddToCart={addToCart}
              onUpdateCart={updateCart}
              onRemoveCart={removeCartItem}
              onSearchProducts={() => { setIntentSearch(false); setCartProductSearchOpen(true); setCartSearch(""); setCartSearchResults([]); setCartSearchError(""); }}
              onCompleteCart={() => void openDocumentModal()}
              onViewProduct={setViewProduct}
              onAddIntent={() => { setIntentSearch(true); setCartProductSearchOpen(true); setCartSearch(""); setCartSearchResults([]); setCartSearchError(""); }}
              onRemoveIntent={(keyword) => setHiddenIntents((current) => [...current, keyword])}
              notes={notes}
              noteText={noteText}
              onNoteTextChange={setNoteText}
              onAddNote={() => void addNote()}
              noteBusy={noteBusy}
              noteError={noteError}
              recordingState={captureState}
              recordingStatus={transcriptionUiStatus}
              insightsStatus={{ key: liveAnalysis, label: liveAnalysis === "running" || liveAnalysis === "pending" ? "Finding product matches…" : liveAnalysis === "unavailable" ? "Product analysis temporarily unavailable" : liveAnalysis === "failed" ? "Product analysis needs attention" : liveAnalysis === "complete" ? "Requirements updated" : "Listening for product requirements" }}
              catalogueNotice={[liveNotice, cartSaveError, demoCatalogueNotice, ...catalogueNotices.filter((notice) => !demoCatalogue || notice.code !== "local_demo_catalogue").map((notice) => notice.message)].filter(Boolean).join(" ")}
              onDownloadTranscript={downloadTranscript}
              sessionStartedAt={recordingSnapshot?.created_at}
            >
              <ConversationRecorder key={`${user?.id}:${workspace?.id}:${session}:${recorderGeneration}`} customerName={identity.name} customerPhone={identity.phone} customerEmail={identity.email} title="Voice session" initialConversationId={conversationId} processingStatus={transcriptionUiStatus} providerReadiness={liveProcessing?.providerReadiness} onConversationChange={linkConversation} onBusyChange={setRecorderBusy} onCaptureChange={setCaptureState} onSaved={() => void recordingSaved()} chunkSeconds={5} grouped sessionOnly />
            </LiveSessionWorkspace>
          </>}
        </section>
      </div>
      {cartProductSearchOpen && <div className="session-modal-backdrop cart-search-backdrop" onClick={() => setCartProductSearchOpen(false)}><section className="product-search-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-search-title" onClick={(event) => event.stopPropagation()}><div className="customer-modal-heading"><div><small>ZOHO INVENTORY</small><h2 id="inventory-search-title">{intentSearch ? "Add a product requirement" : "Add another product"}</h2></div><button type="button" onClick={() => setCartProductSearchOpen(false)} aria-label="Close inventory search"><X size={20} /></button></div><form className="cart-product-search-form" onSubmit={(event) => { event.preventDefault(); void searchCartProducts(); }}><input autoFocus aria-label="Search inventory" placeholder="Search by product, description, or SKU" value={cartSearch} onChange={(event) => { ++catalogueSearch.current; setCartSearch(event.target.value); setCartSearchBusy(false); setCartSearchError(""); }} /><button type="submit" className="primary" disabled={cartSearchBusy || !cartSearch.trim()}>{cartSearchBusy ? "Searching…" : "Search"}</button></form><div className="cart-product-results" aria-busy={cartSearchBusy}>{cartSearchError && <p role="alert">{cartSearchError}</p>}{cartSearchResults.map((result) => {
          const match = recommendations.find((item) => (item.item_id || item.id || item.sku) === (result.item_id || result.id || result.sku));
          const item = match ? { ...result, ...match } : result;
          return <article className="cart-product-result" key={item.item_id || item.id || item.sku}>
            <span className="cart-product-result-image"><ShoppingCart size={20} /></span>
            <div className="lws-search-result-copy"><strong>{item.name || item.item_name}</strong><small>{item.sku || "No SKU"} · {productPriceAvailable(item) ? `₹${Number(item.rate ?? item.price).toLocaleString("en-IN")}` : "Confirm price"}</small><ProductMatchDetails item={item} /><Stock item={item} /></div>
            <ProductCartAction item={item} onAddToCart={addToCart} />
          </article>;
        })}{!cartSearchBusy && cartSearch && !cartSearchResults.length && !cartSearchError && <p className="notes-empty">Search your connected catalogue to see matching inventory.</p>}</div></section></div>}
      {viewProduct && <div className="session-modal-backdrop" onClick={() => setViewProduct(null)}><section className="customer-modal session-product-details" role="dialog" aria-modal="true" aria-labelledby="session-product-title" onClick={(event) => event.stopPropagation()}><div className="customer-modal-heading"><h2 id="session-product-title">{viewProduct.name || viewProduct.item_name}</h2><button type="button" onClick={() => setViewProduct(null)} aria-label="Close product details"><X size={20} /></button></div><div className="session-product-details-body"><p>{viewProduct.description || "No additional description in the connected catalogue."}</p><p>SKU: {viewProduct.sku || "Not provided"}</p><ProductMatchDetails item={viewProduct} showEvidence /><Stock item={viewProduct} /><strong>{productPriceAvailable(viewProduct) ? `₹${Number(viewProduct.rate ?? viewProduct.price).toLocaleString("en-IN")}` : "Confirm price"}</strong><ProductCartAction item={viewProduct} className="primary" onAddToCart={(item) => { addToCart(item); setViewProduct(null); }} /></div></section></div>}
      {archivedConversationId && <div className="session-modal-backdrop" onClick={() => setArchivedConversationId(null)}><section className="session-archive-modal" role="dialog" aria-modal="true" aria-label="Saved conversation transcript" onClick={(event) => event.stopPropagation()}><div className="customer-modal-heading"><h2>Saved conversation</h2><button type="button" onClick={() => setArchivedConversationId(null)} aria-label="Close saved conversation"><X size={20} /></button></div><ConversationTranscript conversationId={archivedConversationId} /></section></div>}
      <CustomerModal
        contact={customerOpen ? selectedContact : null}
        onClose={() => setCustomerOpen(false)}
      />
      <SalesDocumentModal
        open={documentModalOpen}
        contact={documentOptions?.customer || null}
        customerType="customer"
        cart={documentCart}
        subtotal={documentCart.reduce((sum, line) => sum + Number(line.rate || 0) * Number(line.quantity || 0), 0)}
        discount={documentCart.reduce((sum, line) => sum + (line.discount_type === "fixed" ? Number(line.discount || 0) : Number(line.rate || 0) * Number(line.quantity || 0) * Number(line.discount || 0) / 100), 0)}
        documentTypes={documentOptions?.document_types}
        emailConfigured={documentOptions?.email?.configured === true}
        delivery={documentDelivery}
        setDelivery={setDocumentDelivery}
        demo={documentOptions?.mode === "local-demo"}
        error={documentError}
        createdDocument={documentResult ? { number: documentResult.document?.number, deliveryStatus: documentResult.delivery?.status } : null}
        pendingCreation={documentPendingCreation}
        onDownload={() => { try { downloadDocumentPdf(documentResult); setDocumentFeedback("Document PDF downloaded."); } catch (error) { setDocumentError(error.message); } }}
        documentType={documentType}
        setDocumentType={setDocumentType}
        email={documentEmail}
        setEmail={setDocumentEmail}
        subject={documentSubject}
        setSubject={setDocumentSubject}
        message={documentMessage}
        setMessage={setDocumentMessage}
        busy={!!documentBusy}
        onClose={closeDocumentModal}
        onBack={closeDocumentModal}
        onSubmit={(type) => void createDocument(type)}
      />
    </section>
  );
}
