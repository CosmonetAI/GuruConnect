import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Session, User } from '@supabase/supabase-js'
import { configured, localMode, request, supabase, message, json, type Workspace } from '../services/officeApi'

type DemoAccount = { id: string; email: string; name: string; role: Workspace['role']; branch: string; phone: string }
const demoAccounts: DemoAccount[] = [
  { id: 'owner-akshat', email: 'owner1@demo.in', name: 'Akshat Saxena', role: 'owner', branch: 'Bengaluru Central', phone: '9311854768' },
  { id: 'owner-meera', email: 'owner2@demo.in', name: 'Meera Iyer', role: 'owner', branch: 'Mysuru', phone: '9880112200' },
  ...Array.from({ length: 22 }, (_, index) => ({ id: `sales-${index + 1}`, email: `sales${String(index + 1).padStart(2, '0')}@demo.in`, name: ['Ravi Kumar', 'Sumanth Reddy', 'Priya Gupta', 'Sneha Kapoor'][index % 4], role: 'salesperson' as const, branch: ['Bengaluru Central', 'Mysuru', 'Hyderabad'][index % 3], phone: `987650${String(1000 + index).slice(-4)}` })),
  { id: 'reception-nisha', email: 'receptionist1@demo.in', name: 'Nisha Rao', role: 'receptionist', branch: 'Bengaluru Central', phone: '9876501200' },
  { id: 'reception-kavya', email: 'receptionist2@demo.in', name: 'Kavya Menon', role: 'receptionist', branch: 'Mysuru', phone: '9876501210' },
  { id: 'reception-arjun', email: 'receptionist3@demo.in', name: 'Arjun Das', role: 'receptionist', branch: 'Hyderabad', phone: '9876501220' }
]
const accountUser = (account: DemoAccount) => ({ id: account.id, email: account.email, app_metadata: {}, user_metadata: { display_name: account.name }, aud: 'authenticated', created_at: '2026-10-13T00:00:00Z' } as User)
const localUser = accountUser(demoAccounts[0])
const localWorkspace: Workspace = { id: 'mock-workspace-owner-akshat', name: 'ConnectCosmos Demo Workspace', role: 'owner', member_id: localUser.id, display_name: localUser.user_metadata.display_name }
const localWorkspaces: Workspace[] = demoAccounts.map(account => ({ id: `mock-workspace-${account.id}`, name: 'ConnectCosmos Demo Workspace', role: account.role, member_id: account.id, display_name: account.name }))

interface OfficeState {
  configured: boolean; localMode: boolean; session: Session | null; user: User | null; loading: boolean
  workspacesLoading: boolean; workspaces: Workspace[]; workspace: Workspace | null; error: string
  setWorkspace(id: string): void; reloadWorkspaces(): Promise<void>
  signIn(email: string, password: string): boolean; signOut(): void; demoAccounts: DemoAccount[]
  captureBusy: boolean; setCaptureBusy(busy: boolean): void
}
const OfficeContext = createContext<OfficeState | null>(null)

export function OfficeProvider({ children }: { children: ReactNode }) {
  const [localEmail, setLocalEmail] = useState<string | null>(() => sessionStorage.getItem('guruconnect-demo-account'))
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(!!supabase)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [active, setActive] = useState<string | null>(() => localEmail ? (localWorkspaces.find(item => item.member_id === (demoAccounts.find(account => account.email === localEmail)?.id ?? localUser.id))?.member_id ?? localUser.id) : null)
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
      setActive(old => result.items.some(item => (item.member_id ?? item.id) === old) ? old : result.items[0] ? (result.items[0].member_id ?? result.items[0].id) : null)
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
  useEffect(() => {
    if (!active) {
      localStorage.removeItem('guruconnect-active-workspace')
      localStorage.removeItem('guruconnect-active-organisation')
      return
    }
    localStorage.setItem('guruconnect-active-workspace', active)
    const selected = (localMode ? localWorkspaces : workspaces).find(item => (item.member_id ?? item.id) === active)
    if (selected) localStorage.setItem('guruconnect-active-organisation', selected.id)
  }, [active, workspaces, localMode])
  useEffect(() => {
    if (!session || localMode) return
    const ping = () => { void request('/presence', null, json('POST')).catch(() => undefined) }
    ping(); const timer = window.setInterval(ping, 30_000)
    return () => window.clearInterval(timer)
  }, [session, localMode])
  const localAccount = demoAccounts.find(account => account.email === localEmail) ?? demoAccounts[0]
  const localCurrentUser = localEmail ? accountUser(localAccount) : null
  return <OfficeContext.Provider value={{ configured: configured || localMode, localMode, session, user: localMode ? localCurrentUser : session?.user ?? null, loading, workspacesLoading,
    workspaces: localMode ? localWorkspaces : workspaces, workspace: localMode ? localWorkspaces.find(item => (item.member_id ?? item.id) === active) ?? localWorkspace : workspaces.find(item => (item.member_id ?? item.id) === active) ?? null, error, captureBusy, setCaptureBusy,
    reloadWorkspaces, signIn: (email, password) => { const account = demoAccounts.find(item => item.email.toLowerCase() === email.trim().toLowerCase()); if (!account || password !== 'Demo@123') return false; sessionStorage.setItem('guruconnect-demo-account', account.email); setLocalEmail(account.email); setActive(account.id); return true }, signOut: () => { sessionStorage.removeItem('guruconnect-demo-account'); setLocalEmail(null); setActive(null) }, demoAccounts, setWorkspace: id => { if (!captureBusy && (localMode ? localWorkspaces : workspaces).some(item => (item.member_id ?? item.id) === id)) setActive(id) } }}>{children}</OfficeContext.Provider>
}
export function useOffice() {
  const state = useContext(OfficeContext)
  if (!state) throw new Error('OfficeProvider is required')
  return state
}
