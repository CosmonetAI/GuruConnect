import { createClient } from '@supabase/supabase-js';
import type { components } from '../../packages/contracts/api.generated';
import seedData from './mock-data.json';
export type ConversationCreateInput = components['schemas']['ConversationCreate'];
export type ChunkInput = components['schemas']['Chunk'];
export type ManifestInput = components['schemas']['Manifest'];
// This branch is intentionally frontend-only. Environment credentials are not used here.
export const configured = false;
const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
export const localMode = loopback;
export const supabase: ReturnType<typeof createClient> | null = null;
export const apiBase = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
const mockDataKey = 'guruconnect-frontend-mock-data';
type MockData = typeof seedData & { profile: Record<string, unknown>; team: Record<string, unknown>[]; enquiries: Record<string, any>[]; conversations: Record<string, any>[]; connections: Record<string, any>[] };
function readMockData(): MockData {
  try {
    const saved = sessionStorage.getItem(mockDataKey);
    return saved ? JSON.parse(saved) as MockData : structuredClone(seedData) as MockData;
  } catch { return structuredClone(seedData) as MockData; }
}
function writeMockData(data: MockData) { sessionStorage.setItem(mockDataKey, JSON.stringify(data)); }
function mockPage(items: any[]) { return { items, total: items.length, totalPages: Math.max(1, Math.ceil(items.length / 20)), page: 1, pageSize: 20 }; }
async function mockRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const data = readMockData();
  const method = (init.method ?? 'GET').toUpperCase();
  const body = init.body ? JSON.parse(String(init.body)) as Record<string, any> : {};
  const cleanPath = path.split('?')[0];
  if (cleanPath === '/profile') {
    if (method === 'PATCH') data.profile = { ...data.profile, display_name: body.display_name ?? data.profile.display_name, phone: body.phone ?? data.profile.phone, availability_status: body.availability_status ?? data.profile.availability_status, leave_end_date: body.leave_end_date ?? data.profile.leave_end_date, preferences: body.preferences ?? data.profile.preferences };
    writeMockData(data); return data.profile as T;
  }
  if (cleanPath === '/team') return { items: data.team } as T;
  if (cleanPath === '/invites' && method === 'POST') {
    data.team = [...data.team, { user_id: `mock-member-${Date.now()}`, display_name: body.name, email: body.email, role: body.role, status: 'invited', availability_status: 'active' }];
    writeMockData(data); return { ok: true } as T;
  }
  const teamDelete = cleanPath.match(/^\/team\/([^/]+)\/delete$/);
  if (teamDelete && method === 'DELETE') { data.team = data.team.filter(item => item.user_id !== decodeURIComponent(teamDelete[1])); writeMockData(data); return { ok: true } as T; }
  if (cleanPath === '/connections') return { items: data.connections } as T;
  if (cleanPath === '/analytics') return { enquiries: data.enquiries.length, newEnquiries: data.enquiries.filter(item => item.stage === 'new').length, quoted: 0, won: 0, channels: [] } as T;
  if (cleanPath === '/enquiries/unread-count') return { count: data.enquiries.filter(item => item.stage === 'new').length } as T;
  if (cleanPath === '/walkins/missed-visitors') return { items: [] } as T;
  if (cleanPath === '/enquiries') {
    if (method === 'POST') {
      const created = { ...body, id: `mock-enquiry-${Date.now()}`, enquiryNumber: `ENQ-${new Date().getFullYear()}-${String(data.enquiries.length + 1001)}`, createdAt: new Date().toISOString(), stage: body.stage ?? 'new', recording: null };
      data.enquiries = [created as any, ...data.enquiries]; writeMockData(data); return created as T;
    }
    const query = new URLSearchParams(path.split('?')[1] ?? '').get('query')?.toLowerCase() ?? '';
    return mockPage(data.enquiries.filter(item => !query || `${item.customerName} ${item.phone} ${item.summary}`.toLowerCase().includes(query))) as T;
  }
  const enquiryMatch = cleanPath.match(/^\/enquiries\/([^/]+)(?:\/state)?$/);
  if (enquiryMatch) {
    const item = data.enquiries.find(entry => entry.id === decodeURIComponent(enquiryMatch[1]));
    if (!item) throw new ApiError(404, 'not_found', 'Enquiry not found.');
    if (method === 'PATCH') Object.assign(item, cleanPath.endsWith('/state') ? { stage: body.stage } : body);
    writeMockData(data); return item as T;
  }
  if (cleanPath === '/conversations') return mockPage(data.conversations) as T;
  if (cleanPath.startsWith('/conversations/')) return data.conversations.find(item => item.id === cleanPath.split('/')[2]) as T;
  if (cleanPath === '/workspaces') return { items: [] } as T;
  return {} as T;
}
const serviceErrorMessages: Record<string, string> = {
  api_unreachable: localMode ? 'Local services could not be reached. Check the running API and refresh this screen.' : 'Workspace services could not be reached. Check your connection and try again.',
  configuration_mismatch: 'Workspace services are using different modes. Ask your administrator to finish the workspace setup, then try again.',
  engine_unconfigured: 'The managed speech service is unavailable. Contact your administrator, then retry the failed stage. Your saved audio is preserved.',
  database_schema_missing: 'ConnectCosmos setup is incomplete. Ask your administrator to apply all database migrations, then try again.',
  recording_not_saved: 'Stop recording and finish saving the audio before saving this enquiry.',
  stale_connection_generation: 'This connection changed while the form was open. Refresh its settings and try again.',
  database_unavailable: 'Workspace services cannot reach the database. Ask your administrator to check the database connection, then try again.',
  database_not_configured: 'Workspace services are not configured yet. Ask your administrator to finish the database setup, then try again.',
  database_busy: 'Workspace services are temporarily unavailable. Wait a moment and try again. If this continues, ask your administrator to check the database connection.',
  storage_unavailable: 'Recording storage is unavailable. Ask your administrator to check storage access and connectivity before retrying processing.',
  storage_write_failed: 'The processing result could not be saved to recording storage. Ask your administrator to check storage access and configuration before retrying processing.',
  request_timeout: 'The request took too long. Check your connection and try again. If you were saving a change, refresh its status before submitting it again.',
  configuration_missing: 'Workspace services are not configured yet. Ask your administrator to finish the workspace setup, then try again.',
  invalid_credentials: 'The managed service rejected its credentials. Ask your administrator to check the service configuration.',
  metadata_permission_denied: 'This connection check needs User → Read (user_read) permission on your ElevenLabs key. Enable it, save changes in ElevenLabs, and test again.',
  provider_permission_denied: 'The API key does not allow this action. Check its permissions in the provider dashboard and try again.',
  provider_quota_exceeded: 'The provider account or API key has reached its credit limit. Check its remaining credits and key limits before retrying.',
  provider_payment_required: 'The provider requires payment or additional credits before this action can run.',
  provider_plan_access_denied: 'The provider account’s plan does not allow this feature. Check the plan’s access before retrying.',
  provider_model_access_denied: 'The provider account does not have access to this model. Check the model permissions before retrying.',
  provider_access_denied: 'The provider denied access. Check the key’s permissions, workspace access, and IP allowlist.',
  provider_request_rejected: 'The provider rejected the request. Check your key and connection settings, then test again.',
  provider_connection_failed: 'The provider could not be reached. Check the network connection and try again.',
  provider_rate_limited: 'The provider’s request limit was reached. Wait a moment before trying again.',
  provider_unavailable: 'The provider is temporarily unavailable. Wait a moment and try again.',
  translation_refused: 'The translation service could not translate this turn. Your source text is preserved for review.',
  translation_incomplete: 'The translation did not finish. Your source text is preserved; review the processing status before retrying.',
  translator_invalid_response: 'The translation service returned an unusable result. Your source text is preserved for review.',
  translation_term_preservation_failed: 'The translation changed a number or product code. Your source text is preserved for review.',
  external_translation_uncertain: 'The translation request may have been processed. Review its usage before submitting another paid attempt.',
  translator_execution_uncertain: 'The translation request may have been processed. Review its usage before submitting another paid attempt.',
  translator_unconfigured: 'The managed English translation service is unavailable. Ask your administrator to check its configuration.',
  provider_model_mismatch: 'The provider returned a different model. Your source text is preserved; ask your administrator to check model access.',
};
export function errorMessageForCode(code: string, fallback = 'The request failed. Please try again.'): string {
  return Object.hasOwn(serviceErrorMessages, code) ? serviceErrorMessages[code] : fallback;
}
export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string, public readonly details?: Record<string, unknown>) { super(errorMessageForCode(code, message)); this.name = 'ApiError'; }
}
function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const aborted = () => { signal.removeEventListener('abort', aborted); reject(signal.reason ?? new DOMException('The request was cancelled.', 'AbortError')); };
    signal.addEventListener('abort', aborted, { once: true });
    // The operation may already be waiting on the auth client's session lock.
    // Observe its eventual result even after cancellation without starting another request.
    operation.then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted));
    if (signal.aborted) aborted();
  });
}
export async function request<T>(path: string, workspace?: string | null, init: RequestInit = {}, memberId?: string | null): Promise<T> {
  if (!supabase && !localMode) throw new ApiError(503, 'configuration_missing', 'Workspace services are not ready yet. Ask your administrator for help.');
  if (localMode) return mockRequest<T>(path, init);
  if (localMode && !['localhost', '127.0.0.1', '[::1]'].includes(new URL(apiBase).hostname)) throw new ApiError(503, 'configuration_missing', 'Local mode needs a localhost API address.');
  const abort = new AbortController();
  const cancelled = () => abort.abort(init.signal?.reason ?? new DOMException('The request was cancelled.', 'AbortError'));
  init.signal?.addEventListener('abort', cancelled, { once: true });
  if (init.signal?.aborted) cancelled();
  const read = ['GET', 'HEAD'].includes((init.method ?? 'GET').toUpperCase());
  const timeout = setTimeout(() => abort.abort(new DOMException('The request took too long.', 'TimeoutError')), read ? 10000 : 30000);
  try {
    return await abortable((async () => {
      if (abort.signal.aborted) throw abort.signal.reason;
      const session = supabase ? (await supabase.auth.getSession()).data.session : null;
      if (abort.signal.aborted) throw abort.signal.reason;
      if (!session && !localMode) throw new ApiError(401, 'session_expired', 'Your session has expired. Sign in to continue.');
      const headers = new Headers(init.headers);
      if (session) {
        headers.set('Authorization', `Bearer ${session.access_token}`);
        if (session.user?.id) headers.set('X-User-ID', session.user.id);
      }
      if (workspace) headers.set('X-Workspace-ID', workspace);
      if (memberId) headers.set('X-Workspace-Member-ID', memberId);
      if (init.body) headers.set('Content-Type', 'application/json');
      const response = await fetch(`${apiBase}/api/v1${path}`, { ...init, headers, signal: abort.signal, credentials: 'omit' });
      if (configured && response.headers.get('X-GuruConnect-Mode') === 'local') throw new ApiError(503, 'configuration_mismatch', 'Frontend and backend modes differ.');
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string | { message?: string; code?: string }; error?: { message?: string; code?: string } };
        const detail = typeof body.detail === 'string' ? { message: body.detail } : body.detail || body.error;
        throw new ApiError(response.status, detail?.code || 'request_failed', detail?.message || `The request failed (${response.status}). Please try again.`, typeof detail === 'object' ? detail as Record<string, unknown> : undefined);
      }
      return response.status === 204 ? undefined as T : normalizeResponse(path, await response.json()) as T;
    })(), abort.signal);
  } catch (error) {
    if (abort.signal.aborted && abort.signal.reason instanceof DOMException && abort.signal.reason.name === 'TimeoutError') {
      throw new ApiError(504, 'request_timeout', 'The request took too long. Please try again.');
    }
    if (error instanceof TypeError && !abort.signal.aborted) throw new ApiError(503, 'api_unreachable', 'Workspace services could not be reached.');
    throw error;
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener('abort', cancelled);
  }
}

/** Authenticated binary response for progressive audio and other streamed media. */
export async function requestStream(path: string, workspace?: string | null, init: RequestInit = {}, memberId?: string | null): Promise<Response> {
  if (!supabase && !localMode) throw new ApiError(503, 'configuration_missing', 'Workspace services are not ready yet. Ask your administrator for help.');
  if (localMode) throw new ApiError(503, 'local_demo_only', 'Live audio processing is disabled in frontend demo mode.');
  if (localMode && !['localhost', '127.0.0.1', '[::1]'].includes(new URL(apiBase).hostname)) throw new ApiError(503, 'configuration_missing', 'Local mode needs a localhost API address.');
  const signal = init.signal ?? undefined;
  signal?.throwIfAborted();
  try {
    const sessionPromise = supabase?.auth.getSession();
    const session = sessionPromise ? (await (signal ? abortable(sessionPromise, signal) : sessionPromise)).data.session : null;
    signal?.throwIfAborted();
    if (!session && !localMode) throw new ApiError(401, 'session_expired', 'Your session has expired. Sign in to continue.');
    const headers = new Headers(init.headers);
    if (session) {
      headers.set('Authorization', `Bearer ${session.access_token}`);
      if (session.user?.id) headers.set('X-User-ID', session.user.id);
    }
    if (workspace) headers.set('X-Workspace-ID', workspace);
    if (memberId) headers.set('X-Workspace-Member-ID', memberId);
    const response = await fetch(`${apiBase}/api/v1${path}`, { ...init, headers, signal, credentials: 'omit' });
    if (configured && response.headers.get('X-GuruConnect-Mode') === 'local') throw new ApiError(503, 'configuration_mismatch', 'Frontend and backend modes differ.');
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string | Record<string, unknown>; error?: Record<string, unknown> };
      const detail = typeof body.detail === 'string' ? { message: body.detail } : body.detail || body.error || {};
      throw new ApiError(response.status, String(detail.code || 'request_failed'), String(detail.message || `The request failed (${response.status}). Please try again.`), detail);
    }
    return response;
  } catch (error) {
    if (error instanceof TypeError && !signal?.aborted) throw new ApiError(503, 'api_unreachable', 'Workspace services could not be reached.');
    throw error;
  }
}
// These presentation aliases mirror the validated API's snake_case schema. They do not infer completion.
export function normalizeResponse(path: string, input: unknown): unknown {
  if (!input || typeof input !== 'object') return input;
  const row = input as Record<string, unknown>;
  const translation = (r: Record<string, unknown>) => {
    const provenance = r.provenance as Record<string, unknown> | undefined;
    const snapshot = (r.config_snapshot as Record<string, unknown> | undefined)?.translation as Record<string, unknown> | undefined;
    const actual = provenance?.translation;
    const strategy = typeof actual === 'string' && actual.startsWith('openai-') ? 'openai' : actual === 'azure-standard-v3' ? 'azure' : r.translation_strategy;
    const model = provenance?.translation_model ?? (snapshot?.config as Record<string, unknown> | undefined)?.model ?? r.translator_model;
    return { translation_strategy: strategy, translator_model: model };
  };
  const conversation = (r: Record<string, unknown>) => {
    const production = Array.isArray(r.runs) ? (r.runs as Record<string, unknown>[]).find(v=>v.kind==='production') : undefined;
    const version = Array.isArray(r.versions) ? (r.versions as Record<string, unknown>[]).find(v=>v.id===r.active_version_id) : undefined;
    return { ...r, phone: r.phone ?? r.customer_phone, email: r.email ?? r.customer_email, ...translation(version ?? production ?? r), finalization_state: r.final_state ?? r.finalization_state ?? 'pending', active_transcript_version_id: r.active_version_id ?? r.active_transcript_version_id, engine_id: r.engine_id ?? production?.engine_id };
  };
  const run = (r: Record<string, unknown>) => ({...r, ...translation(r), model:r.model_version, covered_seconds:typeof r.coverage_samples==='number'?r.coverage_samples/16000:undefined});
  if (/\/transcript(?:\?|$)/.test(path)) { const version = row.version as Record<string, unknown> | null; return { ...row, ...translation(version ?? row), id:version?.id, version_id:version?.id, run_id:version?.run_id, kind:version?.kind, status:version?.english_state, manual_review_required:!!(version?.provenance as Record<string, unknown>|undefined)?.manual_review_required, speaker_state:version?.speaker_state, covered_seconds:typeof version?.coverage_samples==='number'?version.coverage_samples/16000:undefined, turns:(row.turns as Record<string, unknown>[]||[]).map(t=>({...t,speaker_id:t.speaker_ref,is_uncertain:t.speaker_confidence==='uncertain'||t.speaker_confidence==='overlap'||t.attribution_state==='uncertain'||t.attribution_state==='unresolved'||!t.speaker_ref})), speaker_assignments:(row.speakers as Record<string, unknown>[]||[]).map(s=>({...s,speaker_id:s.speaker_ref,name:s.display_name})) }; }
  if (/\/progress(?:\?|$)/.test(path)) { const runs=(row.runs as Record<string, unknown>[]||[]).map(run); return {...row,conversation:conversation({...row.conversation as Record<string, unknown>,runs}),runs,jobs:(row.jobs as Record<string, unknown>[]||[]).map(j=>({...j,job_type:j.kind,status:j.state})),confirmed_samples:(row.chunks as Record<string, unknown>|undefined)?.saved_samples}; }
  if (/\/connections(?:\?|$)/.test(path) && Array.isArray(row.items)) return {...row,items:(row.items as Record<string, unknown>[]).map(c=>({...c,enabled:!['disabled','disconnected'].includes(String(c.state)),last_test_at:c.tested_at,last_error:c.last_error_code,safe_config:c.config}))};
  if (/\/runs(?:\?|$)/.test(path) && Array.isArray(row.items)) return {...row,items:(row.items as Record<string, unknown>[]).map(run)};
  if (/\/conversations(?:\?|$)/.test(path) && Array.isArray(row.items)) return {...row,items:(row.items as Record<string, unknown>[]).map(conversation)};
  if (/\/conversations\/[\w-]+$/.test(path)) return conversation(row);
  return input;
}
export const json = (method: string, value?: unknown, key?: string): RequestInit => ({ method, body: value === undefined ? undefined : JSON.stringify(value), headers: key ? { 'Idempotency-Key': key } : undefined });
export type Role = 'owner' | 'salesperson' | 'receptionist';
export interface Workspace { id: string; name: string; role: Role; member_id?: string; display_name?: string; }
export interface Customer { id: string; name: string; created_at: string; notes?: string; }
export interface Conversation {
  id: string; customer_name_snapshot: string; salesperson_name_snapshot?: string; title?: string; location?: string;
  phone?: string; email?: string; customer_id?: string;
  created_at: string; duration_seconds?: number; total_samples?: number; capture_state: string; upload_state: string;
  transcription_state: string; english_state?: string; speaker_state: string; finalization_state: string;
  source_language?: string; active_transcript_version_id?: string; engine_id?: string; owner_user_id?: string;
  translation_strategy?: string; translator_model?: string;
}
export interface Page<T> { items: T[]; next_cursor?: string | null; }
export interface Turn { id: string; start_seconds: number; end_seconds: number; speaker_id: string | null; english_text?: string | null; source_text?: string | null; source_language?: string; is_uncertain?: boolean; }
export interface Assignment { speaker_id: string; name: string; role: 'salesperson' | 'customer' | 'other'; }
export interface Transcript { translation_strategy?: string; translator_model?: string; manual_review_required?: boolean; id?: string; version_id?: string; run_id?: string; status?: string; speaker_state?:string; turns: Turn[]; speaker_assignments?: Assignment[]; engine_id?: string; model?: string; covered_seconds?: number; kind?: string; }
export interface Progress { conversation: Conversation; runs?: Run[]; jobs?: Job[]; missing_chunks?: number[]; confirmed_samples?: number; }
export interface Job { id: string; run_id?: string; window_id?: string | null; job_type: string; status: string; error_code?: string; audio_start_seconds?: number; audio_end_seconds?: number; }
export interface Run { id: string; engine_id: string; kind: string; state: string; model?: string; transcript_version_id?: string; covered_seconds?: number; created_at?: string; versions?: {id:string;english_state:string;kind:string}[]; processed_audio_seconds?:number; translation_characters?:number; translation_strategy?:string; translator_model?:string; translation_input_tokens?:number; translation_output_tokens?:number; }
export interface Connection { id: string; engine_id: string; state: string; enabled: boolean; generation: number; last_test_at?: string; last_error?: string; queued_jobs?: number; inflight_jobs?: number; safe_config?: Record<string, unknown>; safe_metadata?: { platform_managed?: boolean; readiness_source?: string }; }
export interface EngineSettings { default_engine_id: string | null; salesperson_engine_selection?: boolean; comparison_allowed?: boolean; allowed_engine_ids?: string[]; budget_policy?:Record<string,unknown>; server_managed_providers?: boolean; }
export interface Member { user_id: string; display_name?: string; email?: string; role: Role; status: string; availability_status?: string; last_seen_at?: string; }
export function message(error: unknown): string { return error instanceof Error ? error.message : 'Something went wrong. Please try again.'; }
export function duration(seconds = 0): string { const s = Math.max(0, Math.floor(seconds)); return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`; }
