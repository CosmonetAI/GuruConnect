import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { ProductCard } from '../components/Records';
import PageLoader from '../components/PageLoader';

const activeFirst = items => [...items].sort((left, right) => {
  const leftActive = ['active', 'enabled'].includes(String(left.status ?? left.Status ?? 'active').toLowerCase());
  const rightActive = ['active', 'enabled'].includes(String(right.status ?? right.Status ?? 'active').toLowerCase());
  return Number(rightActive) - Number(leftActive);
});

export function ProductsPage({ setError }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const date = useZohoDateFilter();
  const [page, setPage] = useState(1); const [providerPage, setProviderPage] = useState(1); const [hasMore, setHasMore] = useState(false);
  useEffect(() => { if (query.trim()) return; let cancelled = false; setBusy(true); call(withDate('/products?page=1&provider_page=1', date)).then(result => { if (!cancelled) { setItems(activeFirst(result.items || [])); setHasMore(Boolean(result.has_more)); setPage(1); setProviderPage(result.next_provider_page || 2); } }).catch(e => !cancelled && setError(e.message)).finally(() => !cancelled && setBusy(false)); return () => { cancelled = true; }; }, [date]);
  const showMore = async () => { if (!hasMore || busy) return; setBusy(true); try { const result = await call(withDate(`/products?page=${page + 1}&provider_page=${providerPage}&search=${encodeURIComponent(query.trim())}`, date)); setItems(current => activeFirst([...new Map([...current, ...(result.items || [])].map(item => [item.item_id || item.id || item.sku || item.name, item])).values()])); setPage(page + 1); setProviderPage(result.next_provider_page || (providerPage + 1)); setHasMore(Boolean(result.has_more)); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  useGlobalPager(showMore, hasMore, busy);
  useEffect(() => {
    if (!query.trim() || !/\s$/.test(query)) return;
    const timer = setTimeout(async () => {
      setBusy(true); setError('');
      try { const result = await call(withDate(`/products?page=1&provider_page=1&search=${encodeURIComponent(query.trim())}`, date)); setItems(result.items || []); setPage(1); setProviderPage(result.next_provider_page || 2); setHasMore(Boolean(result.has_more)); }
      catch (e) { setError(e.message); setItems([]); }
      finally { setBusy(false); }
    }, 350);
    return () => clearTimeout(timer);
  }, [query, date]);
  const search = async event => {
    event?.preventDefault();
    if (!query.trim()) { setItems([]); return; }
    setBusy(true); setError('');
    try { const result = await call(withDate(`/products?page=1&provider_page=1&search=${encodeURIComponent(query.trim())}`, date)); setItems(result.items || []); setPage(1); setProviderPage(result.next_provider_page || 2); setHasMore(Boolean(result.has_more)); }
    catch (e) { setError(e.message); setItems([]); }
    finally { setBusy(false); }
  };
  return <section className="products-page product-catalogue-page"><div className="products-heading"><div><small>SALES CATALOGUE</small><h2>Product catalogue</h2><p>Find products, pricing, and availability while preparing a customer quotation or order.</p></div><span className="live-badge">● Catalogue connected</span></div><form className="product-search" onSubmit={search}><input value={query.trim()} onChange={e => setQuery(e.target.value)} placeholder="Search by product, description, or SKU…" /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search catalogue'}</button></form>{!busy && <p className="product-count">{items.length} {items.length === 1 ? 'product' : 'products'} available</p>} {!query.trim() && busy ? <PageLoader kind="products" label="Loading product catalogue" detail="Finding the latest products and pricing." /> : !query.trim() && !items.length ? <div className="product-empty">No products are available yet.</div> : !busy && !items.length ? <div className="product-empty">{query.trim() ? `No products matched “${query.trim()}”.` : 'No products are available yet.'}</div> : <div className="product-grid">{items.map(item => <ProductCard item={item} key={item.item_id || item.sku || item.name} />)}</div>}</section>;
}
