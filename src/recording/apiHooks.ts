import { json, request } from '../services/officeApi'
import type { RecordingHooks } from './session'

export function recordingHooks(conversationId: string, workspaceId: string, partId: string, leaseToken: string, tabId: string, signal?: AbortSignal): RecordingHooks {
  const root = `/conversations/${encodeURIComponent(conversationId)}`
  const post = <T>(path: string, body: unknown) => request<T>(`${root}${path}`, workspaceId, { ...json('POST', body), signal })
  return {
    authorize: chunk => post('/chunks/authorize', chunk),
    confirm: chunk => post('/chunks/confirm', chunk),
    heartbeat: () => post('/lease/heartbeat', { tab_id: tabId, lease_token: leaseToken }),
    event: (kind, sample) => post('/events', { kind, sample, part_id: partId, lease_token: leaseToken }),
    finalize: manifest => post('/finalize', manifest),
  }
}
