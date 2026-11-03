import type { CaptureState, RecorderSession } from './session'

export type PersistentSessionEntry = {
  key: string
  userId: string
  workspaceId: string
  memberId: string
  customerName: string
  customerPhone: string
  customerEmail: string
  sessionId: string | null
  conversationId: string | null
  recorder: RecorderSession | null
  capture: CaptureState
  demoTimer: number | null
  demoElapsed: number
  saved: boolean
  listeners: Set<(state: CaptureState) => void>
}

const entries = new Map<string, PersistentSessionEntry>()

const emptyCapture = (): CaptureState => ({ capture: 'idle', samples: 0, elapsedSeconds: 0, level: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false })

export function persistentSessionKey({ userId, workspaceId, memberId, customerPhone, customerName }: { userId?: string; workspaceId?: string; memberId?: string; customerPhone?: string; customerName?: string }) {
  const identity = (customerPhone || customerName || 'unknown').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')
  return `${userId || 'anonymous'}:${workspaceId || 'workspace'}:${memberId || userId || 'member'}:${identity}`
}

export function getPersistentSession(key: string) {
  return entries.get(key) || null
}

export function ensurePersistentSession(input: Omit<PersistentSessionEntry, 'recorder' | 'capture' | 'demoTimer' | 'demoElapsed' | 'saved' | 'listeners'>) {
  const existing = entries.get(input.key)
  if (existing) {
    Object.assign(existing, input)
    return existing
  }
  const entry: PersistentSessionEntry = { ...input, recorder: null, capture: emptyCapture(), demoTimer: null, demoElapsed: 0, saved: false, listeners: new Set() }
  entries.set(entry.key, entry)
  return entry
}

export function publishPersistentCapture(entry: PersistentSessionEntry, state: CaptureState) {
  entry.capture = state
  entry.listeners.forEach(listener => listener(state))
}

export function subscribePersistentCapture(entry: PersistentSessionEntry, listener: (state: CaptureState) => void) {
  entry.listeners.add(listener)
  listener(entry.capture)
  return () => entry.listeners.delete(listener)
}

export function removePersistentSession(entry: PersistentSessionEntry) {
  if (entry.demoTimer !== null) window.clearInterval(entry.demoTimer)
  entry.demoTimer = null
  entry.listeners.clear()
  if (entries.get(entry.key) === entry) entries.delete(entry.key)
}

export function activePersistentSession() {
  return [...entries.values()].find(entry => entry.demoTimer !== null || ['recording', 'paused'].includes(entry.capture.capture)) || null
}
