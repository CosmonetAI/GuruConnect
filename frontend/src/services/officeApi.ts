import { createClient } from '@supabase/supabase-js';
import type { components } from '../../packages/contracts/api.generated';
export type ConversationCreateInput = components['schemas']['ConversationCreate'];
export type ChunkInput = components['schemas']['Chunk'];
export type ManifestInput = components['schemas']['Manifest'];
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const key = (import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || import.meta.env.VITE_SUPABASE_ANON_KEY) as string | undefined;
export const configured = !!url && !!key && !!import.meta.env.VITE_API_BASE_URL && !url.includes('YOUR_PROJECT');
const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
export const localMode = !configured && loopback && (import.meta.env.DEV || import.meta.env.VITE_LOCAL_MODE === 'true');
export const supabase = configured ? createClient(url!, key!, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } }) : null;
export const apiBase = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');
const serviceErrorMessages: Record<string, string> = {
  api_unreachable: localMode ? 'Local services could not be reached. Check the running API and refresh this screen.' : 'Workspace services could not be reached. Check your connection and try again.',
  configuration_mismatch: 'The frontend is configured for Supabase but the API is running in local mode. Finish the backend setup for the same project, then restart the API.',
  engine_unconfigured: 'Configure ElevenLabs in Connections, then explicitly retry the failed speech stage. Your saved audio is preserved.',
  database_schema_missing: 'GuruConnect setup is incomplete. Ask your administrator to apply all database migrations, then try again.',
  recording_not_saved: 'Stop recording and finish saving the audio before saving this enquiry.',
  stale_connection_generation: 'This connection changed while the form was open. Refresh its settings and try again.',
  database_unavailable: 'Workspace services cannot reach the database. Ask your administrator to check the database connection, then try again.',
  database_not_configured: 'Workspace services are not configured yet. Ask your administrator to finish the database setup, then try again.',
  database_busy: 'Workspace services are temporarily unavailable. Wait a moment and try again. If this continues, ask your administrator to check the database connection.',
  request_timeout: 'The request took too long. Check your connection and try again. If you were saving a change, refresh its status before submitting it again.',
  configuration_missing: 'Workspace services are not configured yet. Ask your administrator to connect Supabase and the API, then try again.',
  invalid_credentials: 'The provider rejected the saved API key. Copy a complete, active key from its dashboard, save it here, and test again.',
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
  translator_unconfigured: 'Configure English translation with an OpenAI API key, then test the connection.',
  provider_model_mismatch: 'The provider returned a different model. Your source text is preserved; ask your administrator to check model access.',
};
export function errorMessageForCode(code: string, fallback = 'The request failed. Please try again.'): string {
  return Object.hasOwn(serviceErrorMessages, code) ? serviceErrorMessages[code] : fallback;
}
export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(errorMessageForCode(code, message)); this.name = 'ApiError'; }
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
export async function request<T>(path: string, workspace?: string | null, init: RequestInit = {}): Promise<T> {
  if (!supabase && !localMode) throw new ApiError(503, 'configuration_missing', 'Connect Supabase and the API before continuing.');
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
      if (session) headers.set('Authorization', `Bearer ${session.access_token}`);
      if (workspace) headers.set('X-Workspace-ID', workspace);
      if (init.body) headers.set('Content-Type', 'application/json');
      const response = await fetch(`${apiBase}/api/v1${path}`, { ...init, headers, signal: abort.signal, credentials: 'omit' });
      if (configured && response.headers.get('X-GuruConnect-Mode') === 'local') throw new ApiError(503, 'configuration_mismatch', 'Frontend and backend modes differ.');
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string | { message?: string; code?: string }; error?: { message?: string; code?: string } };
        const detail = typeof body.detail === 'string' ? { message: body.detail } : body.detail || body.error;
        throw new ApiError(response.status, detail?.code || 'request_failed', detail?.message || `The request failed (${response.status}). Please try again.`);
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
    return { ...r, ...translation(version ?? production ?? r), finalization_state: r.final_state ?? r.finalization_state ?? 'pending', active_transcript_version_id: r.active_version_id ?? r.active_transcript_version_id, engine_id: r.engine_id ?? production?.engine_id };
  };
  const run = (r: Record<string, unknown>) => ({...r, ...translation(r), model:r.model_version, covered_seconds:typeof r.coverage_samples==='number'?r.coverage_samples/16000:undefined});
  if (/\/transcript(?:\?|$)/.test(path)) { const version = row.version as Record<string, unknown> | null; return { ...row, ...translation(version ?? row), id:version?.id, version_id:version?.id, run_id:version?.run_id, status:version?.english_state, manual_review_required:!!(version?.provenance as Record<string, unknown>|undefined)?.manual_review_required, speaker_state:version?.speaker_state, covered_seconds:typeof version?.coverage_samples==='number'?version.coverage_samples/16000:undefined, turns:(row.turns as Record<string, unknown>[]||[]).map(t=>({...t,speaker_id:t.speaker_ref,is_uncertain:t.speaker_confidence==='uncertain'||t.speaker_confidence==='overlap'||t.attribution_state==='uncertain'||t.attribution_state==='unresolved'||!t.speaker_ref})), speaker_assignments:(row.speakers as Record<string, unknown>[]||[]).map(s=>({...s,speaker_id:s.speaker_ref,name:s.display_name})) }; }
  if (/\/progress(?:\?|$)/.test(path)) { const runs=(row.runs as Record<string, unknown>[]||[]).map(run); return {...row,conversation:conversation({...row.conversation as Record<string, unknown>,runs}),runs,jobs:(row.jobs as Record<string, unknown>[]||[]).map(j=>({...j,job_type:j.kind,status:j.state})),confirmed_samples:(row.chunks as Record<string, unknown>|undefined)?.saved_samples}; }
  if (/\/connections(?:\?|$)/.test(path) && Array.isArray(row.items)) return {...row,items:(row.items as Record<string, unknown>[]).map(c=>({...c,enabled:!['disabled','disconnected'].includes(String(c.state)),last_test_at:c.tested_at,last_error:c.last_error_code,safe_config:c.config}))};
  if (/\/runs(?:\?|$)/.test(path) && Array.isArray(row.items)) return {...row,items:(row.items as Record<string, unknown>[]).map(run)};
  if (/\/conversations(?:\?|$)/.test(path) && Array.isArray(row.items)) return {...row,items:(row.items as Record<string, unknown>[]).map(conversation)};
  if (/\/conversations\/[\w-]+$/.test(path)) return conversation(row);
  return input;
}
export const json = (method: string, value?: unknown, key?: string): RequestInit => ({ method, body: value === undefined ? undefined : JSON.stringify(value), headers: key ? { 'Idempotency-Key': key } : undefined });
export type Role = 'owner' | 'admin' | 'salesperson';
export interface Workspace { id: string; name: string; role: Role; }
export interface Customer { id: string; name: string; created_at: string; notes?: string; }
export interface Conversation {
  id: string; customer_name_snapshot: string; salesperson_name_snapshot?: string; title?: string; location?: string;
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
export interface Job { id: string; run_id?: string; job_type: string; status: string; error_code?: string; audio_start_seconds?: number; audio_end_seconds?: number; }
export interface Run { id: string; engine_id: string; kind: string; state: string; model?: string; transcript_version_id?: string; covered_seconds?: number; created_at?: string; versions?: {id:string;english_state:string;kind:string}[]; processed_audio_seconds?:number; translation_characters?:number; translation_strategy?:string; translator_model?:string; translation_input_tokens?:number; translation_output_tokens?:number; }
export interface Connection { id: string; engine_id: string; state: string; enabled: boolean; generation: number; last_test_at?: string; last_error?: string; queued_jobs?: number; inflight_jobs?: number; safe_config?: Record<string, unknown>; }
export interface EngineSettings { default_engine_id: string | null; salesperson_engine_selection?: boolean; comparison_allowed?: boolean; allowed_engine_ids?: string[]; budget_policy?:Record<string,unknown>; }
export interface Member { user_id: string; display_name?: string; email?: string; role: Role; status: string; }
export function message(error: unknown): string { return error instanceof Error ? error.message : 'Something went wrong. Please try again.'; }
export function duration(seconds = 0): string { const s = Math.max(0, Math.floor(seconds)); return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`; }
