import { useEffect, useState } from 'react';

export function useZohoDateFilter() { const [date, setDate] = useState(() => localStorage.getItem('zoho-filter-date') || ''); useEffect(() => { const sync = () => setDate(localStorage.getItem('zoho-filter-date') || ''); window.addEventListener('zoho-filter-date-changed', sync); return () => window.removeEventListener('zoho-filter-date-changed', sync); }, []); return date; }

export function useGlobalPager(showMore, hasMore, busy) { useEffect(() => { window.__zohoPager = { showMore, hasMore, busy }; window.dispatchEvent(new Event('zoho-pager-changed')); return () => { if (window.__zohoPager?.showMore === showMore) delete window.__zohoPager; window.dispatchEvent(new Event('zoho-pager-changed')); }; }, [showMore, hasMore, busy]); }
