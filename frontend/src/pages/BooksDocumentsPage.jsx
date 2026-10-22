import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { RecordCard } from '../components/Records';
import BooksDocumentForm from '../components/BooksDocumentForm';

const config = {
  quote: { path: '/quotes', key: 'quotes', type: 'Quote', title: 'Quotes', description: 'Search estimates and create customer quotations directly in Zoho Books.', empty: 'No recent quotes found.' },
  'sales-order': { path: '/orders', key: 'orders', type: 'Sales order', title: 'Sales Orders', description: 'Search and create sales orders for customers directly in Zoho Books.', empty: 'No recent sales orders found.' },
  invoice: { path: '/invoices', key: 'invoices', type: 'Invoice', title: 'Invoices', description: 'Search and create invoices for customers directly in Zoho Books.', empty: 'No recent invoices found.' },
  'purchase-order': { path: '/purchase-orders', key: 'purchase_orders', type: 'Purchase order', title: 'Purchase Orders', description: 'Search and create purchase orders for vendors directly in Zoho Books.', empty: 'No recent purchase orders found.' }
};

export default function BooksDocumentsPage({ type, setError }) {
  const meta = config[type];
  const [query, setQuery] = useState('');
  const [records, setRecords] = useState([]);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const date = useZohoDateFilter();

  const load = async (nextPage = 1, search = '') => {
    setBusy(true);
    try {
      const result = await call(withDate(`${meta.path}?page=${nextPage}&search=${encodeURIComponent(search)}`, date));
      const rows = result[meta.key] || [];
      setRecords(current => nextPage === 1 ? rows : [...current, ...rows]);
      setPage(nextPage); setHasMore(Boolean(result.has_more));
    } catch (error) { setError(error.message); } finally { setBusy(false); }
  };

  useEffect(() => { void load(); }, [date]);
  useGlobalPager(() => load(page + 1, query.trim()), hasMore, busy);
  const search = event => { event?.preventDefault(); void load(1, query.trim()); };
  const onCreated = reference => { setShowCreate(false); if (reference) setQuery(reference); setCreated(Boolean(reference)); if (reference) void load(1, reference); };
  const createLabel = type === 'quote' ? 'quote' : type === 'sales-order' ? 'sales order' : type === 'invoice' ? 'invoice' : 'purchase order';

  return <section className="products-page">
    <div className="products-heading"><div><small>{`LIVE ZOHO BOOKS ${meta.title.toUpperCase()}`}</small><h2>{meta.title}</h2><p>{meta.description}</p></div><button className="primary" onClick={() => { setShowCreate(true); setCreated(false); }}>Create {createLabel}</button></div>
    {created && <div className="success">{meta.type} created successfully in Zoho Books.</div>}
    <form className="product-search" onSubmit={search}><input value={query} onChange={event => setQuery(event.target.value)} placeholder={`Search ${meta.title.toLowerCase()}, customer, or reference…`} /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></form>
    {query && !busy && <p className="product-count">{records.length} {records.length === 1 ? meta.type.toLowerCase() : `${meta.type.toLowerCase()}s`} found</p>}
    {!query.trim() && !busy && !records.length ? <div className="product-empty">Loading recent {meta.title.toLowerCase()}…</div> : !busy && !records.length ? <div className="product-empty">{query.trim() ? `No ${meta.title.toLowerCase()} matched “${query.trim()}”.` : meta.empty}</div> : <div className="product-grid">{records.map(record => <RecordCard record={record} type={meta.type} key={record.estimate_id || record.salesorder_id || record.invoice_id || record.purchaseorder_id || record.estimate_number || record.salesorder_number || record.invoice_number || record.purchaseorder_number} />)}</div>}
    {showCreate && <div className="modal-backdrop" onClick={() => setShowCreate(false)}><BooksDocumentForm type={type} setError={setError} onCreated={onCreated} /></div>}
  </section>;
}
