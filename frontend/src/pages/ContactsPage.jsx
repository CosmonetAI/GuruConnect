import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { ContactCard } from '../components/Records';

const emptyAddress = () => ({ attention: '', address: '', street2: '', city: '', state: '', zip: '', country: '', phone: '', fax: '' });
const emptyContactPerson = () => ({ first_name: '', last_name: '', email: '', mobile: '', designation: '', is_primary_contact: false });
const emptyCustomField = () => ({ index: '', value: '' });
const emptyTag = () => ({ tag_id: '', tag_option_id: '' });
const emptyForm = () => ({
  contact_name: '', company_name: '', contact_type: 'customer', customer_sub_type: 'business',
  contact_number: '', salutation: '', first_name: '', last_name: '', website: '', language_code: 'en',
  email: '', phone: '', mobile: '', title: '', department: '', payment_terms: '', payment_terms_label: '',
  credit_limit: '', is_portal_enabled: false, payment_reminder_enabled: true, pan_no: '', gst_no: '',
  notes: 'Contact created through Cosmonet AI', billing_address: emptyAddress(), shipping_address: emptyAddress(),
});

export function ContactsPage({ setError }) {
  const [query, setQuery] = useState('\uFEFF');
  const [contacts, setContacts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [contactPersons, setContactPersons] = useState([]);
  const [customFields, setCustomFields] = useState([]);
  const [tags, setTags] = useState([]);
  const [reportingTags, setReportingTags] = useState([]);
  const [tagOptions, setTagOptions] = useState({});
  const date = useZohoDateFilter();
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    if (query.trim()) return;
    let cancelled = false;
    setBusy(true);
    call(withDate('/contacts?page=1', date)).then(result => {
      if (!cancelled) { setContacts(result.contacts || []); setHasMore(Boolean(result.has_more)); setPage(1); }
    }).catch(e => !cancelled && setError(e.message)).finally(() => !cancelled && setBusy(false));
    return () => { cancelled = true; };
  }, [date]);

  const search = async event => {
    event?.preventDefault();
    if (!query.trim()) { setContacts([]); return; }
    setBusy(true); setError('');
    try {
      const result = await call(withDate(`/contacts?page=1&search=${encodeURIComponent(query.trim())}`, date));
      setContacts(result.contacts || []); setPage(1); setHasMore(Boolean(result.has_more));
    } catch (e) { setError(e.message); setContacts([]); } finally { setBusy(false); }
  };
  const showMore = async () => {
    if (!hasMore || busy) return;
    setBusy(true);
    try {
      const result = await call(withDate(`/contacts?page=${page + 1}&search=${encodeURIComponent(query.trim())}`, date));
      setContacts(current => [...current, ...(result.contacts || [])]); setPage(page + 1); setHasMore(Boolean(result.has_more));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  useGlobalPager(showMore, hasMore, busy);

  useEffect(() => {
    if (!showCreate) return undefined;
    call('/documents/options').then(result => setReportingTags(result.reporting_tags || [])).catch(e => setError(e.message));
    return undefined;
  }, [showCreate, setError]);

  const loadTagOptions = async tagId => {
    if (!tagId || tagOptions[tagId]) return;
    try {
      const result = await call(`/purchase-orders/options/tag-options?tag_id=${encodeURIComponent(tagId)}`);
      setTagOptions(current => ({ ...current, [tagId]: result.options || [] }));
    } catch (e) { setError(e.message); }
  };

  const create = async event => {
    event.preventDefault(); setBusy(true); setError(''); setCreated(false);
    try {
      const body = {
        ...form,
        payment_terms: form.payment_terms === '' ? undefined : Number(form.payment_terms),
        credit_limit: form.credit_limit === '' ? undefined : Number(form.credit_limit),
        contact_persons: contactPersons.filter(person => person.first_name || person.last_name || person.email || person.mobile),
        custom_fields: customFields.filter(field => field.index !== '' && field.value !== '').map(field => ({ index: Number(field.index), value: field.value })),
        tags: tags.filter(tag => tag.tag_id && tag.tag_option_id),
      };
      await call('/books/contacts', { method: 'POST', body: JSON.stringify(body) });
      setCreated(true); setShowCreate(false); setForm(emptyForm());
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const setField = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const setAddress = (kind, key, value) => setForm(current => ({ ...current, [kind]: { ...current[kind], [key]: value } }));
  const addressFields = (kind, label) => <div className="contact-subsection"><div className="form-section-heading"><div><h4>{label}</h4><p>Optional address information</p></div></div><div className="form-two contact-address-grid">{[['attention', 'Attention'], ['address', 'Address'], ['street2', 'Address line 2'], ['city', 'City'], ['state', 'State'], ['zip', 'Postal code'], ['country', 'Country'], ['phone', 'Phone'], ['fax', 'Fax']].map(([key, placeholder]) => <label className="contact-field" key={key}><span>{placeholder} <small>Optional</small></span><input placeholder={placeholder} value={form[kind][key]} onChange={e => setAddress(kind, key, e.target.value)} /></label>)}</div></div>;

  return <section className="products-page">
    <div className="products-heading"><div><small>LIVE ZOHO CONTACTS</small><h2>Contacts</h2><p>Zoho Bigin and Zoho Books contacts are shown separately and each action uses its own provider API.</p></div><button className="primary" onClick={() => { setShowCreate(true); setCreated(false); setForm(emptyForm()); setContactPersons([]); setCustomFields([]); setTags([]); setTagOptions({}); }}>Create Books contact</button></div>
    {created && <div className="success">Contact created successfully in Zoho Books.</div>}
    <form className="product-search" onSubmit={search}><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by phone, email, or name…" /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></form>
    {query && !busy && <p className="product-count">{contacts.length} {contacts.length === 1 ? 'contact' : 'contacts'} found</p>}
    {!query ? <div className="product-empty">Search Zoho Bigin and Zoho Books contacts by phone number, email, or name.</div> : !busy && !contacts.length ? <div className="product-empty">{query.trim() ? `No contacts matched “${query.trim()}”.` : 'No recent contacts found.'}</div> : <div className="contacts-grid">{contacts.map(contact => <ContactCard contact={contact} key={`${contact.__source || 'unknown'}-${contact.id || contact.contact_id}`} />)}</div>}
    {showCreate && <div className="modal-backdrop" onClick={() => setShowCreate(false)}><form className="contact-modal" onSubmit={create} onClick={e => e.stopPropagation()}>
      <div className="modal-heading"><h3>Create Books contact</h3><button type="button" onClick={() => setShowCreate(false)}>×</button></div><p>Creates the record only in Zoho Books.</p>
      <div className="form-section-heading"><div><h4>Contact identity</h4><p className="required-guidance"><strong className="required-copy">Required:</strong> first name and mobile. All other identity fields are optional.</p></div><span>Core customer details</span></div>
      <div className="form-two contact-form-grid">{[['contact_name','Contact name'],['company_name','Company name'],['contact_number','Contact number'],['salutation','Salutation'],['first_name','First name'],['last_name','Last name'],['website','Website'],['language_code','Language code'],['email','Email'],['phone','Phone'],['mobile','Mobile'],['title','Designation'],['department','Department'],['payment_terms','Payment terms (days)'],['payment_terms_label','Payment terms label'],['credit_limit','Credit limit'],['pan_no','PAN number'],['gst_no','GST number']].map(([key, label]) => <label className="contact-field" key={key}><span>{label} <small>{key === 'first_name' || key === 'mobile' ? 'Required' : 'Optional'}</small></span><input required={key === 'first_name' || key === 'mobile'} type={key === 'email' ? 'email' : key === 'credit_limit' || key === 'payment_terms' ? 'number' : 'text'} placeholder={`Enter ${label.toLowerCase()}`} value={form[key] || ''} onChange={e => setField(key, key === 'mobile' ? e.target.value.replace(/\D/g, '').slice(0, 10) : e.target.value)} /></label>)}</div>
      <label className="checkbox-row"><input type="checkbox" checked={form.is_portal_enabled} onChange={e => setField('is_portal_enabled', e.target.checked)} /> Enable portal</label>
      <label className="checkbox-row"><input type="checkbox" checked={form.payment_reminder_enabled} onChange={e => setField('payment_reminder_enabled', e.target.checked)} /> Enable payment reminders</label>
      {addressFields('billing_address', 'Billing address')}{addressFields('shipping_address', 'Shipping address')}
      <section className="contact-form-section">
        <div className="form-section-heading"><div><h4>Notes</h4><p>Optional internal note for this Books contact.</p></div><span>01</span></div>
        <label className="contact-field contact-field-wide"><span>Internal notes <small>Optional</small></span><textarea value={form.notes} onChange={e => setField('notes', e.target.value)} placeholder="Add context for your team" /></label>
      </section>
      <section className="contact-form-section">
        <div className="form-section-heading"><div><h4>Additional contact persons</h4><p>Add people who can receive communications for this customer.</p></div><span>02</span></div>
        {!contactPersons.length && <div className="contact-empty-row">No additional contact persons added yet.</div>}
        {contactPersons.map((person, index) => <div className="structured-row" key={`person-${index}`}>
          <div className="contact-form-grid">
            <label className="contact-field"><span>First name <small>Optional</small></span><input value={person.first_name} onChange={e => setContactPersons(current => current.map((row, i) => i === index ? { ...row, first_name: e.target.value } : row))} /></label>
            <label className="contact-field"><span>Last name <small>Optional</small></span><input value={person.last_name} onChange={e => setContactPersons(current => current.map((row, i) => i === index ? { ...row, last_name: e.target.value } : row))} /></label>
            <label className="contact-field"><span>Email <small>Optional</small></span><input type="email" value={person.email} onChange={e => setContactPersons(current => current.map((row, i) => i === index ? { ...row, email: e.target.value } : row))} /></label>
            <label className="contact-field"><span>Mobile <small>Optional</small></span><input value={person.mobile} onChange={e => setContactPersons(current => current.map((row, i) => i === index ? { ...row, mobile: e.target.value } : row))} /></label>
            <label className="contact-field"><span>Designation <small>Optional</small></span><input value={person.designation} onChange={e => setContactPersons(current => current.map((row, i) => i === index ? { ...row, designation: e.target.value } : row))} /></label>
          </div>
          <div className="structured-row-actions"><label className="checkbox-row"><input type="checkbox" checked={Boolean(person.is_primary_contact)} onChange={() => setContactPersons(current => current.map((row, i) => ({ ...row, is_primary_contact: i === index })))} /> Primary contact</label><button type="button" className="danger-link" onClick={() => setContactPersons(current => current.filter((_, i) => i !== index))}>Remove</button></div>
        </div>)}
        <button type="button" className="secondary-add-button" onClick={() => setContactPersons(current => [...current, emptyContactPerson()])}>+ Add contact person</button>
      </section>
      <section className="contact-form-section">
        <div className="form-section-heading"><div><h4>Custom fields</h4><p>Choose the field index and enter the value configured in Zoho Books.</p></div><span>03</span></div>
        {!customFields.length && <div className="contact-empty-row">No custom fields added.</div>}
        {customFields.map((field, index) => <div className="structured-inline-row" key={`custom-${index}`}><label className="contact-field"><span>Field index <small>Required</small></span><input type="number" min="1" value={field.index} onChange={e => setCustomFields(current => current.map((row, i) => i === index ? { ...row, index: e.target.value } : row))} /></label><label className="contact-field"><span>Value <small>Required</small></span><input value={field.value} onChange={e => setCustomFields(current => current.map((row, i) => i === index ? { ...row, value: e.target.value } : row))} /></label><button type="button" className="danger-link" onClick={() => setCustomFields(current => current.filter((_, i) => i !== index))}>Remove</button></div>)}
        <button type="button" className="secondary-add-button" onClick={() => setCustomFields(current => [...current, emptyCustomField()])}>+ Add custom field</button>
      </section>
      <section className="contact-form-section">
        <div className="form-section-heading"><div><h4>Reporting tags</h4><p>Use configured Zoho Books tags to classify this contact.</p></div><span>04</span></div>
        {!tags.length && <div className="contact-empty-row">No reporting tags added.</div>}
        {tags.map((tag, index) => <div className="structured-inline-row" key={`tag-${index}`}><label className="contact-field"><span>Tag <small>Required</small></span><select value={tag.tag_id} onChange={e => { const tagId = e.target.value; loadTagOptions(tagId); setTags(current => current.map((row, i) => i === index ? { ...row, tag_id: tagId, tag_option_id: '' } : row)); }}><option value="">Select a tag</option>{reportingTags.map(option => <option key={option.tag_id || option.id} value={option.tag_id || option.id}>{option.tag_name || option.name}</option>)}</select></label><label className="contact-field"><span>Tag option <small>Required</small></span><select value={tag.tag_option_id} disabled={!tag.tag_id} onChange={e => setTags(current => current.map((row, i) => i === index ? { ...row, tag_option_id: e.target.value } : row))}><option value="">Select an option</option>{(tagOptions[tag.tag_id] || []).map(option => <option key={option.option_id || option.id} value={option.option_id || option.id}>{option.option_name || option.name}</option>)}</select></label><button type="button" className="danger-link" onClick={() => setTags(current => current.filter((_, i) => i !== index))}>Remove</button></div>)}
        <button type="button" className="secondary-add-button" onClick={() => setTags(current => [...current, emptyTag()])}>+ Add reporting tag</button>
      </section>
      <div className="modal-actions"><button type="button" onClick={() => setShowCreate(false)}>Cancel</button><button className="primary" disabled={busy}>Create Books contact</button></div>
    </form></div>}
  </section>;
}
