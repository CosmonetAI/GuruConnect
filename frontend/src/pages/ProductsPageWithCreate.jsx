import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { ProductCard } from '../components/Records';
import PageLoader from '../components/PageLoader';
import ProductCreateModal from '../components/ProductCreateModal';

const activeFirst = items => [...items].sort((left, right) => {
  const leftActive = ['active', 'enabled'].includes(String(left.status ?? left.Status ?? 'active').toLowerCase());
  const rightActive = ['active', 'enabled'].includes(String(right.status ?? right.Status ?? 'active').toLowerCase());
  return Number(rightActive) - Number(leftActive);
});

export function ProductsPage({ setError, showCreate, setShowCreate }) {
  const [query, setQuery] = useState(''); const [items, setItems] = useState([]); const [busy, setBusy] = useState(false); const date = useZohoDateFilter(); const [page, setPage] = useState(1); const [providerPage, setProviderPage] = useState(1); const [hasMore, setHasMore] = useState(false);
  const load = async (nextPage = 1, search = '', nextProviderPage = 1) => { setBusy(true); try { const result = await call(withDate(`/products?page=${nextPage}&provider_page=${nextProviderPage}&search=${encodeURIComponent(search)}`, date)); setItems(current => { const merged = nextPage === 1 ? (result.items || []) : [...current, ...(result.items || [])]; return activeFirst([...new Map(merged.map(item => [item.item_id || item.id || item.sku || item.name, item])).values()]); }); setPage(nextPage); setProviderPage(result.next_provider_page || (nextProviderPage + 1)); setHasMore(Boolean(result.has_more)); } catch (error) { setError(error.message); } finally { setBusy(false); } };
  useEffect(() => { if (!query.trim()) void load(1, '', 1); }, [date]);
  const showMore = () => { if (!hasMore || busy) return; void load(page + 1, query.trim(), providerPage); }; useGlobalPager(showMore, hasMore, busy);
  const search = event => { event?.preventDefault(); void load(1, query.trim(), 1); };
  return <section className="products-page product-catalogue-page"><form className="product-search" data-search-label="Search by product, description, or SKU" onSubmit={search}><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search by product, description, or SKU…" /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search catalogue'}</button></form>{!busy && <p className="product-count">{items.length} {items.length === 1 ? 'product' : 'products'} available</p>}{!query.trim() && busy ? <PageLoader kind="products" label="Loading product catalogue" detail="Finding the latest products and pricing." /> : !items.length ? <div className="product-empty">{query.trim() ? `No products matched “${query.trim()}”.` : 'No products are available yet.'}</div> : <div className="product-grid">{items.map(item => <ProductCard item={item} key={item.item_id || item.sku || item.name} />)}</div>}{showCreate && <ProductCreateModal setError={setError} onClose={() => setShowCreate(false)} onCreated={() => { void load(1, query.trim(), 1); }} />}</section>;
}
