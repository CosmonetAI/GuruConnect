import seedData from '../../services/mock-data.json';
const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
export const API = `${API_BASE}/api`;

const demoZohoHosts = new Set(['connectcosmos.onrender.com']);

const mockKey = 'guruconnect-frontend-zoho-mock-data';
const demoMembers = [
  { id: 'owner-akshat', email: 'akshat.saxena@zoho.in', name: 'Akshat Saxena', role: 'owner', branch: 'Bengaluru Central' },
  { id: 'owner-meera', email: 'meera.iyer@zoho.in', name: 'Meera Iyer', role: 'owner', branch: 'Mysuru' },
  ...Array.from({ length: 22 }, (_, index) => ({ id: `sales-${index + 1}`, email: `${['ravi.kumar', 'sumanth.reddy', 'priya.gupta', 'sneha.kapoor', 'arjun.mehta', 'neha.sharma', 'karan.malhotra', 'divya.nair', 'aditya.rao', 'rohan.bhat', 'pooja.iyer', 'manish.verma', 'kavita.joshi', 'nikhil.jain', 'aditi.menon', 'varun.shah', 'meenal.patil', 'sameer.khan', 'ishita.bose', 'rahul.nair', 'tanvi.kulkarni', 'mohit.sethi'][index]}@zoho.in`, name: ['Ravi Kumar', 'Sumanth Reddy', 'Priya Gupta', 'Sneha Kapoor', 'Arjun Mehta', 'Neha Sharma', 'Karan Malhotra', 'Divya Nair', 'Aditya Rao', 'Rohan Bhat', 'Pooja Iyer', 'Manish Verma', 'Kavita Joshi', 'Nikhil Jain', 'Aditi Menon', 'Varun Shah', 'Meenal Patil', 'Sameer Khan', 'Ishita Bose', 'Rahul Nair', 'Tanvi Kulkarni', 'Mohit Sethi'][index], role: 'salesperson', branch: ['Bengaluru Central', 'Mysuru', 'Hyderabad'][index % 3] })),
  { id: 'reception-nisha', email: 'nisha.rao@zoho.in', name: 'Nisha Rao', role: 'receptionist', branch: 'Bengaluru Central' },
  { id: 'reception-kavya', email: 'kavya.menon@zoho.in', name: 'Kavya Menon', role: 'receptionist', branch: 'Mysuru' },
  { id: 'reception-arjun', email: 'arjun.das@zoho.in', name: 'Arjun Das', role: 'receptionist', branch: 'Hyderabad' },
];
const demoOwnersAndSales = demoMembers.filter((member) => member.role !== 'receptionist');
const customerFirstNames = ['Aarav', 'Aditi', 'Ananya', 'Arjun', 'Asha', 'Dev', 'Diya', 'Ishaan', 'Ira', 'Kabir', 'Kavya', 'Kiran', 'Mihir', 'Naina', 'Neha', 'Nikhil', 'Pooja', 'Ritesh', 'Riya', 'Rohan', 'Sahil', 'Sameer', 'Sanya', 'Shreya', 'Tanvi', 'Varun', 'Vikram', 'Yash', 'Zoya', 'Anika'];
const customerLastNames = ['Sharma', 'Verma', 'Mehta', 'Iyer', 'Nair', 'Rao', 'Kapoor', 'Malhotra', 'Bose', 'Joshi', 'Patel', 'Jain', 'Menon', 'Chopra', 'Desai', 'Bhat'];
function demoCustomerName(index) { return `${customerFirstNames[(index - 1) % customerFirstNames.length]} ${customerLastNames[Math.floor((index - 1) / customerFirstNames.length) % customerLastNames.length]}`; }
function demoCustomerPhone(index) { const prefix = ['98', '99', '97', '96'][index % 4]; return `${prefix}${String(10000000 + ((index * 7919) % 89999999)).padStart(8, '0')}`; }
function activeDemoMember() {
  let workspaceMemberId = '';
  let email = '';
  try {
    workspaceMemberId = localStorage.getItem('guruconnect-active-workspace') || '';
    email = sessionStorage.getItem('guruconnect-demo-account') || '';
  } catch { /* browser storage may be unavailable in a test */ }
  if (workspaceMemberId) return demoMembers.find((member) => member.id === workspaceMemberId) || demoMembers[0];
  return demoMembers.find((member) => member.email === email.toLowerCase()) || demoMembers[0];
}
function memberForContact(item, index) {
  const name = String(item.contact_name || '').trim().toLowerCase();
  const knownAssignments = { 'akshat saxena': 'sales-1', 'rahul sharma': 'sales-2', 'priya gupta': 'sales-3', 'rakesh kumar': 'sales-4' };
  if (String(item.contact_id || '') === 'contact-akshat' || String(item.mobile || '').replace(/\D/g, '') === '9311854768' || name === 'akshat saxena') return demoMembers.find((member) => member.id === 'sales-1');
  if (knownAssignments[name]) return demoMembers.find((member) => member.id === knownAssignments[name]);
  const savedMember = demoMembers.find((member) => member.id === item.assigned_id);
  return savedMember || demoOwnersAndSales[index % demoOwnersAndSales.length];
}
function visibleContactsFor(data) {
  const member = activeDemoMember();
  return member.role === 'receptionist' ? data.contacts : data.contacts.filter((item) => item.assigned_id === member.id);
}
function readMock() {
  try { const saved = sessionStorage.getItem(mockKey); return expandMockData(saved ? JSON.parse(saved) : structuredClone(seedData)); } catch { return expandMockData(structuredClone(seedData)); }
}
function expandMockData(data) {
  const branches = ['Bengaluru Central', 'Mysuru', 'Hyderabad'];
  const executives = ['Ravi Kumar', 'Sumanth Reddy', 'Priya Gupta', 'Sneha Kapoor'];
  data.contacts = data.contacts.map((item) => {
    const match = String(item.contact_id ?? '').match(/^contact-mock-(\d+)$/);
    if (!match || !String(item.contact_name ?? '').startsWith('Demo Customer')) return item;
    const index = Number(match[1]);
    const name = demoCustomerName(index);
    return { ...item, contact_name: name, company_name: item.company_name === 'Retail customer' ? item.company_name : `${name} Home Studio`, company: item.company === 'Retail customer' ? item.company : `${name} Home Studio`, mobile: demoCustomerPhone(index), email: `${name.toLowerCase().replaceAll(' ', '.')}@zoho.in` };
  });
  data.contacts = data.contacts.map((item, index) => ({
    ...item,
    branch: item.branch || branches[index % branches.length],
    assigned_id: memberForContact(item, index).id,
    assigned: memberForContact(item, index).name,
    customerType: item.customerType || 'Retail',
    company: item.company || item.company_name || 'Individual customer',
    project: item.project || `${branches[index % branches.length]} home project`,
    preferredContact: item.preferredContact || (index % 2 ? 'Email' : 'Phone'),
    sources: item.sources || [index % 3 === 1 ? 'Incoming Call' : 'Retail walk-in'],
  }));
  const productImages = ['/product-aero-bldc.jpg', '/product-luxe-led.jpg', '/product-led-bulb.jpg', '/product-pedestal-fan.jpg'];
  data.products = data.products.map((item, index) => ({ ...item, image_url: item.image_url || productImages[index % productImages.length] }));
  const workTypes = [
    ['quote', 'Quote discussed', 'QT-2026-1001', 'Draft'],
    ['invoice', 'Invoice discussed', 'INV-2026-1002', 'Sent'],
    ['sales-order', 'Sales order discussed', 'SO-2026-1003', 'Confirmed'],
  ];
  data.contacts.forEach((contact, index) => {
    const isAkshat = String(contact.contact_name || '').trim().toLowerCase() === 'akshat saxena' || String(contact.mobile || '').replace(/\D/g, '') === '9311854768';
    if (isAkshat) { contact.demo_work_type = null; contact.demo_work_label = null; contact.demo_work_number = null; contact.demo_work_status = null; return; }
    const [type, label, number, status] = workTypes[index % workTypes.length];
    contact.demo_work_type = type;
    contact.demo_work_label = label;
    contact.demo_work_number = number;
    contact.demo_work_status = status;
  });
  const fanNames = ['Aero', 'Luxe', 'Havells', 'Atomberg', 'Crompton', 'Orient', 'Usha', 'Bajaj', 'Superfan', 'V-Guard'];
  while (data.products.length < 100) { const i = data.products.length + 1; data.products.push({ item_id: `item-mock-${i}`, name: `${fanNames[i % fanNames.length]} Ceiling Fan ${1000 + (i % 4) * 100}mm`, sku: `FAN-MOCK-${String(i).padStart(3, '0')}`, rate: 1999 + (i % 12) * 500, status: 'active', stock_on_hand: 5 + (i % 40), description: 'Approved catalogue product for the frontend demo.', unit: 'pcs', image_url: productImages[i % productImages.length] }); }
  while (data.contacts.length < 240) { const i = data.contacts.length + 1; const branch = branches[i % branches.length]; const member = memberForContact({ contact_id: `contact-mock-${i}` }, i); const [demoType, demoLabel, demoNumber, demoStatus] = workTypes[i % workTypes.length]; const name = demoCustomerName(i); data.contacts.push({ contact_id: `contact-mock-${i}`, contact_name: name, company_name: i % 2 ? 'Retail customer' : `${name} Home Studio`, company: i % 2 ? 'Retail customer' : `${name} Home Studio`, contact_type: 'customer', mobile: demoCustomerPhone(i), email: `${name.toLowerCase().replaceAll(' ', '.')}@zoho.in`, status: 'active', branch, assigned_id: member.id, assigned: member.name, customerType: i % 4 === 0 ? 'Dealer' : 'Retail', project: `${branch} customer project`, preferredContact: i % 2 ? 'Email' : 'Phone', sources: [i % 3 === 1 ? 'Incoming Call' : 'Retail walk-in'], demo_work_type: demoType, demo_work_label: demoLabel, demo_work_number: `${demoNumber.slice(0, -1)}${String(1000 + i).padStart(4, '0')}`, demo_work_status: demoStatus }); }
  const documentSets = [['quotes', 'estimate_id', 'estimate_number', 'QT-2026-', 'sent'], ['orders', 'salesorder_id', 'salesorder_number', 'SO-2026-', 'confirmed'], ['purchase_orders', 'purchaseorder_id', 'purchaseorder_number', 'PO-2026-', 'issued'], ['invoices', 'invoice_id', 'invoice_number', 'INV-2026-', 'sent']];
  for (const [key, idKey, numberKey, prefix, status] of documentSets) while (data[key].length < 20) { const i = data[key].length + 1; const customer = data.contacts[i % data.contacts.length]; data[key].push({ [idKey]: `${key}-mock-${i}`, [numberKey]: `${prefix}${String(1000 + i).padStart(4, '0')}`, customer_name: customer.contact_name, vendor_name: key === 'purchase_orders' ? 'Guru Electricals Supply' : undefined, date: `2026-10-${String(1 + (i % 28)).padStart(2, '0')}`, status, total: 4999 + (i % 14) * 3250, currency_symbol: '₹', line_items: [] }); }
  const seededDocumentTypes = [
    ['quote', 'quotes', 'estimate_id', 'estimate_number', 'QT-2025-'],
    ['sales-order', 'orders', 'salesorder_id', 'salesorder_number', 'SO-2025-'],
    ['invoice', 'invoices', 'invoice_id', 'invoice_number', 'INV-2025-'],
  ];
  const seedDocumentLines = (customer, index) => data.products.slice(index % 4, (index % 4) + 2).map((product, lineIndex) => ({
    item_id: product.item_id,
    item_name: product.name,
    name: product.name,
    description: product.description || product.name,
    sku: product.sku,
    quantity: lineIndex === 0 ? (index % 3) + 1 : 1,
    rate: Number(product.rate || 0),
    unit: product.unit || 'pcs',
    discount: lineIndex === 0 ? 5 : 0,
    discount_type: 'percentage',
  }));
  const seedDocumentTotals = (lines) => {
    const subtotal = lines.reduce((sum, line) => sum + Number(line.rate || 0) * Number(line.quantity || 0), 0);
    const discount = lines.reduce((sum, line) => { const gross = Number(line.rate || 0) * Number(line.quantity || 0); const value = Number(line.discount || 0); return sum + (line.discount_type === 'fixed' ? value : gross * value / 100); }, 0);
    const taxable = Math.max(0, subtotal - discount);
    const tax = Math.round(taxable * 0.18 * 100) / 100;
    return { subtotal, discount, taxable, tax, cgst: Math.round(taxable * 0.09 * 100) / 100, sgst: Math.round(taxable * 0.09 * 100) / 100, total: Math.round((taxable + tax) * 100) / 100 };
  };
  demoOwnersAndSales.forEach((member, memberIndex) => {
    const customer = data.contacts.find((item) => item.assigned_id === member.id && item.contact_name !== 'Akshat Saxena') || data.contacts[(memberIndex + 5) % data.contacts.length];
    seededDocumentTypes.forEach(([type, collection, idKey, numberKey, prefix], typeIndex) => {
      data[collection] = data[collection] || [];
      if (data[collection].some((item) => item.salesperson_id === member.id)) return;
      const lines = seedDocumentLines(customer, memberIndex + typeIndex);
      const totals = seedDocumentTotals(lines);
      const serial = 3000 + memberIndex * 10 + typeIndex + 1;
      data[collection].unshift({ [idKey]: `${collection}-demo-${member.id}`, [numberKey]: `${prefix}${serial}`, contact_id: customer.contact_id, customer_id: customer.contact_id, customer_name: customer.contact_name, customer_email: customer.email, salesperson_id: member.id, salesperson_name: member.name, assigned: member.name, date: `2025-${String((memberIndex % 11) + 1).padStart(2, '0')}-${String((memberIndex % 25) + 1).padStart(2, '0')}`, status: type === 'invoice' ? 'sent' : type === 'sales-order' ? 'confirmed' : 'sent', currency_symbol: '₹', ...totals, tax_percentage: 18, line_items: lines, notes: `Prepared by ${member.name} for ${customer.contact_name}.` });
    });
  });
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
function documentDefinition(type) {
  if (type === 'quote') return { collection: 'quotes', idKey: 'estimate_id', numberKey: 'estimate_number', prefix: 'QT-2026-', responseKey: 'quote' };
  if (type === 'sales-order') return { collection: 'orders', idKey: 'salesorder_id', numberKey: 'salesorder_number', prefix: 'SO-2026-', responseKey: 'order' };
  if (type === 'purchase-order') return { collection: 'purchase_orders', idKey: 'purchaseorder_id', numberKey: 'purchaseorder_number', prefix: 'PO-2026-', responseKey: 'purchase_order' };
  return { collection: 'invoices', idKey: 'invoice_id', numberKey: 'invoice_number', prefix: 'INV-2026-', responseKey: 'invoice' };
}
function parseDocumentBody(opts) { try { return JSON.parse(opts.body || '{}'); } catch { return {}; } }
function calculateDocumentTotals(lines, body = {}) {
  const normalizedLines = (Array.isArray(lines) ? lines : []).map((line) => ({ ...line, quantity: Number(line.quantity || 0), rate: Number(line.rate || 0), discount: Number(line.discount || 0) }));
  const subtotal = normalizedLines.reduce((sum, line) => sum + line.rate * line.quantity, 0);
  const hasLineDiscounts = normalizedLines.some((line) => line.discount > 0);
  const itemLevel = body.discount_type === 'item_level' || body.discount_type === 'line_item' || (!body.discount && hasLineDiscounts);
  const lineDiscount = normalizedLines.reduce((sum, line) => { const gross = line.rate * line.quantity; return sum + (line.discount_type === 'fixed' ? line.discount : gross * line.discount / 100); }, 0);
  const rawDiscount = body.discount;
  const discountValue = typeof rawDiscount === 'string' && rawDiscount.trim().endsWith('%') ? Number.parseFloat(rawDiscount) : Number(rawDiscount || 0);
  const discount = itemLevel ? lineDiscount : body.discount_type === 'percentage' ? subtotal * discountValue / 100 : discountValue;
  const taxable = Math.max(0, subtotal - discount);
  const taxPercentage = Number(body.tax_percentage ?? body.gst_rate ?? 18);
  const tax = Math.round(taxable * taxPercentage / 100 * 100) / 100;
  return { line_items: normalizedLines, subtotal, discount, discount_type: body.discount_type || (itemLevel ? 'item_level' : 'fixed'), taxable, tax_percentage: taxPercentage, tax, cgst: Math.round(tax / 2 * 100) / 100, sgst: Math.round(tax / 2 * 100) / 100, total: Math.round((taxable + tax) * 100) / 100 };
}
function demoPdf({ number, type, contact, lines, subtotal, discount, taxable, cgst, sgst, total }) {
  const title = type === 'quote' ? 'QUOTATION' : type === 'sales-order' ? 'SALES ORDER' : 'TAX INVOICE';
  const escapePdf = (value) => String(value ?? '').replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
  const rows = [
    'ConnectCosmos Commerce', 'Demo sales desk | Bengaluru', '', title, `Reference: ${number}`,
    `Bill To: ${contact.contact_name}`, `Phone: ${contact.mobile || 'Not provided'}`, `Email: ${contact.email || 'Not provided'}`, '',
    'ORDER SUMMARY', 'Item | Qty | Rate | Discount | Amount',
    ...lines.map((line, index) => {
      const gross = Number(line.rate || 0) * Number(line.quantity || 0);
      const lineDiscount = line.discount_type === 'fixed' ? Number(line.discount || 0) : gross * Number(line.discount || 0) / 100;
      return `${index + 1}. ${String(line.description || 'Catalogue item').slice(0, 32)} | ${line.quantity} | INR ${Number(line.rate || 0).toFixed(2)} | ${line.discount_type === 'fixed' ? `INR ${lineDiscount.toFixed(2)}` : `${Number(line.discount || 0)}%`} | INR ${(gross - lineDiscount).toFixed(2)}`;
    }),
    '', `Subtotal: INR ${subtotal.toFixed(2)}`, `Discount: INR ${discount.toFixed(2)}`, `Taxable amount: INR ${taxable.toFixed(2)}`,
    `CGST: INR ${cgst.toFixed(2)}`, `SGST: INR ${sgst.toFixed(2)}`, `TOTAL: INR ${total.toFixed(2)}`,
    '', 'Prepared in demo mode. Values are simulated for workflow review.',
  ];
  let y = 760;
  const commands = ['BT', '/F1 10 Tf'];
  rows.forEach((row) => { commands.push(`1 0 0 1 48 ${y} Tm (${escapePdf(row)}) Tj`); y -= 16; if (y < 48) y = 760; });
  commands.push('ET');
  const stream = commands.join('\n');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let pdf = '%PDF-1.4\n'; const offsets = [0];
  objects.forEach((object, index) => { offsets[index + 1] = pdf.length; pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = pdf.length; pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n `).join('\n')}\ntrailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return btoa(unescape(encodeURIComponent(pdf)));
}
function mockZoho(path, opts = {}) {
  const data = readMock(); const clean = path.split('?')[0]; const query = new URLSearchParams(path.split('?')[1] || ''); const method = String(opts.method || 'GET').toUpperCase();
  const search = (query.get('search') || '').toLowerCase();
  const filter = items => items.filter(item => !search || JSON.stringify(item).toLowerCase().includes(search));
  const visibleContacts = visibleContactsFor(data);
  const visibleContactIds = new Set(visibleContacts.map((item) => item.contact_id));
  const visibleContactNames = new Set(visibleContacts.map((item) => item.contact_name));
  const visibleRecords = (items) => items.filter((item) => visibleContactIds.has(item.contact_id) || visibleContactIds.has(item.Contact_Name?.id) || visibleContactNames.has(item.customer_name) || visibleContactNames.has(item.Contact_Name?.name) || activeDemoMember().role === 'receptionist');
  const contactForSession = (contactId) => visibleContacts.find((item) => item.contact_id === contactId) || visibleContacts[0] || data.contacts[0];
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
  const demoWorkItemFor = (contact, products) => {
    const type = contact.demo_work_type || 'quote';
    const lineItems = products.slice(0, 2).map((product, index) => ({ item_id: product.item_id, product: product.name, name: product.name, sku: product.sku, rate: product.rate, quantity: type === 'sales-order' ? (index ? 3 : 12) : index ? 1 : 2, discount: index ? 0 : 5, discount_type: 'percentage', image_url: product.image_url }));
    return { type, label: contact.demo_work_label || (type === 'quote' ? 'Quote ready' : type === 'invoice' ? 'Invoice ready' : 'Sales order ready'), number: contact.demo_work_number, status: contact.demo_work_status, editable: true, line_items: lineItems };
  };
  const sessionPayload = (contact, agent = 'Ravi Kumar') => {
    const now = new Date().toISOString();
    const variant = variantFor(contact);
    const set = recommendationSets[variant];
    const productsForSession = recommendedProductsFor(contact);
    const demoWorkItem = demoWorkItemFor(contact, productsForSession);
    const documentRecommendation = { recommendation_kind: 'document', name: `${demoWorkItem.label} ${demoWorkItem.number}`, document_type: demoWorkItem.type, document_number: demoWorkItem.number, status: demoWorkItem.status, description: `The customer asked for this ${demoWorkItem.type === 'sales-order' ? 'sales order' : demoWorkItem.type} to be prepared and shared. Edit the order summary before sending.` };
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
    const documentPrompt = demoWorkItem.type === 'invoice' ? 'Please send me the invoice for the selected products.' : demoWorkItem.type === 'sales-order' ? 'The customer approved the options. Please create and share the sales order.' : 'Please prepare a quote and share it with me before I confirm the order.';
    const documentReply = demoWorkItem.type === 'invoice' ? `I will prepare invoice ${demoWorkItem.number} with GST and send it to your email.` : demoWorkItem.type === 'sales-order' ? `I will prepare sales order ${demoWorkItem.number} with the agreed quantities and GST.` : `I will prepare ${demoWorkItem.number} with the product summary and share it for review.`;
    transcript[2].english_text = documentPrompt; transcript[3].english_text = documentReply;
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
      demo_work_item: demoWorkItem,
      document_recommendation: documentRecommendation,
    };
  };
  if (clean === '/products' && method === 'POST') {
    let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {}
    const name = String(body.name || body.item_name || '').trim();
    if (!name) throw Object.assign(new Error('Item name is required.'), { status: 400 });
    const now = Date.now();
    const item = { item_id: `item-custom-${now}`, name, sku: String(body.sku || `ITEM-${String(now).slice(-6)}`).trim(), item_type: body.item_type || 'inventory', unit: body.unit || 'pcs', rate: Number(body.rate || 0), purchase_rate: Number(body.purchase_rate || 0), stock_on_hand: Number(body.stock_on_hand || 0), reorder_level: Number(body.reorder_level || 0), description: String(body.description || '').trim(), tax_name: body.tax_name || 'GST 18%', tax_percentage: Number(body.tax_percentage || 18), can_be_sold: body.can_be_sold !== false, can_be_purchased: body.can_be_purchased !== false, track_inventory: body.track_inventory !== false, status: 'active', available_for_sale: true, image_url: '/product-aero-bldc.jpg', created_time: new Date(now).toISOString() };
    data.products.unshift(item); writeMock(data); return { item };
  }
  const documentCollectionFor = (pathName) => pathName === '/quotes' || pathName.startsWith('/quotes/') ? 'quote' : pathName === '/orders' || pathName.startsWith('/orders/') ? 'sales-order' : pathName === '/purchase-orders' || pathName.startsWith('/purchase-orders/') ? 'purchase-order' : pathName === '/invoices' || pathName.startsWith('/invoices/') ? 'invoice' : null;
  const documentFromBody = (type, body, existing = null) => {
    const definition = documentDefinition(type);
    const customer = data.contacts.find((item) => item.contact_id === body.customer_id || item.contact_id === body.contactId || item.contact_id === body.contact_id) || data.contacts.find((item) => item.contact_name === body.customer_name) || visibleContacts[0] || data.contacts[0];
    const member = activeDemoMember();
    const mergedBody = { ...(existing || {}), ...body };
    const totals = calculateDocumentTotals(body.line_items || existing?.line_items || [], mergedBody);
    const nextNumber = body[definition.numberKey] || body.reference_number || existing?.[definition.numberKey] || `${definition.prefix}${String(1000 + data[definition.collection].length + 1).padStart(4, '0')}`;
    const nextId = existing?.[definition.idKey] || body[definition.idKey] || `${definition.collection}-demo-${Date.now()}`;
    return { ...(existing || {}), ...body, [definition.idKey]: nextId, [definition.numberKey]: nextNumber, reference_number: body.reference_number || existing?.reference_number || nextNumber, contact_id: body.contact_id || body.customer_id || existing?.contact_id || customer?.contact_id, customer_id: body.customer_id || existing?.customer_id || customer?.contact_id, customer_name: body.customer_name || existing?.customer_name || customer?.contact_name, customer_email: body.customer_email || body.email || existing?.customer_email || customer?.email, salesperson_id: body.salesperson_id || existing?.salesperson_id || member.id, salesperson_name: body.salesperson_name || existing?.salesperson_name || member.name, date: body.date || existing?.date || new Date().toISOString().slice(0, 10), status: body.status || existing?.status || (type === 'invoice' ? 'sent' : type === 'sales-order' ? 'confirmed' : 'sent'), currency_symbol: existing?.currency_symbol || '₹', ...totals, notes: body.notes ?? existing?.notes ?? '' };
  };
  const documentPathType = documentCollectionFor(clean);
  if (documentPathType && method === 'POST' && !clean.includes('/email')) {
    const body = parseDocumentBody(opts);
    const definition = documentDefinition(documentPathType);
    const record = documentFromBody(documentPathType, body);
    data[definition.collection].unshift(record);
    writeMock(data);
    return { [definition.responseKey]: record, document: record, reference_number: record[definition.numberKey] };
  }
  if (documentPathType && method === 'PUT' && clean.split('/').length > 2) {
    const body = parseDocumentBody(opts);
    const definition = documentDefinition(documentPathType);
    const id = decodeURIComponent(clean.split('/').pop());
    const index = data[definition.collection].findIndex((item) => item[definition.idKey] === id);
    if (index < 0) throw Object.assign(new Error('Document not found.'), { status: 404 });
    const record = documentFromBody(documentPathType, body, data[definition.collection][index]);
    data[definition.collection][index] = record;
    writeMock(data);
    return { [definition.responseKey]: record, document: record };
  }
  if (clean === '/products') return mockList(filter(data.products), 'items');
  if (clean === '/contacts') return mockList(filter(visibleContacts), 'contacts');
  if (clean === '/quotes') return mockList(visibleRecords(filter(data.quotes)), 'quotes');
  if (clean === '/orders') return mockList(visibleRecords(filter(data.orders)), 'orders');
  if (clean === '/purchase-orders') return mockList(filter(data.purchase_orders), 'purchase_orders');
  if (clean === '/invoices') return mockList(visibleRecords(filter(data.invoices)), 'invoices');
  if (clean === '/documents/options') return { reporting_tags: [{ tag_id: 'intent', tag_name: 'Customer intent' }] };
  if (clean.startsWith('/purchase-orders/options/tag-options')) return { options: [{ option_id: 'new', option_name: 'New enquiry' }, { option_id: 'hot', option_name: 'High intent' }] };
  if (clean === '/pipelines/layouts') return { layouts: [{ id: 'sales-pipeline', name: 'Sales pipeline', stages: ['Qualification', 'Proposal/Price Quote', 'Negotiation/Review', 'Closed Won', 'Closed Lost'] }] };
  if (clean === '/pipelines' || clean === '/pipelines/search') return { records: visibleRecords(filter(data.pipelines)), last_sync: new Date().toISOString(), has_more: false };
  if (clean.startsWith('/pipelines/record/')) { const id = clean.split('/').pop(); const record = data.pipelines.find(item => item.id === id) || data.pipelines[0]; if (method === 'PUT' && record) { let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {} Object.assign(record, body); writeMock(data); } if (method === 'DELETE') { data.pipelines = data.pipelines.filter(item => item.id !== id); writeMock(data); return { ok: true }; } return { record }; }
  if (clean.startsWith('/enquiries/') && method === 'PATCH') { let body = {}; try { body = JSON.parse(opts.body || '{}'); } catch {} const enquiryId = clean.split('/')[2]; const enquiry = (data.enquiries || []).find(item => item.id === enquiryId || item.enquiry_id === enquiryId); const contact = data.contacts.find(item => item.contact_id === enquiry?.contact_id || item.mobile === enquiry?.phone || item.contact_name === enquiry?.customer_name) || data.contacts.find(item => enquiryId.includes(String(item.contact_id).replace('contact-', '')) || enquiryId.includes(item.mobile)); if (enquiry) enquiry.stage = body.stage || enquiry.stage; const lead = contact && data.pipelines.find(item => item.Contact_Name?.id === contact.contact_id); if (lead) { const stage = String(body.stage || '').toLowerCase(); lead.Stage = stage.includes('closed') ? 'Closed Won' : stage.includes('process') ? 'Needs Analysis' : 'Qualification'; lead.Lead_Status = stage.includes('closed') ? 'Converted' : 'Registered'; writeMock(data); } return { ok: true, enquiry, crm_lead: lead }; }
  if (clean === '/pipelines/sync') return { last_sync: new Date().toISOString(), limited: false };
  if (clean === '/bigin/deleted/Pipelines' || clean === '/bigin/deleted/Tasks') return { items: [] };
  if (clean === '/chats') return visibleContacts.slice(0, 12).map((item, index) => ({ id: `chat-${item.contact_id}`, name: item.contact_name, title: item.project || (index % 2 ? 'Product follow-up' : 'New customer enquiry'), updated_at: `2026-10-${String(30 - (index % 20)).padStart(2, '0')}T09:20:00Z`, phone: item.mobile, sources: item.sources }));
  if (clean === '/session-contacts') return { contacts: visibleContacts.map(item => ({ session_id: `chat-${item.contact_id}`, id: item.contact_id, name: item.contact_name, phone: item.mobile, email: item.email, sources: item.sources, branch: item.branch, assigned: item.assigned, assigned_id: item.assigned_id, customerType: item.customerType, company: item.company || item.company_name, project: item.project, preferredContact: item.preferredContact, demo_work_type: item.demo_work_type, demo_work_label: item.demo_work_label, demo_work_number: item.demo_work_number, demo_work_status: item.demo_work_status })) };
  if (clean === '/voice-sessions' && method === 'POST') {
    let input = {}; try { input = JSON.parse(opts.body || '{}'); } catch {}
    const contact = visibleContacts.find(item => item.mobile === input.phone || item.contact_name === input.name) || visibleContacts[0] || data.contacts[0];
    const payload = sessionPayload(contact, activeDemoMember().name);
    payload.customer_history = customerHistory(contact); payload.crm_lead = payload.customer_history.lead;
    return { session: { id: `chat-${contact.contact_id}`, name: contact.contact_name, phone: contact.mobile, email: contact.email, enquiry_id: `enquiry-${contact.contact_id.replace('contact-', '')}`, enquiry_stage: 'In process', demo_work_item: payload.demo_work_item }, ...payload, recorder_sessions: [], insights: [{ label: 'Customer intent', value: payload.current_intent }, { label: 'Budget', value: payload.requirements?.find((item) => item.label === 'Budget')?.value || 'Project pricing' }], recommendations: recommendedProductsFor(contact) };
  }
  if (clean.startsWith('/voice-sessions/') && clean.endsWith('/history')) {
    const sessionId = clean.split('/')[2]; const contactId = sessionId.replace(/^chat-/, ''); const contact = contactForSession(contactId);
    const payload = sessionPayload(contact);
    payload.customer_history = customerHistory(contact); payload.crm_lead = payload.customer_history.lead;
    return { session: { name: contact.contact_name, phone: contact.mobile, email: contact.email, enquiry_id: `enquiry-${contact.contact_id.replace('contact-', '')}`, enquiry_stage: 'In process', books_contact_id: contact.contact_id, demo_work_item: payload.demo_work_item }, ...payload, recorder_sessions: [], conversation_id: null, insights: [{ label: 'Customer intent', value: payload.current_intent }, { label: 'Budget', value: payload.requirements?.find((item) => item.label === 'Budget')?.value || 'Project pricing' }], recommendations: recommendedProductsFor(contact) };
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
    const contact = contactForSession(contactId);
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
    const contact = contactForSession(sessionId.replace(/^chat-/, '')) || visibleContacts.find((item) => item.mobile === body.customer_phone || item.contact_name === body.customer_name) || visibleContacts[0] || data.contacts[0];
    const existing = data.demo_documents.find((item) => item.request_id === body.request_id);
    if (existing) return existing.response;
    const serial = 1000 + data.demo_documents.length + 1;
    const prefixes = { invoice: 'INV-2026-', quote: 'QT-2026-', 'sales-order': 'SO-2026-' };
    const number = `${prefixes[type] || prefixes.invoice}${serial}`;
    const id = `demo-${type}-${serial}`;
    const cgst = Math.round(taxable * 0.09 * 100) / 100;
    const sgst = cgst;
    const owner = activeDemoMember();
    const document = { id, type, number, status: body.delivery === 'email' ? 'sent' : 'created', customer_id: contact.contact_id, customer_name: contact.contact_name, customer_email: contact.email, customer_phone: contact.mobile, salesperson_id: owner.id, salesperson_name: owner.name, total, subtotal, discount, discount_type: 'item_level', taxable, tax, cgst, sgst, gst_rate: 18, gstin: '29CCXPC4821K1Z7', date: new Date().toISOString().slice(0, 10), demo: true, line_items: lines };
    const target = type === 'quote' ? data.quotes : type === 'sales-order' ? data.orders : data.invoices;
    const idKey = type === 'quote' ? 'estimate_id' : type === 'sales-order' ? 'salesorder_id' : 'invoice_id';
    const numberKey = type === 'quote' ? 'estimate_number' : type === 'sales-order' ? 'salesorder_number' : 'invoice_number';
    target.unshift({ [idKey]: id, [numberKey]: number, customer_id: contact.contact_id, customer_name: contact.contact_name, customer_email: contact.email, salesperson_id: owner.id, salesperson_name: owner.name, status: document.status, total, subtotal, discount, discount_type: 'item_level', taxable, tax, cgst, sgst, gst_rate: 18, gstin: '29CCXPC4821K1Z7', date: document.date, line_items: lines });
    const base64 = demoPdf({ number, type, contact, lines, subtotal, discount, taxable, cgst, sgst, total });
    const response = { document, delivery: { status: body.delivery === 'email' ? 'accepted' : 'accepted', message: 'Sample document created locally. No external service was called.' }, pdf: { mime_type: 'application/pdf', base64, filename: `${number}.pdf` } };
    data.demo_documents.unshift({ request_id: body.request_id, response });
    writeMock(data);
    return response;
  }
  if (clean.startsWith('/chats/') && clean.endsWith('/messages')) return [{ role: 'user', content: 'I am looking for a ceiling fan for my living room.' }, { role: 'assistant', content: 'I can suggest quiet BLDC options with remote control.' }];
  if (clean === '/chat' && method === 'POST') return { session_id: `chat-${visibleContacts[0]?.contact_id || 'akshat'}`, answer: 'I found matching products and can prepare a quotation.' };
  if (method === 'POST' && clean.startsWith('/books/')) return { estimate: { estimate_number: `QT-2026-${1030 + data.quotes.length}` }, reference_number: `QT-2026-${1030 + data.quotes.length}` };
  return {};
}

/** @param {string} path @param {Record<string, any>} opts @param {string | null} workspaceId */
export async function call(path, opts = {}, workspaceId = null) {
  const hostname = location.hostname;
  const demoMode = import.meta.env.VITE_DEMO_MODE === 'true' || demoZohoHosts.has(hostname) || hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
  if (demoMode) return mockZoho(path, opts);
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
