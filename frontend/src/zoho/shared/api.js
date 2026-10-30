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
  data.voice_contexts = data.voice_contexts || {};
  data.demo_documents = data.demo_documents || [];
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
  const variantFor = (contact) => [...String(contact.contact_id || contact.mobile || contact.contact_name)].reduce((sum, character) => sum + character.charCodeAt(0), 0) % 4;
  const recommendationSets = [
    { keywords: ['Ceiling fan', '1200mm', 'BLDC', 'Remote control', 'Energy efficient'], intent: 'Compare quiet BLDC ceiling fans', requirements: [{ label: 'Product', value: '1200mm ceiling fan' }, { label: 'Specification', value: 'Quiet BLDC with remote' }, { label: 'Budget', value: '₹12,000' }], guidance: ['Lead with BLDC energy savings', 'Mention remote control and installation', 'Prepare a quote after product selection'], features: [['Energy efficient BLDC', 'Low noise operation', 'Remote control'], ['BLDC motor', 'LED light with dimmer', 'Remote control'], ['High air delivery', 'Copper motor', 'Easy installation']] },
    { keywords: ['Pedestal fan', '400mm', 'Low noise', 'Office', 'Delivery'], intent: 'Find practical pedestal fans for an office', requirements: [{ label: 'Product', value: '400mm pedestal fan' }, { label: 'Specification', value: 'Low noise with strong airflow' }, { label: 'Budget', value: '₹18,000' }], guidance: ['Confirm the number of workstations', 'Compare sweep and speed controls', 'Offer a bulk delivery timeline'], features: [['Wide sweep', 'Low noise operation', 'Three speed control'], ['Heavy base', 'Adjustable height', 'Thermal protection'], ['Energy efficient motor', 'Compact footprint', 'Easy service access']] },
    { keywords: ['LED bulb', '9W', 'Pack of 4', 'Warm white', 'Bulk order'], intent: 'Source LED lighting for a residential project', requirements: [{ label: 'Product', value: '9W LED bulbs' }, { label: 'Specification', value: 'Pack of 4, warm white' }, { label: 'Quantity', value: '24 packs' }], guidance: ['Confirm colour temperature', 'Check the required pack quantity', 'Offer a project-rate quote'], features: [['9W LED', 'Pack of 4', 'Long life'], ['Warm white', 'B22 base', 'Energy saving'], ['Cool daylight', 'High lumen output', 'Bulk pack pricing']] },
    { keywords: ['Smart fan', 'Light', 'Remote', 'Apartment', 'Installation'], intent: 'Choose a smart fan with integrated light', requirements: [{ label: 'Product', value: 'Smart ceiling fan with light' }, { label: 'Specification', value: 'Remote and dimmable LED' }, { label: 'Project', value: 'New apartment' }], guidance: ['Demonstrate the light controls', 'Confirm ceiling height and wiring', 'Include installation in the quote'], features: [['Integrated LED light', 'Remote control', 'Modern design'], ['Dimmable light', 'BLDC motor', 'Sleep mode'], ['Timer function', 'High air delivery', 'Warranty support']] },
  ];
  const recommendedProductsFor = (contact) => {
    const variant = variantFor(contact);
    const set = recommendationSets[variant];
    const offset = [0, 3, 6, 9][variant];
    return [0, 1, 2].map((position) => {
      const item = data.products[(offset + position) % data.products.length];
      return { ...item, match_status: 'verified', matched_specs: set.features[position].slice(0, 2), features: set.features[position] };
    });
  };
  const sessionPayload = (contact, agent = 'Ravi Kumar') => {
    const now = new Date().toISOString();
    const variant = variantFor(contact);
    const set = recommendationSets[variant];
    const productsForSession = recommendedProductsFor(contact);
    const productNames = productsForSession.map((product) => product.name);
    const transcriptSets = [
      [`I am looking for a quiet ceiling fan for my living room. Do you have something energy efficient?`, `Yes, I can suggest ${productNames[0]} and ${productNames[1]} with remote control and good air delivery.`, `Please show me the 1200mm model and a premium option with a light. My budget is around twelve thousand.`, `I will compare the matching products and prepare the quotation with the available discount.`],
      [`We need fans for a small office and want strong airflow without too much noise.`, `I can compare pedestal options such as ${productNames[0]} and ${productNames[1]} for the office floor.`, `Please check delivery for twelve units and include the service details.`, `I will prepare a bulk quote after confirming the sweep and delivery timeline.`],
      [`I am sourcing LED bulbs for a residential project and need a reliable pack size.`, `The catalogue has ${productNames[0]} and ${productNames[1]} with different colour and lumen options.`, `Please include the pack pricing for the project quantity.`, `I will verify the project rate and prepare the quotation.`],
      [`I want a smart ceiling fan with an integrated light for a new apartment.`, `I can show ${productNames[0]} and ${productNames[1]} with remote and dimmable light controls.`, `Please include installation and confirm the wiring requirements.`, `I will compare the matching models and prepare a quote with installation.`],
    ][variant];
    const transcript = [
      ...transcriptSets.map((text, index) => ({ start_seconds: [0, 12, 25, 39][index], recorded_at: now, speaker_role: index % 2 ? 'agent' : 'customer', speaker_name: index % 2 ? agent : contact.contact_name, english_text: text, source_text: text })),
    ];
    return {
      messages: transcript.map(turn => ({ role: turn.speaker_role === 'customer' ? 'user' : 'assistant', content: turn.english_text })),
      transcript_preview: transcript,
      keywords: set.keywords,
      requirements: set.requirements,
      current_intent: set.intent,
      agent_guidance: set.guidance,
      clarification_suggestions: variant === 1 ? ['Confirm number of units', 'Confirm delivery location'] : variant === 2 ? ['Confirm colour temperature', 'Confirm pack quantity'] : variant === 3 ? ['Confirm ceiling height', 'Confirm installation date'] : ['Confirm blade colour', 'Confirm delivery timeline'],
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
    return { session: { id: `chat-${contact.contact_id}`, name: contact.contact_name, phone: contact.mobile, email: contact.email, enquiry_id: `enquiry-${contact.contact_id.replace('contact-', '')}`, enquiry_stage: 'In process' }, ...payload, recorder_sessions: [], insights: [{ label: 'Customer intent', value: payload.current_intent }, { label: 'Budget', value: payload.requirements?.find((item) => item.label === 'Budget')?.value || 'Project pricing' }], recommendations: recommendedProductsFor(contact) };
  }
  if (clean.startsWith('/voice-sessions/') && clean.endsWith('/history')) {
    const sessionId = clean.split('/')[2]; const contactId = sessionId.replace(/^chat-/, ''); const contact = data.contacts.find(item => item.contact_id === contactId) || data.contacts[0];
    const payload = sessionPayload(contact);
    payload.customer_history = customerHistory(contact); payload.crm_lead = payload.customer_history.lead;
    return { session: { name: contact.contact_name, phone: contact.mobile, email: contact.email, enquiry_id: `enquiry-${contact.contact_id.replace('contact-', '')}`, enquiry_stage: 'In process', books_contact_id: contact.contact_id }, ...payload, recorder_sessions: [], conversation_id: null, insights: [{ label: 'Customer intent', value: payload.current_intent }, { label: 'Budget', value: payload.requirements?.find((item) => item.label === 'Budget')?.value || 'Project pricing' }], recommendations: recommendedProductsFor(contact) };
  }
  if (clean.includes('/voice-sessions/') && clean.endsWith('/notes')) {
    if (method === 'POST') { let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {} return { note: { id: `note-${Date.now()}`, author_name: 'Ravi Kumar', content: body.content || 'Follow up with the customer.', created_at: new Date().toISOString() } }; }
    return { notes: [
      { id: 'note-1', author_name: 'Akshat Saxena', content: 'Customer prefers a quiet BLDC fan with remote control. Shared two suitable models.', created_at: '2026-10-30T09:24:00Z' },
      { id: 'note-2', author_name: 'Ravi Kumar', content: 'Customer mentioned a budget around ₹12k. Follow up with installation offer.', created_at: '2026-10-30T09:25:00Z' },
      { id: 'note-3', author_name: 'Sumanth Reddy', content: 'Please confirm colour preference and delivery timeline before preparing the quote.', created_at: '2026-10-30T09:27:00Z' },
    ] };
  }
  if (clean.includes('/voice-sessions/') && clean.endsWith('/context')) {
    const sessionId = clean.split('/')[2];
    if (method === 'PUT') {
      let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {}
      data.voice_contexts[sessionId] = {
        product_requirements: Array.isArray(body.product_requirements) ? body.product_requirements : [],
        cart: Array.isArray(body.cart) ? body.cart : [],
      };
      writeMock(data);
    }
    return data.voice_contexts[sessionId] || { product_requirements: [], cart: [] };
  }
  if (clean.startsWith('/voice-sessions/') && clean.endsWith('/sales-documents/options')) {
    const sessionId = clean.split('/')[2];
    const contactId = sessionId.replace(/^chat-/, '');
    const contact = data.contacts.find((item) => item.contact_id === contactId) || data.contacts[0];
    return {
      mode: 'local-demo',
      document_types: ['invoice', 'quote', 'sales-order'],
      customer: { name: contact.contact_name, contact_name: contact.contact_name, phone: contact.mobile, email: contact.email },
      email: { configured: false },
    };
  }
  if (clean.startsWith('/voice-sessions/') && clean.includes('/sales-documents') && method === 'POST') {
    let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {}
    const type = body.type || 'invoice';
    const lines = Array.isArray(body.line_items) ? body.line_items : [];
    const subtotal = lines.reduce((sum, line) => sum + Number(line.rate || 0) * Number(line.quantity || 0), 0);
    const discount = lines.reduce((sum, line) => {
      const value = Number(line.discount || 0);
      return sum + (line.discount_type === 'fixed' ? value : Number(line.rate || 0) * Number(line.quantity || 0) * value / 100);
    }, 0);
    const taxable = Math.max(0, subtotal - discount);
    const tax = Math.round(taxable * 0.18 * 100) / 100;
    const total = Math.round((taxable + tax) * 100) / 100;
    const sessionId = clean.split('/')[2];
    const contact = data.contacts.find((item) => `chat-${item.contact_id}` === sessionId) || data.contacts[0];
    const existing = data.demo_documents.find((item) => item.request_id === body.request_id);
    if (existing) return existing.response;
    const serial = 1000 + data.demo_documents.length + 1;
    const prefixes = { invoice: 'INV-2026-', quote: 'QT-2026-', 'sales-order': 'SO-2026-' };
    const number = `${prefixes[type] || prefixes.invoice}${serial}`;
    const id = `demo-${type}-${serial}`;
    const document = { id, type, number, status: body.delivery === 'email' ? 'sent' : 'created', customer_name: contact.contact_name, customer_phone: contact.mobile, total, subtotal, discount, tax, date: new Date().toISOString().slice(0, 10), demo: true, line_items: lines };
    const target = type === 'quote' ? data.quotes : type === 'sales-order' ? data.orders : data.invoices;
    const idKey = type === 'quote' ? 'estimate_id' : type === 'sales-order' ? 'salesorder_id' : 'invoice_id';
    const numberKey = type === 'quote' ? 'estimate_number' : type === 'sales-order' ? 'salesorder_number' : 'invoice_number';
    target.unshift({ [idKey]: id, [numberKey]: number, customer_name: contact.contact_name, status: document.status, total, subtotal, discount, tax, date: document.date, line_items: lines });
    const pdfText = `%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF`;
    const base64 = btoa(unescape(encodeURIComponent(pdfText)));
    const response = { document, delivery: { status: body.delivery === 'email' ? 'accepted' : 'accepted', message: 'Sample document created locally. No external service was called.' }, pdf: { mime_type: 'application/pdf', base64, filename: `${number}.pdf` } };
    data.demo_documents.unshift({ request_id: body.request_id, response });
    writeMock(data);
    return response;
  }
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
