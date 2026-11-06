import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { ProductCard } from '../components/Records';
import PageLoader from '../components/PageLoader';
import ProductCreateModal from '../components/ProductCreateModal';

export function ProductsPage({ setError }) {
  const [query, setQuery] = useState(''); const [items, setItems] = useState([]); const [busy, setBusy] = useState(false); const [showCreate, setShowCreate] = useState(false); const date = useZohoDateFilter(); const [page, setPage] = useState(1); const [hasMore, setHasMore] = useState(false);
  const load = async (nextPage = 1, search = '') => { setBusy(true); try { const result = await call(withDate(`/products?page=${nextPage}&search=${encodeURIComponent(search)}`, date)); setItems(current => nextPage === 1 ? (result.items || []) : [...current, ...(result.items || [])]); setPage(nextPage); setHasMore(Boolean(result.has_more)); } catch (error) { setError(error.message); } finally { setBusy(false); } };
  useEffect(() => { if (!query.trim()) void load(); }, [date]);
  const showMore = () => { if (!hasMore || busy) return; void load(page + 1, query.trim()); }; useGlobalPager(showMore, hasMore, busy);
  const search = event => { event?.preventDefault(); void load(1, query.trim()); };
  return <section className="products-page"><div className="products-heading"><div><small>SALES CATALOGUE</small><h2>Product catalogue</h2><p>Find approved products, pricing, and availability while preparing a customer quotation or order.</p></div><div className="products-heading-actions"><button className="primary" type="button" onClick={() => setShowCreate(true)}>+ Add item</button><span className="live-badge">● Catalogue connected</span></div></div><form className="product-search" onSubmit={search}><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search by product, description, or SKU…" /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search catalogue'}</button></form>{!busy && <p className="product-count">{items.length} active {items.length === 1 ? 'product' : 'products'} available</p>}{!query.trim() && busy ? <PageLoader kind="products" label="Loading product catalogue" detail="Finding the latest approved products and pricing." /> : !items.length ? <div className="product-empty">{query.trim() ? `No active products matched “${query.trim()}”.` : 'No active products are available yet.'}</div> : <div className="product-grid">{items.map(item => <ProductCard item={item} key={item.item_id || item.sku || item.name} />)}</div>}{showCreate && <ProductCreateModal setError={setError} onClose={() => setShowCreate(false)} onCreated={() => { void load(1, query.trim()); }} />}</section>;
}
