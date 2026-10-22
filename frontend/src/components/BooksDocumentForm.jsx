import React, { useEffect, useState } from 'react';
import { call } from '../shared/api';
import DocumentLines, { newDocumentLine } from './DocumentLines';
import { LookupField } from './Records';
import '../styles/document-forms.css';

const isPurchaseOrder = type => type === 'purchase-order';
const defaults = type => ({
  party_id: '', party_name: '', number: '', date: new Date().toISOString().slice(0, 10),
  expiry_date: new Date(Date.now() + 9 * 86400000).toISOString().slice(0, 10), shipment_date: '',
  delivery_date: '', due_date: new Date(Date.now() + 9 * 86400000).toISOString().slice(0, 10),
  reference_number: '', currency_id: '', exchange_rate: '1', contact_person_id: '',
  billing_address_id: '', shipping_address_id: '', place_of_supply: '', gst_treatment: '', gst_no: '',
  tax_treatment: '', is_reverse_charge_applied: false, salesperson_name: '', location_id: '',
  discount: '', discount_type: 'entity_level', discount_account_id: '', is_discount_before_tax: true,
  is_inclusive_tax: false, shipping_charge: '0', adjustment: '0', adjustment_description: '',
  delivery_method: '', ship_via: '', attention: '', payment_terms: '', payment_terms_label: '',
  payment_expected_date: '', custom_field_value: '', is_email_enabled: true, is_sms_enabled: false,
  is_whatsapp_enabled: false,
  notes: isPurchaseOrder(type) ? 'Purchase order created through Cosmonet AI' : type === 'quote' ? 'Quotation generated via Cosmonet AI' : type === 'sales-order' ? 'Sales order created through AI integration' : 'Invoice generated through AI integration',
  terms: isPurchaseOrder(type) ? 'Delivery and payment terms as agreed.' : type === 'quote' ? 'Please confirm acceptance before the expiry date.' : type === 'sales-order' ? 'Delivery and payment terms as agreed.' : 'Payment due by the specified due date.'
});

const display = value => value?.name || value?.location_name || value?.project_name || value?.tax_name || value?.tag_name || value?.account_name || value?.currency_name_formatted || value?.id || 'Unnamed';
function Select({ value, onChange, children, required = false }) { return <select value={value ?? ''} onChange={event => onChange(event.target.value)} required={required}>{children}</select>; }
function Field({ label, required = false, children, hint }) { return <label className="document-field"><span>{label} <small className={required ? 'field-required' : 'field-optional'}>{required ? 'Required' : 'Optional'}</small></span>{children}{hint && <em>{hint}</em>}</label>; }
function Section({ title, description, children }) { const required = /required|necessary|reveals/i.test(description || ''); return <section className="document-form-section"><div className="form-section-heading"><div><h4>{title}</h4><span className={`section-guidance ${required ? 'required-guidance' : 'optional-guidance'}`}>{description}</span></div><span className="section-marker">{required ? 'Required' : 'Optional'}</span></div><div className="document-form-grid">{children}</div></section>; }

export default function BooksDocumentForm({ type, setError, onCreated }) {
  const purchase = isPurchaseOrder(type);
  const [form, setForm] = useState(() => defaults(type));
  const [parties, setParties] = useState([]);
  const [items, setItems] = useState([]);
  const [lines, setLines] = useState([newDocumentLine()]);
  const [partyDetail, setPartyDetail] = useState({ contact_persons: [] });
  const [options, setOptions] = useState({ currencies: [], locations: [], accounts: [], projects: [], reporting_tags: [], taxes: [] });
  const [baseCurrencyId, setBaseCurrencyId] = useState('');
  const [busy, setBusy] = useState(false);
  const partyLabel = purchase ? 'Vendor' : 'Customer';
  const setField = (key, value) => setForm(current => ({ ...current, [key]: value }));

  const lookupParties = async value => {
    setField('party_name', value); setField('party_id', ''); setParties([]);
    if (!value.trim()) return;
    try {
      const result = await call(`/document-contacts?role=${purchase ? 'vendor' : 'customer'}&search=${encodeURIComponent(value.trim())}`);
      setParties(result.contacts || []);
    } catch (error) { setError(error.message); }
  };

  const selectParty = async party => {
    const id = party.contact_id;
    setForm(current => ({ ...current, party_id: id, party_name: party.contact_name || party.customer_name || party.vendor_name || '' }));
    setParties([]);
    try {
      const result = await call(`${purchase ? '/purchase-orders/vendors/' : '/documents/contacts/'}${id}`);
      const detail = result.contact || result.vendor || {};
      const persons = result.contact_persons?.length
        ? result.contact_persons
        : detail.contact_persons || detail.contactpersons || result.contacts || result.contactpersons || [];
      const primary = persons.find(person => person.is_primary_contact) || persons[0];
      setPartyDetail({ ...detail, contact_persons: persons });
      setForm(current => ({ ...current, currency_id: detail.currency_id || current.currency_id, contact_person_id: primary?.contact_person_id || '', billing_address_id: detail.billing_address?.address_id || '', shipping_address_id: detail.shipping_address?.address_id || '', attention: detail.billing_address?.attention || [primary?.first_name, primary?.last_name].filter(Boolean).join(' ') || current.attention, gst_treatment: detail.gst_treatment || current.gst_treatment, gst_no: detail.gst_no || '', is_email_enabled: primary?.communication_preference?.is_email_enabled ?? true, is_sms_enabled: primary?.communication_preference?.is_sms_enabled ?? false, is_whatsapp_enabled: primary?.communication_preference?.is_whatsapp_enabled ?? false }));
    } catch (error) { setError(error.message); }
  };

  const lookupItems = async value => {
    try { const result = await call(value.trim() ? `/products?search=${encodeURIComponent(value.trim())}` : '/products?page=1'); setItems(result.items || []); }
    catch (error) { setError(error.message); }
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([call('/documents/options'), lookupItems('')]).then(([result]) => {
      if (cancelled) return;
      setOptions(current => ({ ...current, ...result }));
      const base = (result.currencies || []).find(currency => currency.is_base_currency);
      if (base) { setBaseCurrencyId(base.currency_id); setForm(current => ({ ...current, currency_id: current.currency_id || base.currency_id })); }
    }).catch(error => !cancelled && setError(error.message));
    return () => { cancelled = true; };
  }, []);

  const create = async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const contactPersons = form.contact_person_id ? [{ contact_person_id: form.contact_person_id, communication_preference: { is_email_enabled: form.is_email_enabled, is_sms_enabled: form.is_sms_enabled, is_whatsapp_enabled: form.is_whatsapp_enabled } }] : [];
      const lineItems = lines.map((line, index) => ({ item_id: line.item_id, name: line.item_name, description: line.description, quantity: Number(line.quantity), rate: Number(line.rate), unit: line.unit, item_order: index, discount: form.discount_type === 'item_level' ? line.discount : undefined, account_id: purchase ? line.account_id || undefined : undefined, hsn_or_sac: line.hsn_or_sac || undefined, tax_id: line.tax_id || undefined, location_id: line.location_id || undefined, project_id: line.project_id || undefined, tags: line.tag_id && line.tag_option_id ? [{ tag_id: line.tag_id, tag_option_id: line.tag_option_id }] : [], item_custom_fields: line.custom_value ? [{ index: 1, value: line.custom_value }] : [], line_item_category: 'line_item' }));
      const payload = { [purchase ? 'vendor_id' : 'customer_id']: form.party_id, currency_id: form.currency_id, exchange_rate: Number(form.exchange_rate || 1), contact_persons_associated: contactPersons, [purchase ? 'purchaseorder_number' : type === 'quote' ? 'estimate_number' : type === 'sales-order' ? 'salesorder_number' : 'invoice_number']: form.number || undefined, date: form.date, ...(purchase ? { delivery_date: form.delivery_date || undefined } : type === 'quote' ? { expiry_date: form.expiry_date } : type === 'sales-order' ? { shipment_date: form.shipment_date || undefined } : { due_date: form.due_date, payment_terms: form.payment_terms === '' ? undefined : Number(form.payment_terms), payment_terms_label: form.payment_terms_label || undefined, payment_expected_date: form.payment_expected_date || undefined }), reference_number: form.reference_number || undefined, billing_address_id: form.billing_address_id || undefined, shipping_address_id: form.shipping_address_id || undefined, attention: form.attention || undefined, ship_via: form.ship_via || undefined, place_of_supply: form.place_of_supply || undefined, gst_treatment: form.gst_treatment || undefined, gst_no: form.gst_no || undefined, tax_treatment: form.tax_treatment || undefined, is_reverse_charge_applied: form.is_reverse_charge_applied, salesperson_name: form.salesperson_name || undefined, location_id: form.location_id || undefined, discount: form.discount === '' ? undefined : form.discount, discount_type: form.discount_type, discount_account_id: purchase ? form.discount_account_id || undefined : undefined, is_discount_before_tax: form.is_discount_before_tax, is_inclusive_tax: form.is_inclusive_tax, shipping_charge: Number(form.shipping_charge || 0), adjustment: Number(form.adjustment || 0), adjustment_description: form.adjustment_description || undefined, delivery_method: form.delivery_method || undefined, line_items: lineItems, custom_fields: form.custom_field_value ? [{ index: 1, value: form.custom_field_value }] : [], notes: form.notes, terms: form.terms };
      await call(purchase ? '/purchase-orders' : type === 'quote' ? '/quotes' : type === 'sales-order' ? '/orders' : '/invoices', { method: 'POST', body: JSON.stringify(payload) });
      onCreated(form.reference_number || form.number || null);
    } catch (error) { setError(error.message); } finally { setBusy(false); }
  };

  const foreignCurrency = Boolean(form.currency_id && baseCurrencyId && form.currency_id !== baseCurrencyId);
  const needsAdjustmentDescription = Number(form.adjustment || 0) !== 0;
  const ready = Boolean(form.party_id && form.currency_id && form.date && (!purchase || form.delivery_date === '' || form.delivery_date >= form.date) && (type !== 'quote' || form.expiry_date) && (type !== 'invoice' || form.due_date) && (!foreignCurrency || Number(form.exchange_rate) > 0) && (!purchase || !form.discount || form.discount_account_id) && (!form.gst_treatment || form.gst_treatment !== 'business_gst' || form.gst_no.trim()) && (!needsAdjustmentDescription || form.adjustment_description.trim()) && (!form.payment_terms || form.payment_terms_label.trim()) && lines.length > 0 && lines.every(line => line.item_id && Number(line.quantity) > 0 && line.rate !== '' && (!purchase || line.account_id) && (!line.tag_id || line.tag_option_id) && (form.discount_type !== 'item_level' || line.discount !== '')));
  const title = purchase ? 'Create purchase order' : type === 'quote' ? 'Create quote' : type === 'sales-order' ? 'Create sales order' : 'Create invoice';
  const numberLabel = purchase ? 'Purchase order number' : type === 'quote' ? 'Quote number' : type === 'sales-order' ? 'Sales order number' : 'Invoice number';

  return <form className="contact-modal quote-modal document-form" onSubmit={create} onClick={event => event.stopPropagation()}>
    <div className="modal-heading"><div><small>COMMERCIAL WORKSPACE</small><h3>{title}</h3></div><button type="button" onClick={() => onCreated(null)}>×</button></div>
    <Section title={partyLabel} description={`${partyLabel} and at least one product line are required. Select the correct record before continuing.`}>
      <Field label={partyLabel} required><LookupField required placeholder={`Search ${partyLabel.toLowerCase()} name or email`} value={form.party_name} selected={form.party_id} options={parties} onChange={lookupParties} onSearch={lookupParties} renderOption={party => <button type="button" key={party.contact_id} onClick={() => void selectParty(party)}>{party.contact_name || party.customer_name || party.vendor_name} {party.email ? `— ${party.email}` : ''}</button>} /></Field>
      <Field label="Contact person"><Select value={form.contact_person_id} onChange={value => setField('contact_person_id', value)}><option value="">Select a contact person</option>{(partyDetail.contact_persons || []).map(person => <option key={person.contact_person_id} value={person.contact_person_id}>{[person.first_name, person.last_name].filter(Boolean).join(' ') || person.contact_person_id}</option>)}</Select></Field>
      {form.contact_person_id && <div className="document-checkboxes"><span>Communication preferences <small>Optional</small></span><label><input type="checkbox" checked={form.is_email_enabled} onChange={event => setField('is_email_enabled', event.target.checked)} /> Email</label><label><input type="checkbox" checked={form.is_sms_enabled} onChange={event => setField('is_sms_enabled', event.target.checked)} /> SMS</label><label><input type="checkbox" checked={form.is_whatsapp_enabled} onChange={event => setField('is_whatsapp_enabled', event.target.checked)} /> WhatsApp</label></div>}
    </Section>
    <Section title="Document details" description="Required dates are marked. Leave the document number blank when automatic numbering is enabled.">
      <Field label={numberLabel} hint="Optional when automatic numbering is enabled."><input value={form.number} onChange={event => setField('number', event.target.value)} placeholder="Generated automatically if blank" /></Field>
      <Field label="Reference number"><input value={form.reference_number} onChange={event => setField('reference_number', event.target.value)} /></Field>
      <Field label="Document date" required><input type="date" value={form.date} onChange={event => setField('date', event.target.value)} required /></Field>
      {purchase && <Field label="Delivery date"><input type="date" min={form.date} value={form.delivery_date} onChange={event => setField('delivery_date', event.target.value)} /></Field>}
      {type === 'quote' && <Field label="Expiry date" required><input type="date" min={form.date} value={form.expiry_date} onChange={event => setField('expiry_date', event.target.value)} required /></Field>}
      {type === 'sales-order' && <Field label="Shipment date"><input type="date" min={form.date} value={form.shipment_date} onChange={event => setField('shipment_date', event.target.value)} /></Field>}
      {type === 'invoice' && <Field label="Due date" required><input type="date" min={form.date} value={form.due_date} onChange={event => setField('due_date', event.target.value)} required /></Field>}
      <Field label="Currency" required><Select value={form.currency_id} onChange={value => setField('currency_id', value)} required><option value="">Select currency</option>{options.currencies.map(currency => <option key={currency.currency_id} value={currency.currency_id}>{currency.currency_code} — {currency.currency_name}</option>)}</Select></Field>
      <Field label="Exchange rate" required={foreignCurrency} hint={foreignCurrency ? 'Required because the selected currency differs from the organization base currency.' : 'Optional for the organization base currency.'}><input type="number" min="0.000001" step="0.000001" value={form.exchange_rate} onChange={event => setField('exchange_rate', event.target.value)} required={foreignCurrency} /></Field>
    </Section>
    <Section title="Addresses and tax" description="Address, GST, and tax fields are optional until a selected tax treatment makes a related value necessary.">
      <Field label="Billing address"><Select value={form.billing_address_id} onChange={value => setField('billing_address_id', value)}><option value="">Use {partyLabel.toLowerCase()} default</option>{partyDetail.billing_address?.address_id && <option value={partyDetail.billing_address.address_id}>{partyDetail.billing_address.city || 'Billing'} ({partyDetail.billing_address.address_id})</option>}</Select></Field>
      <Field label="Shipping address"><Select value={form.shipping_address_id} onChange={value => setField('shipping_address_id', value)}><option value="">Use {partyLabel.toLowerCase()} default</option>{partyDetail.shipping_address?.address_id && <option value={partyDetail.shipping_address.address_id}>{partyDetail.shipping_address.city || 'Shipping'} ({partyDetail.shipping_address.address_id})</option>}</Select></Field>
      <Field label="Attention"><input value={form.attention} onChange={event => setField('attention', event.target.value)} /></Field>
      <Field label="Location"><Select value={form.location_id} onChange={value => setField('location_id', value)}><option value="">No location</option>{options.locations.map(location => <option key={location.location_id} value={location.location_id}>{display(location)}</option>)}</Select></Field>
      <Field label="GST treatment"><Select value={form.gst_treatment} onChange={value => setField('gst_treatment', value)}><option value="">Not specified</option><option value="business_gst">Business GST</option><option value="business_none">Business non-GST</option><option value="overseas">Overseas</option><option value="consumer">Consumer</option></Select></Field>
      {form.gst_treatment === 'business_gst' && <Field label="GST number" required><input value={form.gst_no} onChange={event => setField('gst_no', event.target.value)} required /></Field>}
      <Field label="Tax treatment"><Select value={form.tax_treatment} onChange={value => setField('tax_treatment', value)}><option value="">Not specified</option><option value="vat_registered">VAT registered</option><option value="vat_not_registered">VAT not registered</option><option value="non_gcc">Non-GCC</option></Select></Field>
      {form.tax_treatment && <label className="document-checkbox"><input type="checkbox" checked={form.is_reverse_charge_applied} onChange={event => setField('is_reverse_charge_applied', event.target.checked)} /> Reverse charge applied <small>Optional</small></label>}
    </Section>
    <Section title="Pricing and delivery" description="Selecting a discount, adjustment, payment term, or foreign currency reveals the fields required to complete it.">
      <Field label="Discount"><input value={form.discount} onChange={event => setField('discount', event.target.value)} placeholder="Amount or percentage, e.g. 10%" /></Field>
      {purchase && <Field label="Discount account" required={Boolean(form.discount)}><Select value={form.discount_account_id} onChange={value => setField('discount_account_id', value)} required={Boolean(form.discount)}><option value="">Select discount account</option>{options.accounts.map(account => <option key={account.account_id} value={account.account_id}>{display(account)}</option>)}</Select></Field>}
      <Field label="Discount type"><Select value={form.discount_type} onChange={value => setField('discount_type', value)}><option value="entity_level">Document-level discount</option><option value="item_level">Line-level discount</option></Select></Field>
      <label className="document-checkbox"><input type="checkbox" checked={form.is_discount_before_tax} onChange={event => setField('is_discount_before_tax', event.target.checked)} /> Discount before tax <small>Optional</small></label>
      <label className="document-checkbox"><input type="checkbox" checked={form.is_inclusive_tax} onChange={event => setField('is_inclusive_tax', event.target.checked)} /> Rates include tax <small>Optional</small></label>
      <Field label="Shipping charge"><input type="number" min="0" step="0.01" value={form.shipping_charge} onChange={event => setField('shipping_charge', event.target.value)} /></Field>
      <Field label="Adjustment"><input type="number" step="0.01" value={form.adjustment} onChange={event => setField('adjustment', event.target.value)} /></Field>
      {needsAdjustmentDescription && <Field label="Adjustment description" required><input value={form.adjustment_description} onChange={event => setField('adjustment_description', event.target.value)} required /></Field>}
      {purchase && <Field label="Ship via"><input value={form.ship_via} onChange={event => setField('ship_via', event.target.value)} /></Field>}
      {type === 'sales-order' && <Field label="Delivery method"><input value={form.delivery_method} onChange={event => setField('delivery_method', event.target.value)} /></Field>}
      {type === 'invoice' && <><Field label="Payment terms (days)" hint="Payment terms can be set up to 100 days."><input type="number" min="0" max="100" value={form.payment_terms} onChange={event => setField('payment_terms', event.target.value)} /></Field>{form.payment_terms !== '' && <Field label="Payment terms label" required><input value={form.payment_terms_label} onChange={event => setField('payment_terms_label', event.target.value)} required /></Field>}<Field label="Payment expected date"><input type="date" value={form.payment_expected_date} onChange={event => setField('payment_expected_date', event.target.value)} /></Field></>}
    </Section>
    <DocumentLines lines={lines} setLines={setLines} items={items} lookupItems={lookupItems} options={options} includeAccount={purchase} purchaseMode={purchase} itemDiscounts={form.discount_type === 'item_level'} />
    <Section title="Additional details" description="Add the context your customer, supplier, or internal team needs. All fields in this section are optional.">
      <Field label="Salesperson name"><input value={form.salesperson_name} onChange={event => setField('salesperson_name', event.target.value)} /></Field>
      <Field label="Custom field value"><input value={form.custom_field_value} onChange={event => setField('custom_field_value', event.target.value)} /></Field>
      <Field label="Notes"><textarea value={form.notes} onChange={event => setField('notes', event.target.value)} /></Field>
      <Field label="Terms"><textarea value={form.terms} onChange={event => setField('terms', event.target.value)} /></Field>
    </Section>
    <div className="modal-actions"><button type="button" onClick={() => onCreated(null)}>Cancel</button><button className="primary" disabled={!ready || busy}>{busy ? 'Generating…' : `Generate ${purchase ? 'purchase order' : type === 'quote' ? 'quote' : type === 'sales-order' ? 'sales order' : 'invoice'}`}</button></div>
  </form>;
}
