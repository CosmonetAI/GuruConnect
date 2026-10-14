const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
export const API = `${API_BASE}/api`;

export async function call(path, opts = {}) { const response = await fetch(API + path, { headers: { 'Content-Type': 'application/json' }, ...opts }); const data = await response.json(); if (!response.ok) throw Error(data.detail || 'Request failed'); return data; }

export function withDate(path, date) { const cleaned = path.replace(/([?&])search=$/, ''); return date ? `${cleaned}${cleaned.includes('?') ? '&' : '?'}date=${encodeURIComponent(date)}` : cleaned; }
