import React, { useEffect, useState } from 'react';
import { call } from '../shared/api';

export function ProductValue({ value }) {
  if (Array.isArray(value)) return <>{value.map((entry, index) => <span key={index}>{typeof entry === 'object' ? JSON.stringify(entry) : String(entry)}{index < value.length - 1 ? ', ' : ''}</span>)}</>;
  if (value && typeof value === 'object') return <span>{JSON.stringify(value)}</span>;
  return <span>{String(value)}</span>;
}

const MONEY_FIELDS = new Set(['rate', 'purchase_rate', 'selling_price', 'sales_rate', 'total', 'bcy_total', 'balance', 'amount', 'shipping_charge', 'adjustment', 'tax_total', 'discount_total', 'discount_amount', 'sub_total', 'outstanding_receivable_amount', 'outstanding_payable_amount', 'unused_credits_receivable_amount', 'unused_credits_payable_amount', 'credit_limit', 'customer_balance']);

export function MoneyValue({ field, value }) {
  if (!MONEY_FIELDS.has(field) || typeof value === 'boolean' || value === null || value === undefined || value === '' || (typeof value === 'string' && value.trim().endsWith('%'))) return <ProductValue value={value} />;
  return <span>â‚¹{value}</span>;
}

export function ProductCard({ item }) {
  const [current, setCurrent] = useState(item);
  const [showDetails, setShowDetails] = useState(false);
  useEffect(() => setCurrent(item), [item]);
  const fields = Object.entries(current || {}).filter(([, value]) => value !== null && value !== undefined && value !== '' && !(Array.isArray(value) && value.length === 0));
  const label = key => key.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
  const important = new Set(['item_id', 'name', 'item_name', 'sku', 'unit', 'status', 'rate', 'description', 'stock_on_hand', 'available_stock', 'available_for_sale', 'item_type', 'product_type', 'can_be_sold', 'track_inventory']);
  const sections = [
    ['Product', ['item_id', 'name', 'item_name', 'sku', 'unit', 'status', 'description']],
    ['Pricing & accounts', ['rate', 'purchase_rate', 'tax_name', 'tax_percentage', 'account_name', 'purchase_account_name']],
    ['Inventory', ['stock_on_hand', 'available_stock', 'actual_available_stock', 'available_for_sale', 'reorder_level', 'track_inventory']],
    ['Sales & purchasing', ['can_be_sold', 'can_be_purchased', 'item_type', 'product_type', 'purpose_of_use', 'vendor_name']],
    ['Other details', fields.map(([key]) => key).filter(key => !important.has(key))]
  ].map(([title, keys]) => ({ title, fields: keys.map(key => fields.find(([field]) => field === key)).filter(Boolean) })).filter(section => section.fields.length);
  const save = async values => { const result = await call('/products/' + current.item_id, { method: 'PUT', body: JSON.stringify(values) }); setCurrent(old => ({ ...old, ...(result.item?.item || result.item || {}) })); };
  return <article className="product-card"><div className="product-card-top"><span className="active-label">Active</span>{current.sku && <span className="product-sku">SKU: {current.sku}</span>}</div><h3>{current.name || current.item_name || 'Product'}</h3><div className="product-summary"><span>{current.item_id ? 'Item ID: ' + current.item_id : 'Zoho Books item'}</span>{current.rate != null && <strong>₹{current.rate}</strong>}</div><div className="product-card-actions"><button type="button" className="secondary" onClick={() => setShowDetails(true)}>Show more</button></div>{showDetails && <DetailModal eyebrow="ZOHO BOOKS ITEM" title={current.name || current.item_name || 'Product'} fields={fields} editableKeys={['name', 'item_name', 'sku', 'unit', 'description', 'rate', 'purchase_rate', 'reorder_level']} onSave={save} onClose={() => setShowDetails(false)} />}</article>;
}

export function PhoneExtensionSelect({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const options = [['+91', 'India'], ['+1', 'US/Canada'], ['+44', 'UK'], ['+971', 'UAE']];
  return <div className="extension-picker"><button type="button" className="extension-current" onClick={() => setOpen(current => !current)}>{value} <span>âŒ„</span></button>{open && <div className="extension-options">{options.map(([code, name]) => <button type="button" key={code} onClick={() => { onChange({ target: { value: code } }); setOpen(false); }}>{code} â€” {name}</button>)}</div>}</div>;
}

export function DetailModal({ eyebrow, title, fields, onClose, action, onSave, editableKeys = [] }) {
  const label = key => key.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
  const [editing, setEditing] = useState(false); const [draft, setDraft] = useState({}); const [saving, setSaving] = useState(false);
  useEffect(() => setDraft(Object.fromEntries(fields)), [fields]);
  const groups = [
    ['Identity', ['id', 'contact_id', 'estimate_id', 'estimate_number', 'salesorder_id', 'salesorder_number', 'purchaseorder_id', 'purchaseorder_number', 'invoice_id', 'invoice_number', 'reference_number']],
    ['People & account', ['customer_name', 'customer_id', 'vendor_name', 'vendor_id', 'company_name', 'email', 'phone', 'mobile', 'Full_Name', 'First_Name', 'Last_Name', 'Title', 'Account_Name']],
    ['Dates & status', ['date', 'due_date', 'expiry_date', 'created_time', 'last_modified_time', 'Created_Time', 'Modified_Time', 'Last_Activity_Time', 'status', 'current_sub_status', 'order_status', 'invoiced_status', 'paid_status', 'shipped_status']],
    ['Amounts & currency', ['total', 'balance', 'rate', 'quantity', 'currency_code', 'currency_symbol', 'discount', 'tax_total', 'shipping_charge', 'adjustment']],
    ['Addresses & notes', ['billing_address', 'shipping_address', 'Mailing_Street', 'Mailing_City', 'Mailing_State', 'Mailing_Country', 'Mailing_Zip', 'description', 'notes', 'terms']],
  ];
  const used = new Set(groups.flatMap(([, keys]) => keys));
  const sections = groups.map(([name, keys]) => ({ name, fields: keys.map(key => fields.find(([field]) => field === key)).filter(Boolean) })).filter(section => section.fields.length);
  const other = fields.filter(([key]) => !used.has(key));
  if (other.length) sections.push({ name: 'Other details', fields: other });
  const isEditable = key => onSave && editableKeys.includes(key) && !['id', 'contact_id', 'estimate_id', 'salesorder_id', 'purchaseorder_id', 'invoice_id'].includes(key);
  const save = async () => { setSaving(true); try { const values = Object.fromEntries(editableKeys.filter(key => Object.prototype.hasOwnProperty.call(draft, key)).map(key => { let value = draft[key]; if (['line_items', 'billing_address', 'shipping_address'].includes(key) && typeof value === 'string') { try { value = JSON.parse(value); } catch { /* leave invalid JSON for the API to report */ } } return [key, value]; })); await onSave(values); setEditing(false); } finally { setSaving(false); } };
  const editValue = (key, value) => typeof value === 'object' ? JSON.stringify(value, null, 2) : (value ?? '');
  return <div className="modal-backdrop" onClick={onClose}><div className="product-details-modal" onClick={event => event.stopPropagation()}><div className="modal-heading"><div><small>{eyebrow}</small><h3>{title}</h3></div><button type="button" onClick={onClose}>×</button></div>{(action || onSave) && <div className="detail-modal-action">{action}{onSave && !editing && <button type="button" className="secondary" onClick={() => setEditing(true)}>Edit</button>}{onSave && editing && <><button type="button" className="secondary" onClick={() => { setDraft(Object.fromEntries(fields)); setEditing(false); }}>Cancel</button><button type="button" className="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button></>}</div>}{sections.map(section => <section className="product-detail-section" key={section.name}><h4>{section.name}</h4><div className="product-fields">{section.fields.map(([key, value]) => <div className={'product-field ' + (editing && isEditable(key) ? 'editable-field' : '')} key={key}><span>{label(key)}</span>{editing && isEditable(key) ? (typeof value === 'object' || (typeof value === 'string' && value.length > 80) ? <textarea value={editValue(key, draft[key])} onChange={e => setDraft({ ...draft, [key]: e.target.value })} /> : <input value={editValue(key, draft[key])} onChange={e => setDraft({ ...draft, [key]: e.target.value })} />) : <strong><MoneyValue field={key} value={value} /></strong>}</div>)}</div></section>)}</div></div>;
}

export function ContactCard({ contact }) {
  const [current, setCurrent] = useState(contact); const [showDetails, setShowDetails] = useState(false);
  useEffect(() => setCurrent(contact), [contact]);
  if (!current) return null;
  const fields = Object.entries(current).filter(([, value]) => value !== null && value !== undefined && value !== '' && !(Array.isArray(value) && value.length === 0) && !String(value).startsWith('__'));
  const name = current.Full_Name || current.contact_name || current.customer_name || [current.First_Name, current.Last_Name].filter(Boolean).join(' ') || [current.first_name, current.last_name].filter(Boolean).join(' ') || 'Contact';
  const email = current.Email || current.email; const phone = current.Mobile || current.mobile || current.Phone || current.phone; const title = current.Title || current.title;
  const source = current.__source === 'books' ? 'books' : 'bigin';
  const sourceLabel = source === 'books' ? 'Zoho Books contact' : 'Zoho Bigin contact';
  const id = current.id || current.contact_id;
  const save = async values => { const path = `/${source}/contacts/${id}`; const result = await call(path, { method: 'PUT', body: JSON.stringify(values) }); setCurrent(old => ({ ...old, ...(result.contact?.contact || result.contact || {}) })); };
  const remove = async () => { if (!window.confirm(`Delete ${name} from ${source === 'books' ? 'Zoho Books' : 'Zoho Bigin'}?`)) return; await call(`/${source}/contacts/${id}`, { method: 'DELETE' }); setCurrent(null); setShowDetails(false); };
  return <article className="product-card"><div className="product-card-top"><span className="active-label">{sourceLabel}</span><span className="product-sku">{id || ''}</span></div><h3>{name}</h3><div className="product-summary contact-summary">{email && <span><b>Email</b>{email}</span>}{phone && <span><b>Phone</b>{phone}</span>}{title && <span><b>Title</b>{title}</span>}{!email && !phone && !title && <span>Contact record</span>}</div><div className="product-card-actions"><button type="button" className="secondary" onClick={() => setShowDetails(true)}>Show more</button><button type="button" className="secondary" onClick={remove}>Delete</button></div>{showDetails && <DetailModal eyebrow={sourceLabel.toUpperCase()} title={name} fields={fields} editableKeys={['First_Name', 'Last_Name', 'Email', 'Mobile', 'Phone', 'Title', 'Description', 'email', 'mobile', 'phone']} onSave={save} onClose={() => setShowDetails(false)} />}</article>;
}

export function RecordCard({ record, type }) {
  const [current, setCurrent] = useState(record); const [mailing, setMailing] = useState(false); const [mailStatus, setMailStatus] = useState(''); const [showDetails, setShowDetails] = useState(false);
  useEffect(() => setCurrent(record), [record]);
  const title = current.estimate_number || current.salesorder_number || current.purchaseorder_number || current.invoice_number || current.reference_number || current.customer_name || current.customer || current.vendor_name || 'Record';
  const fields = Object.entries(current).filter(([key, value]) => value !== null && value !== undefined && value !== '' && !key.startsWith('$') && !['tags', 'custom_fields'].includes(key));
  const id = current.estimate_id || current.invoice_id || current.purchaseorder_id || current.salesorder_id;
  const updatePath = type === 'Quote' ? '/quotes/' + id : type === 'Invoice' ? '/invoices/' + id : type === 'Purchase order' ? '/purchase-orders/' + id : '/orders/' + id;
  const emailPath = type === 'Quote' ? '/quotes/' + id + '/email' : type === 'Invoice' ? '/invoices/' + id + '/email' : type === 'Purchase order' ? '/purchase-orders/' + id + '/email' : '/orders/' + id + '/email';
  const send = async () => { if (!id || mailing) return; setMailing(true); setMailStatus(''); try { await call(emailPath, { method: 'POST' }); setMailStatus('Email sent'); } catch (e) { setMailStatus(e.message || 'Unable to send email'); } finally { setMailing(false); } };
  const save = async values => { const result = await call(updatePath, { method: 'PUT', body: JSON.stringify(values) }); setCurrent(old => ({ ...old, ...(result.quote || result.order || result.purchase_order || result.invoice || {}) })); };
  const editable = ['reference_number', 'date', 'due_date', 'expiry_date', 'delivery_date', 'expected_delivery_date', 'notes', 'terms', 'discount', 'shipping_charge', 'adjustment', 'adjustment_description', 'line_items'];
  return <article className="product-card"><div className="product-card-top"><span className="active-label">{type}</span><span className="product-sku">{id || ''}</span></div><h3>{title}</h3><div className="product-summary"><span>{current.customer_name || current.vendor_name || current.company_name || current.date || 'Zoho Books record'}</span>{current.total != null && <strong>{current.currency_symbol || '₹'}{current.total}</strong>}</div><div className="product-card-actions"><button type="button" className="secondary" onClick={() => setShowDetails(true)}>Show more</button>{id && <button type="button" className="secondary" onClick={send} disabled={mailing}>{mailing ? 'Sending…' : 'Email'}</button>}{mailStatus && <span className={mailStatus === 'Email sent' ? 'mail-success' : 'mail-error'}>{mailStatus}</span>}</div>{showDetails && <DetailModal eyebrow={'ZOHO BOOKS ' + type.toUpperCase()} title={title} fields={fields} editableKeys={editable} onSave={save} onClose={() => setShowDetails(false)} action={id && <button type="button" className="primary" onClick={send} disabled={mailing}>{mailing ? 'Sending…' : 'Send by email'}</button>} />}</article>;
}

export function LookupField({ value, onChange, onSearch, options, selected, placeholder, renderOption, required = false }) {
  return <div className="lookup-field"><div className="lookup-input-row"><input required={required} placeholder={placeholder} value={value} onChange={event => onChange(event.target.value)} /><button type="button" className="lookup-search-button" onClick={onSearch} disabled={!value.trim()}>Search</button></div>{options.length > 0 && !selected && <div className="lookup-options">{options.map(option => <div className="lookup-option-entry" key={option.contact_id || option.item_id || option.id}>{renderOption(option)}{option.__source && <small>{option.__source === 'bigin' ? 'Zoho Bigin contact' : 'Zoho Books contact'}</small>}</div>)}</div>}</div>;
}

export function LineItemsEditor({ lines, setLines, items, lookupItems, currency = 'INR' }) {
  const update = (index, key, value) => setLines(current => current.map((line, i) => i === index ? { ...line, [key]: value } : line));
  const add = () => setLines(current => [...current, { item_id: '', item_name: '', quantity: 1, rate: '', description: '', discount: 0, stock_on_hand: null, available_stock: null }]);
  const remove = index => setLines(current => current.length === 1 ? current : current.filter((_, i) => i !== index));
  return <section className="line-items-editor"><div className="form-section-heading"><h4>Products and quantities</h4><span>Add each product required for this document.</span></div>{lines.map((line, index) => <div className="line-item" key={index}><div className="line-item-heading"><strong>Product {index + 1}</strong>{lines.length > 1 && <button type="button" className="link-button" onClick={() => remove(index)}>Remove</button>}</div><LookupField required placeholder="Search product name, SKU, or description" value={line.item_name} selected={line.item_id} options={items} onChange={value => { update(index, 'item_name', value); update(index, 'item_id', ''); }} onSearch={() => lookupItems(line.item_name)} renderOption={item => <button type="button" key={item.item_id} onClick={() => { update(index, 'item_id', item.item_id); update(index, 'item_name', item.name || item.item_name || ''); update(index, 'rate', line.rate || item.rate || item.purchase_rate || ''); update(index, 'stock_on_hand', item.stock_on_hand); update(index, 'available_stock', item.available_stock); }}>{item.name || item.item_name}{item.stock_on_hand != null ? ` - Stock: ${item.stock_on_hand}` : ''}</button>} /><div className="line-item-meta"><input required type="number" min="1" placeholder="Quantity required" value={line.quantity} onChange={event => update(index, 'quantity', event.target.value)} /><input required type="number" min="0" step="0.01" placeholder={`Rate (${currency})`} value={line.rate} onChange={event => update(index, 'rate', event.target.value)} /><input type="number" min="0" step="0.01" placeholder="Discount" value={line.discount} onChange={event => update(index, 'discount', event.target.value)} /></div><div className="inventory-hint">{line.item_id ? `Inventory available: ${line.available_stock ?? line.stock_on_hand ?? 'Not provided'}` : 'Search and select a product to view inventory.'}</div><input placeholder="Line description (optional)" value={line.description} onChange={event => update(index, 'description', event.target.value)} /></div>)}<button type="button" className="add-line-button" onClick={add}>+ Add another product</button></section>;
}

