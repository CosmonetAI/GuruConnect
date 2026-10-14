import React, { useEffect, useState } from 'react';
import { call, withDate } from '../shared/api';
import { useZohoDateFilter, useGlobalPager } from '../shared/hooks';
import { ProductCard } from '../components/Records';

export function ProductsPage({ setError }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const date = useZohoDateFilter();
  const [page, setPage] = useState(1); const [hasMore, setHasMore] = useState(false);
  useEffect(() => { if (query.trim()) return; let cancelled = false; setBusy(true); call(withDate('/products?page=1', date)).then(result => { if (!cancelled) { setItems(result.items || []); setHasMore(Boolean(result.has_more)); setPage(1); } }).catch(e => !cancelled && setError(e.message)).finally(() => !cancelled && setBusy(false)); return () => { cancelled = true; }; }, [date]);
  const showMore = async () => { if (!hasMore || busy) return; setBusy(true); try { const result = await call(withDate(`/products?page=${page + 1}&search=${encodeURIComponent(query.trim())}`, date)); setItems(current => [...current, ...(result.items || [])]); setPage(page + 1); setHasMore(Boolean(result.has_more)); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  useGlobalPager(showMore, hasMore, busy);
  useEffect(() => {
    if (!query.trim() || !/\s$/.test(query)) return;
    const timer = setTimeout(async () => {
      setBusy(true); setError('');
      try { const result = await call(withDate(`/products?page=1&search=${encodeURIComponent(query.trim())}`, date)); setItems(result.items || []); setPage(1); setHasMore(Boolean(result.has_more)); }
      catch (e) { setError(e.message); setItems([]); }
      finally { setBusy(false); }
    }, 350);
    return () => clearTimeout(timer);
  }, [query, date]);
  const search = async event => {
    event?.preventDefault();
    if (!query.trim()) { setItems([]); return; }
    setBusy(true); setError('');
    try { const result = await call(withDate(`/products?page=1&search=${encodeURIComponent(query.trim())}`, date)); setItems(result.items || []); setPage(1); setHasMore(Boolean(result.has_more)); }
    catch (e) { setError(e.message); setItems([]); }
    finally { setBusy(false); }
  };
  return <section className="products-page"><div className="products-heading"><div><small>LIVE ZOHO BOOKS CATALOGUE</small><h2>My Products</h2><p>Search active products directly from Zoho Books. Items are not stored in Supabase.</p></div><span className="live-badge">● Live search</span></div><form className="product-search" onSubmit={search}><input value={query.trim()} onChange={e => setQuery(e.target.value)} placeholder="Search products, descriptions, SKU…" /><button className="primary" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></form>{!busy && <p className="product-count">{items.length} active {items.length === 1 ? 'item' : 'items'} found</p>} {!query.trim() && !items.length ? <div className="product-empty">Loading recent products…</div> : !busy && !items.length ? <div className="product-empty">{query.trim() ? `No active products matched “${query.trim()}”.` : 'No active products found.'}</div> : <div className="product-grid">{items.map(item => <ProductCard item={item} key={item.item_id || item.sku || item.name} />)}</div>}</section>;
}
