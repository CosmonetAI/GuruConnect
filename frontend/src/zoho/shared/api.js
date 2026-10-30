const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
export const API = `${API_BASE}/api`;

export async function call(path, opts = {}, workspaceId = null) {
  const { timeoutMs = 30000, ...requestOptions } = opts;
  const headers = new Headers({ 'Content-Type': 'application/json', ...(requestOptions.headers || {}) });
  const workspace = workspaceId
    || localStorage.getItem('guruconnect-active-organisation')
    || localStorage.getItem('guruconnect-active-workspace');
  if (workspace) headers.set('X-Workspace-ID', workspace);
  const member = localStorage.getItem('guruconnect-active-workspace');
  if (member && !headers.has('X-Workspace-Member-ID')) headers.set('X-Workspace-Member-ID', member);
  try {
    const { supabase } = await import('../../services/officeApi');
    const session = supabase ? (await supabase.auth.getSession()).data.session : null;
    if (session) {
      headers.set('Authorization', `Bearer ${session.access_token}`);
      headers.set('X-User-ID', session.user.id);
    }
  } catch {}
  const timeout = AbortSignal.timeout(Number.isFinite(timeoutMs) ? Math.max(1000, Math.min(120000, timeoutMs)) : 30000);
  const signal = requestOptions.signal ? AbortSignal.any([timeout, requestOptions.signal]) : timeout;
  const response = await fetch(API + path, { ...requestOptions, headers, signal, credentials: 'omit' });
  const data = await response.json().catch(() => ({ detail: `Request failed (${response.status})` }));
  if (!response.ok) {
    const details = typeof data.detail === 'object' && data.detail ? data.detail : data.error || {};
    const error = Object.assign(Error(typeof data.detail === 'string' ? data.detail : details.message || 'Request failed'), { status: response.status, code: details.code, details });
    throw error;
  }
  return data;
}

export function withDate(path, date) {
  const cleaned = path.replace(/([?&])search=$/, '');
  return date ? `${cleaned}${cleaned.includes('?') ? '&' : '?'}date=${encodeURIComponent(date)}` : cleaned;
}
