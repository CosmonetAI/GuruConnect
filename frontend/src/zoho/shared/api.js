import seedData from '../../services/mock-data.json';
const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
export const API = `${API_BASE}/api`;

const mockKey = 'guruconnect-frontend-zoho-mock-data';
function readMock() {
  try { const saved = sessionStorage.getItem(mockKey); return expandMockData(saved ? JSON.parse(saved) : structuredClone(seedData)); } catch { return expandMockData(structuredClone(seedData)); }
}
function expandMockData(data) {
  const branches = ['Bengaluru Central', 'Mysuru', 'Hyderabad'];
  const executives = ['Ravi Kumar', 'Sumanth Reddy', 'Priya Gupta', 'Sneha Kapoor'];
  data.contacts = data.contacts.map((item, index) => ({
    ...item,
    branch: item.branch || branches[index % branches.length],
    assigned: item.assigned || executives[index % executives.length],
    customerType: item.customerType || 'Retail',
    company: item.company || item.company_name || 'Individual customer',
    project: item.project || `${branches[index % branches.length]} home project`,
    preferredContact: item.preferredContact || (index % 2 ? 'Email' : 'Phone'),
    sources: item.sources || [index % 3 === 1 ? 'Incoming Call' : 'Retail walk-in'],
  }));
  const productImages = ['/product-aero-bldc.jpg', '/product-luxe-led.jpg', '/product-led-bulb.jpg', '/product-pedestal-fan.jpg'];
  data.products = data.products.map((item, index) => ({ ...item, image_url: item.image_url || productImages[index % productImages.length] }));
  const fanNames = ['Aero', 'Luxe', 'Havells', 'Atomberg', 'Crompton', 'Orient', 'Usha', 'Bajaj', 'Superfan', 'V-Guard'];
  while (data.products.length < 100) { const i = data.products.length + 1; data.products.push({ item_id: `item-mock-${i}`, name: `${fanNames[i % fanNames.length]} Ceiling Fan ${1000 + (i % 4) * 100}mm`, sku: `FAN-MOCK-${String(i).padStart(3, '0')}`, rate: 1999 + (i % 12) * 500, status: 'active', stock_on_hand: 5 + (i % 40), description: 'Approved catalogue product for the frontend demo.', unit: 'pcs', image_url: productImages[i % productImages.length] }); }
  while (data.contacts.length < 25) { const i = data.contacts.length + 1; const branch = branches[i % branches.length]; data.contacts.push({ contact_id: `contact-mock-${i}`, contact_name: `Demo Customer ${String(i).padStart(2, '0')}`, company_name: i % 2 ? 'Retail customer' : `Demo Business ${i}`, company: i % 2 ? 'Retail customer' : `Demo Business ${i}`, contact_type: 'customer', mobile: `98${String(70000000 + i * 1371).slice(0, 8)}`, email: `customer${i}@demo.in`, status: 'active', branch, assigned: executives[i % executives.length], customerType: i % 4 === 0 ? 'Dealer' : 'Retail', project: `${branch} customer project`, preferredContact: i % 2 ? 'Email' : 'Phone', sources: [i % 3 === 1 ? 'Incoming Call' : 'Retail walk-in'] }); }
  const documentSets = [['quotes', 'estimate_id', 'estimate_number', 'QT-2026-', 'sent'], ['orders', 'salesorder_id', 'salesorder_number', 'SO-2026-', 'confirmed'], ['purchase_orders', 'purchaseorder_id', 'purchaseorder_number', 'PO-2026-', 'issued'], ['invoices', 'invoice_id', 'invoice_number', 'INV-2026-', 'sent']];
  for (const [key, idKey, numberKey, prefix, status] of documentSets) while (data[key].length < 20) { const i = data[key].length + 1; const customer = data.contacts[i % data.contacts.length]; data[key].push({ [idKey]: `${key}-mock-${i}`, [numberKey]: `${prefix}${String(1000 + i).padStart(4, '0')}`, customer_name: customer.contact_name, vendor_name: key === 'purchase_orders' ? 'Guru Electricals Supply' : undefined, date: `2026-10-${String(1 + (i % 28)).padStart(2, '0')}`, status, total: 4999 + (i % 14) * 3250, currency_symbol: '₹', line_items: [] }); }
  const existingLeadContacts = new Set((data.pipelines || []).map((lead) => lead.Contact_Name?.id).filter(Boolean));
  const crmStages = ['Qualification', 'Needs Analysis', 'Proposal/Price Quote', 'Negotiation/Review'];
  data.pipelines = data.pipelines || [];
  data.contacts.forEach((contact, index) => {
    if (existingLeadContacts.has(contact.contact_id)) return;
    data.pipelines.push({ id: `lead-${contact.contact_id}`, Deal_Name: `${contact.contact_name} · ${contact.project || 'Customer opportunity'}`, Stage: crmStages[index % crmStages.length], Lead_Status: 'Registered', Amount: 4999 + (index % 12) * 750, Expected_Revenue: 3999 + (index % 10) * 600, Probability: 25 + (index % 6) * 10, Closing_Date: `2026-11-${String(1 + (index % 25)).padStart(2, '0')}`, Next_Step: index % 2 ? 'Follow up with product comparison' : 'Confirm requirements and prepare quote', Contact_Name: { id: contact.contact_id, name: contact.contact_name, mobile: contact.mobile }, Phone: contact.mobile, Owner: { name: contact.assigned || executives[index % executives.length] }, Pipeline: { id: 'sales-pipeline', name: 'Sales pipeline' }, Description: `Registered demo lead for ${contact.contact_name}. Product interests and conversation history are available in the customer session.` });
  });
  return data;
}
function writeMock(data) { sessionStorage.setItem(mockKey, JSON.stringify(data)); }
function mockList(items, key) { return { [key]: items, has_more: false }; }
function mockZoho(path, opts = {}) {
  const data = readMock(); const clean = path.split('?')[0]; const query = new URLSearchParams(path.split('?')[1] || ''); const method = String(opts.method || 'GET').toUpperCase();
  const search = (query.get('search') || '').toLowerCase();
  const filter = items => items.filter(item => !search || JSON.stringify(item).toLowerCase().includes(search));
  const customerHistory = (contact) => {
    const quotes = data.quotes.filter((quote) => quote.customer_name === contact.contact_name).slice(0, 3);
    const lead = data.pipelines.find((item) => item.Contact_Name?.id === contact.contact_id) || data.pipelines.find((item) => item.Contact_Name?.name === contact.contact_name);
    return { transcript_summary: `Customer is comparing quiet, energy-efficient ceiling fans for a ${contact.project || 'home project'} and asked for a 1200mm model with remote control.`, keywords: ['Ceiling fan', '1200mm', 'BLDC', 'Remote control', 'Energy efficient', contact.branch].filter(Boolean), recordings: [{ id: `history-${contact.contact_id}-1`, created_at: '2026-10-29T10:24:00Z', duration_seconds: 192, summary: 'Customer compared regular and BLDC fan options and requested a premium model.', keywords: ['BLDC', 'Remote control', 'Premium model'], status: 'Processed' }, { id: `history-${contact.contact_id}-2`, created_at: '2026-10-27T15:12:00Z', duration_seconds: 137, summary: 'Customer shared budget, installation preference, and a follow-up requirement.', keywords: ['Budget', 'Installation', 'Follow-up'], status: 'Processed' }], quotes: quotes.length ? quotes.map((quote) => ({ id: quote.estimate_id, number: quote.estimate_number, status: quote.status, total: quote.total, date: quote.date })) : [{ id: `quote-${contact.contact_id}`, number: 'QT-2026-1027', status: 'sent', total: lead?.Amount || 6999, date: '2026-10-28' }], lead };
  };
  const recommendedProducts = data.products.slice(0, 3).map((item, index) => ({ ...item, match_status: 'verified', matched_specs: index === 0 ? ['quiet operation', 'energy efficient'] : ['ceiling fan', 'modern design'], features: index === 0 ? ['Energy efficient BLDC', 'Low noise operation', 'Remote control'] : index === 1 ? ['BLDC motor', 'LED light with dimmer', 'Remote control'] : ['High air delivery', 'Copper motor', 'Easy installation'] }));
  const sessionPayload = (contact, agent = 'Ravi Kumar') => {
    const now = new Date().toISOString();
    const transcript = [
      { start_seconds: 0, recorded_at: now, speaker_role: 'customer', speaker_name: contact.contact_name, english_text: 'I am looking for a quiet ceiling fan for my living room. Do you have something energy efficient?', source_text: 'I am looking for a quiet ceiling fan for my living room. Do you have something energy efficient?' },
      { start_seconds: 12, recorded_at: now, speaker_role: 'agent', speaker_name: agent, english_text: 'Yes, I can suggest a few BLDC options with remote control and good air delivery.', source_text: 'Yes, I can suggest a few BLDC options with remote control and good air delivery.' },
      { start_seconds: 25, recorded_at: now, speaker_role: 'customer', speaker_name: contact.contact_name, english_text: 'Please show me the 1200mm model and a premium option with a light. My budget is around twelve thousand.', source_text: 'Please show me the 1200mm model and a premium option with a light. My budget is around twelve thousand.' },
      { start_seconds: 39, recorded_at: now, speaker_role: 'agent', speaker_name: agent, english_text: 'I will compare the matching products and prepare the quotation with the available discount.', source_text: 'I will compare the matching products and prepare the quotation with the available discount.' },
    ];
    return {
      messages: transcript.map(turn => ({ role: turn.speaker_role === 'customer' ? 'user' : 'assistant', content: turn.english_text })),
      transcript_preview: transcript,
      keywords: ['Ceiling fan', 'Regular fan', 'Good air delivery', 'Modern design'],
      requirements: [{ label: 'Product', value: '1200mm ceiling fan' }, { label: 'Specification', value: 'Quiet BLDC with remote' }, { label: 'Budget', value: '₹12,000' }],
      current_intent: 'Compare ceiling fan options',
      agent_guidance: ['Lead with BLDC energy savings', 'Mention remote control and installation', 'Prepare a quote after product selection'],
      clarification_suggestions: ['Confirm blade colour', 'Confirm delivery timeline'],
      catalogue_status: 'up_to_date',
      analysis_status: 'complete',
      processing_status: { stage: 'complete', message: 'Demo transcript analysed locally.' },
      notices: [{ code: 'local_demo_catalogue', message: 'Demo catalogue: products, prices and stock are sample data.' }],
    };
  };
  if (clean === '/products') return mockList(filter(data.products), 'items');
  if (clean === '/contacts') return mockList(filter(data.contacts), 'contacts');
  if (clean === '/quotes') return mockList(filter(data.quotes), 'quotes');
  if (clean === '/orders') return mockList(filter(data.orders), 'orders');
  if (clean === '/purchase-orders') return mockList(filter(data.purchase_orders), 'purchase_orders');
  if (clean === '/invoices') return mockList(filter(data.invoices), 'invoices');
  if (clean === '/documents/options') return { reporting_tags: [{ tag_id: 'intent', tag_name: 'Customer intent' }] };
  if (clean.startsWith('/purchase-orders/options/tag-options')) return { options: [{ option_id: 'new', option_name: 'New enquiry' }, { option_id: 'hot', option_name: 'High intent' }] };
  if (clean === '/pipelines/layouts') return { layouts: [{ id: 'sales-pipeline', name: 'Sales pipeline', stages: ['Qualification', 'Proposal/Price Quote', 'Negotiation/Review', 'Closed Won', 'Closed Lost'] }] };
  if (clean === '/pipelines' || clean === '/pipelines/search') return { records: filter(data.pipelines), last_sync: new Date().toISOString(), has_more: false };
  if (clean.startsWith('/pipelines/record/')) { const id = clean.split('/').pop(); const record = data.pipelines.find(item => item.id === id) || data.pipelines[0]; if (method === 'PUT' && record) { let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {} Object.assign(record, body); writeMock(data); } if (method === 'DELETE') { data.pipelines = data.pipelines.filter(item => item.id !== id); writeMock(data); return { ok: true }; } return { record }; }
  if (clean.startsWith('/enquiries/') && method === 'PATCH') { let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {} const enquiryId = clean.split('/')[2]; const enquiry = (data.enquiries || []).find(item => item.id === enquiryId || item.enquiry_id === enquiryId); const contact = data.contacts.find(item => item.contact_id === enquiry?.contact_id || item.mobile === enquiry?.phone || item.contact_name === enquiry?.customer_name) || data.contacts.find(item => enquiryId.includes(String(item.contact_id).replace('contact-', '')) || enquiryId.includes(item.mobile)); if (enquiry) enquiry.stage = body.stage || enquiry.stage; const lead = contact && data.pipelines.find(item => item.Contact_Name?.id === contact.contact_id); if (lead) { const stage = String(body.stage || '').toLowerCase(); lead.Stage = stage.includes('closed') ? 'Closed Won' : stage.includes('process') ? 'Needs Analysis' : 'Qualification'; lead.Lead_Status = stage.includes('closed') ? 'Converted' : 'Registered'; writeMock(data); } return { ok: true, enquiry, crm_lead: lead }; }
  if (clean === '/pipelines/sync') return { last_sync: new Date().toISOString(), limited: false };
  if (clean === '/bigin/deleted/Pipelines' || clean === '/bigin/deleted/Tasks') return { items: [] };
  if (clean === '/chats') return [{ id: 'chat-akshat', name: 'Akshat Saxena', title: 'Ceiling fan enquiry', updated_at: '2026-10-30T09:20:00Z', phone: '9311854768', sources: ['Retail walk-in'] }, { id: 'chat-rahul', name: 'Rahul Sharma', title: 'Bulk fan quote', updated_at: '2026-10-30T08:10:00Z', phone: '9311854700', sources: ['Incoming call'] }];
  if (clean === '/session-contacts') return { contacts: data.contacts.map(item => ({ session_id: `chat-${item.contact_id}`, name: item.contact_name, phone: item.mobile, email: item.email, sources: item.sources, branch: item.branch, assigned: item.assigned, customerType: item.customerType, company: item.company || item.company_name, project: item.project, preferredContact: item.preferredContact })) };
  if (clean === '/voice-sessions' && method === 'POST') {
    let input = {}; try { input = JSON.parse(opts.body || '{}'); } catch {}
    const contact = data.contacts.find(item => item.mobile === input.phone || item.contact_name === input.name) || data.contacts[0];
    const payload = sessionPayload(contact);
    payload.customer_history = customerHistory(contact); payload.crm_lead = payload.customer_history.lead;
    return { session: { id: `chat-${contact.contact_id}`, name: contact.contact_name, phone: contact.mobile, email: contact.email, enquiry_id: `enquiry-${contact.contact_id.replace('contact-', '')}`, enquiry_stage: 'In process' }, ...payload, recorder_sessions: [], insights: [{ label: 'Customer intent', value: payload.current_intent }, { label: 'Budget', value: '₹12,000' }], recommendations: recommendedProducts };
  }
  if (clean.startsWith('/voice-sessions/') && clean.endsWith('/history')) {
    const sessionId = clean.split('/')[2]; const contactId = sessionId.replace(/^chat-/, ''); const contact = data.contacts.find(item => item.contact_id === contactId) || data.contacts[0];
    const payload = sessionPayload(contact);
    payload.customer_history = customerHistory(contact); payload.crm_lead = payload.customer_history.lead;
    return { session: { name: contact.contact_name, phone: contact.mobile, email: contact.email, enquiry_id: `enquiry-${contact.contact_id.replace('contact-', '')}`, enquiry_stage: 'In process', books_contact_id: contact.contact_id }, ...payload, recorder_sessions: [], conversation_id: null, insights: [{ label: 'Customer intent', value: payload.current_intent }, { label: 'Budget', value: '₹12,000' }], recommendations: recommendedProducts };
  }
  if (clean.includes('/voice-sessions/') && clean.endsWith('/notes')) {
    if (method === 'POST') { let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {} return { note: { id: `note-${Date.now()}`, author_name: 'Ravi Kumar', content: body.content || 'Follow up with the customer.', created_at: new Date().toISOString() } }; }
    return { notes: [
      { id: 'note-1', author_name: 'Akshat Saxena', content: 'Customer prefers a quiet BLDC fan with remote control. Shared two suitable models.', created_at: '2026-10-30T09:24:00Z' },
      { id: 'note-2', author_name: 'Ravi Kumar', content: 'Customer mentioned a budget around ₹12k. Follow up with installation offer.', created_at: '2026-10-30T09:25:00Z' },
      { id: 'note-3', author_name: 'Sumanth Reddy', content: 'Please confirm colour preference and delivery timeline before preparing the quote.', created_at: '2026-10-30T09:27:00Z' },
    ] };
  }
  if (clean.includes('/voice-sessions/') && clean.endsWith('/context')) return { product_requirements: [], cart: [] };
  if (clean.startsWith('/chats/') && clean.endsWith('/messages')) return [{ role: 'user', content: 'I am looking for a ceiling fan for my living room.' }, { role: 'assistant', content: 'I can suggest quiet BLDC options with remote control.' }];
  if (clean === '/chat' && method === 'POST') return { session_id: 'chat-akshat', answer: 'I found matching products and can prepare a quotation.' };
  if (method === 'POST' && clean.startsWith('/books/')) return { estimate: { estimate_number: `QT-2026-${1030 + data.quotes.length}` }, reference_number: `QT-2026-${1030 + data.quotes.length}` };
  return {};
}

/** @param {string} path @param {Record<string, any>} opts @param {string | null} workspaceId */
export async function call(path, opts = {}, workspaceId = null) {
  if (location.hostname === 'localhost' || location.hostname === '127.0.0.1' || location.hostname === '[::1]') return mockZoho(path, opts);
  const { timeoutMs = 30000, ...requestOptions } = opts;
  const headers = new Headers({ 'Content-Type': 'application/json', ...(requestOptions.headers || {}) });
  const workspace = workspaceId
    || localStorage.getItem('guruconnect-active-organisation')
    || localStorage.getItem('guruconnect-active-workspace');
  if (workspace) headers.set('X-Workspace-ID', workspace);
  const member = localStorage.getItem('guruconnect-active-workspace');
  if (member && !headers.has('X-Workspace-Member-ID')) headers.set('X-Workspace-Member-ID', member);
  try {
    const { supabase } = await import('../../services/officeApi');
    const session = supabase ? (await supabase.auth.getSession()).data.session : null;
    if (session) {
      headers.set('Authorization', `Bearer ${session.access_token}`);
      headers.set('X-User-ID', session.user.id);
    }
  } catch {}
  const timeout = AbortSignal.timeout(Number.isFinite(timeoutMs) ? Math.max(1000, Math.min(120000, timeoutMs)) : 30000);
  const signal = requestOptions.signal ? AbortSignal.any([timeout, requestOptions.signal]) : timeout;
  const response = await fetch(API + path, { ...requestOptions, headers, signal, credentials: 'omit' });
  const data = await response.json().catch(() => ({ detail: `Request failed (${response.status})` }));
  if (!response.ok) {
    const details = typeof data.detail === 'object' && data.detail ? data.detail : data.error || {};
    const error = Object.assign(Error(typeof data.detail === 'string' ? data.detail : details.message || 'Request failed'), { status: response.status, code: details.code, details });
    throw error;
  }
  return data;
}

export function withDate(path, date) {
  const cleaned = path.replace(/([?&])search=$/, '');
  return date ? `${cleaned}${cleaned.includes('?') ? '&' : '?'}date=${encodeURIComponent(date)}` : cleaned;
}
