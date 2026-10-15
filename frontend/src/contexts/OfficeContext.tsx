import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Session, User } from '@supabase/supabase-js'
import { configured, localMode, request, supabase, message, json, type Workspace } from '../services/officeApi'

const localUser = { id: '00000000-0000-0000-0000-000000000001', email: 'local@localhost', app_metadata: {}, user_metadata: { display_name: 'Local user' }, aud: 'authenticated', created_at: '2026-10-13T00:00:00Z' } as User
const localWorkspace: Workspace = { id: '00000000-0000-0000-0000-000000000002', name: 'Local workspace', role: 'owner' }

interface OfficeState {
  configured: boolean; localMode: boolean; session: Session | null; user: User | null; loading: boolean
  workspacesLoading: boolean; workspaces: Workspace[]; workspace: Workspace | null; error: string
  setWorkspace(id: string): void; reloadWorkspaces(): Promise<void>
  captureBusy: boolean; setCaptureBusy(busy: boolean): void
}
const OfficeContext = createContext<OfficeState | null>(null)

export function OfficeProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(!!supabase)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [workspacesLoading, setWorkspacesLoading] = useState(false)
  const [captureBusy, setCaptureBusy] = useState(false)
  const identity = useRef<string | null>(null)
  const sequence = useRef(0)
  const reloadWorkspaces = useCallback(async () => {
    const userId = identity.current
    if (!userId) return
    const ticket = ++sequence.current
    setWorkspacesLoading(true)
    try {
      const result = await request<{ items: Workspace[] }>('/workspaces')
      if (identity.current !== userId || sequence.current !== ticket) return
      setWorkspaces(result.items)
      setActive(old => result.items.some(item => item.id === old) ? old : result.items[0]?.id ?? null)
      setError('')
    } catch (failure) {
      if (identity.current !== userId || sequence.current !== ticket) return
      setWorkspaces([]); setActive(null); setError(message(failure))
    } finally {
      if (identity.current === userId && sequence.current === ticket) setWorkspacesLoading(false)
    }
  }, [])
  useEffect(() => {
    if (!supabase) return
    let alive = true
    let receivedEvent = false
    const accept = (next: Session | null) => {
      if (!alive) return
      const userId = next?.user.id ?? null
      if (userId !== identity.current) {
        ++sequence.current; setWorkspaces([]); setActive(null); setError(''); setCaptureBusy(false)
        setWorkspacesLoading(!!next)
      }
      identity.current = userId; setSession(next); setLoading(false)
    }
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, next) => { receivedEvent = true; accept(next) })
    void supabase.auth.getSession().then(({ data, error: failure }) => {
      if (!alive || receivedEvent) return
      accept(data.session)
      if (failure) setError('Your session could not be restored. Sign in again.')
    }).catch(() => { if (alive) { setLoading(false); setError('Your session could not be restored. Sign in again.') } })
    return () => { alive = false; ++sequence.current; subscription.unsubscribe() }
  }, [])
  useEffect(() => { if (session?.user.id) void reloadWorkspaces() }, [session?.user.id, reloadWorkspaces])
  useEffect(() => { if (active) localStorage.setItem('guruconnect-active-workspace', active) }, [active])
  useEffect(() => {
    if (!session || localMode) return
    const ping = () => { void request('/presence', null, json('POST')).catch(() => undefined) }
    ping(); const timer = window.setInterval(ping, 30_000)
    return () => window.clearInterval(timer)
  }, [session, localMode])
  return <OfficeContext.Provider value={{ configured: configured || localMode, localMode, session, user: localMode ? localUser : session?.user ?? null, loading, workspacesLoading,
    workspaces: localMode ? [localWorkspace] : workspaces, workspace: localMode ? localWorkspace : workspaces.find(item => item.id === active) ?? null, error, captureBusy, setCaptureBusy,
    reloadWorkspaces, setWorkspace: id => { if (!captureBusy && workspaces.some(item => item.id === id)) setActive(id) } }}>{children}</OfficeContext.Provider>
}
export function useOffice() {
  const state = useContext(OfficeContext)
  if (!state) throw new Error('OfficeProvider is required')
  return state
}
