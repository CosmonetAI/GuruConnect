import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { ContactCard } from '../components/Records';

const emptyAddress = () => ({ attention: '', address: '', street2: '', city: '', state: '', zip: '', country: '', phone: '', fax: '' });
const emptyForm = () => ({
  contact_name: '', company_name: '', contact_type: 'customer', customer_sub_type: 'business',
  contact_number: '', salutation: '', first_name: '', last_name: '', website: '', language_code: 'en',
  email: '', phone: '', mobile: '', title: '', department: '', payment_terms: '', payment_terms_label: '',
  credit_limit: '', is_portal_enabled: false, payment_reminder_enabled: true, pan_no: '', gst_no: '',
  notes: 'Contact created through Cosmonet AI', contact_persons_json: '[]', custom_fields_json: '[]', tags_json: '[]', billing_address: emptyAddress(), shipping_address: emptyAddress(),
});

export function ContactsPage({ setError }) {
  const [query, setQuery] = useState('\uFEFF');
  const [contacts, setContacts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState(false);
  const [form, setForm] = useState(emptyForm);
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

  const create = async event => {
    event.preventDefault(); setBusy(true); setError(''); setCreated(false);
    try {
      let contactPersons = []; let customFields = []; let tags = [];
      try { contactPersons = JSON.parse(form.contact_persons_json || '[]'); customFields = JSON.parse(form.custom_fields_json || '[]'); tags = JSON.parse(form.tags_json || '[]'); } catch { throw Error('Additional contact data must be valid JSON arrays.'); }
      const body = { ...form, payment_terms: form.payment_terms === '' ? undefined : Number(form.payment_terms), credit_limit: form.credit_limit === '' ? undefined : Number(form.credit_limit), contact_persons: contactPersons, custom_fields: customFields, tags };
      await call('/books/contacts', { method: 'POST', body: JSON.stringify(body) });
      setCreated(true); setShowCreate(false); setForm(emptyForm());
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const setField = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const setAddress = (kind, key, value) => setForm(current => ({ ...current, [kind]: { ...current[kind], [key]: value } }));
  const addressFields = (kind, label) => <div><div className="form-section-heading"><h4>{label}</h4><span>Optional address information</span></div><div className="form-two">{['attention', 'address', 'street2', 'city', 'state', 'zip', 'country', 'phone', 'fax'].map(key => <input key={key} placeholder={key.replace(/_/g, ' ')} value={form[kind][key]} onChange={e => setAddress(kind, key, e.target.value)} />)}</div></div>;

  return <section className="products-page">
    <div className="products-heading"><div><small>LIVE ZOHO CONTACTS</small><h2>Contacts</h2><p>Zoho Bigin and Zoho Books contacts are shown separately and each action uses its own provider API.</p></div><button className="primary" onClick={() => { setShowCreate(true); setCreated(false); }}>Create Books contact</button></div>
    {created && <div className="success">Contact created successfully in Zoho Books.</div>}
    <form className="product-search" onSubmit={search}><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by phone, email, or name…" /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></form>
    {query && !busy && <p className="product-count">{contacts.length} {contacts.length === 1 ? 'contact' : 'contacts'} found</p>}
    {!query ? <div className="product-empty">Search Zoho Bigin and Zoho Books contacts by phone number, email, or name.</div> : !busy && !contacts.length ? <div className="product-empty">{query.trim() ? `No contacts matched “${query.trim()}”.` : 'No recent contacts found.'}</div> : <div className="contacts-grid">{contacts.map(contact => <ContactCard contact={contact} key={`${contact.__source || 'unknown'}-${contact.id || contact.contact_id}`} />)}</div>}
    {showCreate && <div className="modal-backdrop" onClick={() => setShowCreate(false)}><form className="contact-modal" onSubmit={create} onClick={e => e.stopPropagation()}>
      <div className="modal-heading"><h3>Create Books contact</h3><button type="button" onClick={() => setShowCreate(false)}>×</button></div><p>Creates the record only in Zoho Books.</p>
      <div className="form-section-heading"><h4>Contact identity</h4><span>Core customer details</span></div>
      <div className="form-two">{[['contact_name','Contact name'],['company_name','Company name'],['contact_number','Contact number'],['salutation','Salutation'],['first_name','First name'],['last_name','Last name'],['website','Website'],['language_code','Language code'],['email','Email'],['phone','Phone'],['mobile','Mobile'],['title','Designation'],['department','Department'],['payment_terms','Payment terms (days)'],['payment_terms_label','Payment terms label'],['credit_limit','Credit limit'],['pan_no','PAN number'],['gst_no','GST number']].map(([key, placeholder]) => <input key={key} required={key === 'first_name' || key === 'mobile'} type={key === 'email' ? 'email' : key === 'credit_limit' || key === 'payment_terms' ? 'number' : 'text'} placeholder={placeholder} value={form[key] || ''} onChange={e => setField(key, key === 'mobile' ? e.target.value.replace(/\D/g, '').slice(0, 10) : e.target.value)} />)}</div>
      <label className="checkbox-row"><input type="checkbox" checked={form.is_portal_enabled} onChange={e => setField('is_portal_enabled', e.target.checked)} /> Enable portal</label>
      <label className="checkbox-row"><input type="checkbox" checked={form.payment_reminder_enabled} onChange={e => setField('payment_reminder_enabled', e.target.checked)} /> Enable payment reminders</label>
      {addressFields('billing_address', 'Billing address')}{addressFields('shipping_address', 'Shipping address')}
      <textarea placeholder="Notes" value={form.notes} onChange={e => setField('notes', e.target.value)} /><textarea placeholder="Additional contact persons (JSON array)" value={form.contact_persons_json} onChange={e => setField('contact_persons_json', e.target.value)} /><textarea placeholder="Custom fields (JSON array)" value={form.custom_fields_json} onChange={e => setField('custom_fields_json', e.target.value)} /><textarea placeholder="Tags (JSON array)" value={form.tags_json} onChange={e => setField('tags_json', e.target.value)} />
      <div className="modal-actions"><button type="button" onClick={() => setShowCreate(false)}>Cancel</button><button className="primary" disabled={busy}>Create Books contact</button></div>
    </form></div>}
  </section>;
}
