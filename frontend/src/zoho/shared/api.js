const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
export const API = `${API_BASE}/api`;

export async function call(path, opts = {}, workspaceId = null) {
  const headers = new Headers({ 'Content-Type': 'application/json', ...(opts.headers || {}) });
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
  const timeout = AbortSignal.timeout(30000);
  const signal = opts.signal ? AbortSignal.any([timeout, opts.signal]) : timeout;
  const response = await fetch(API + path, { ...opts, headers, signal, credentials: 'omit' });
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || data.error?.message || 'Request failed');
  return data;
}

export function withDate(path, date) {
  const cleaned = path.replace(/([?&])search=$/, '');
  return date ? `${cleaned}${cleaned.includes('?') ? '&' : '?'}date=${encodeURIComponent(date)}` : cleaned;
}
