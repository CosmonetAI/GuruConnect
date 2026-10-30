import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { AudioLines, ArrowRight, Check, ChevronDown, Languages, Search, ShieldCheck } from 'lucide-react'
import { useOffice } from '../contexts/OfficeContext'
import { json, request, supabase, message } from '../services/officeApi'
import PageLoader from '../components/PageLoader'

function WelcomeFrame({ children }: { children: ReactNode }) {
  return <main className="welcome-page"><section className="welcome-story"><div className="welcome-brand"><div className="brand-collaboration-logos"><img src="/Cosmonet%20Logo.png" alt="Cosmonet AI" /><span>×</span><img src="/GG%20Logo.jpeg" alt="Guru Group" /></div><div><strong>ConnectCosmos</strong><small>Cosmonet AI × Guru Group</small></div></div><div className="welcome-kicker">CONVERSATION-TO-COMMERCE WORKSPACE</div>
    <h1>Turn every visit<br /><span>into the next sale.</span></h1><p>Register walk-in visitors, capture customer conversations, route enquiries, and move from product interest to quotation and order.</p>
    <ol className="welcome-steps"><li><AudioLines /> Capture visitor and sales conversations</li><li><Languages /> Structure products, requirements, and intent</li><li><ShieldCheck /> Follow up with a clear commercial record</li></ol>
  </section><section className="welcome-card">{children}</section></main>
}
export default function Welcome() {
  const office = useOffice()
  const [mode, setMode] = useState<'signin' | 'signup' | 'reset'>('signin')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [workspaceName, setWorkspaceName] = useState('')
  const [selectedDemoAccount, setSelectedDemoAccount] = useState('')
  const [memberMenuOpen, setMemberMenuOpen] = useState(false)
  const [memberSearch, setMemberSearch] = useState('')
  const [memberRole, setMemberRole] = useState<'all' | 'owner' | 'salesperson' | 'receptionist'>('all')
  const memberPickerRef = useRef<HTMLDivElement>(null)
  const selectedAccount = office.demoAccounts.find(account => account.email === selectedDemoAccount)
  const visibleDemoAccounts = useMemo(() => office.demoAccounts.filter(account => {
    const search = memberSearch.trim().toLowerCase()
    const matchesRole = memberRole === 'all' || account.role === memberRole
    const matchesSearch = !search || `${account.name} ${account.email} ${account.branch}`.toLowerCase().includes(search)
    return matchesRole && matchesSearch
  }), [office.demoAccounts, memberRole, memberSearch])
  useEffect(() => {
    if (!memberMenuOpen) return
    const close = (event: MouseEvent) => { if (!memberPickerRef.current?.contains(event.target as Node)) setMemberMenuOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [memberMenuOpen])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (office.localMode) {
      const form = new FormData(event.currentTarget)
      if (mode !== 'signin') { setError('Only demo sign-in is available in frontend-only mode.'); return }
      const accepted = office.signIn(String(form.get('email') ?? ''), String(form.get('password') ?? ''))
      if (!accepted) setError('Use a demo email and the shared password Demo@123.')
      return
    }
    if (!supabase || busy) return
    setBusy(true); setError(''); setNotice('')
    const form = new FormData(event.currentTarget)
    const email = String(form.get('email') ?? '').trim()
    const password = String(form.get('password') ?? '')
    try {
      if (mode === 'reset') {
        const result = await supabase.auth.resetPasswordForEmail(email, { redirectTo: `${location.origin}/reset` })
        if (result.error) throw result.error
        setNotice('If this email has an account, a password reset link will arrive shortly.')
      } else if (mode === 'signup') {
        const result = await supabase.auth.signUp({ email, password, options: { data: { display_name: String(form.get('name') ?? '').trim() }, emailRedirectTo: `${location.origin}/auth/callback` } })
        if (result.error) throw result.error
        setNotice('Check your email to confirm your account, then sign in.')
      } else {
        const result = await supabase.auth.signInWithPassword({ email, password })
        if (result.error) throw result.error
      }
    } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  async function createWorkspace() {
    if (busy) return
    setBusy(true); setError('')
    try {
      await request('/workspaces', null, json('POST', { name: workspaceName.trim() }))
      await office.reloadWorkspaces()
    } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  if (!office.configured) return <WelcomeFrame><h2>Complete your workspace setup</h2><p>ConnectCosmos needs a one-time setup before team accounts, conversation capture, and AI processing are available.</p><p>Ask your administrator to complete the setup, then refresh this page.</p><div className="flow-note">Connection details are managed securely by your administrator.</div></WelcomeFrame>
  if (office.loading || (office.session && office.workspacesLoading)) return <WelcomeFrame><PageLoader kind="dashboard" label="Loading your workspace" detail="Preparing your sales workspace and connected services." /></WelcomeFrame>
  if (office.session) return <WelcomeFrame><h2>Your conversations need a workspace</h2><p>Create a workspace to get started. Team members are added automatically when an administrator sends their email invitation.</p>
    {(error || office.error) && <p role="alert" className="flow-error">{error || office.error}</p>}
    <form onSubmit={event => { event.preventDefault(); void createWorkspace() }}><label>Workspace name<input required minLength={2} maxLength={200} value={workspaceName} onChange={event => setWorkspaceName(event.target.value)} /></label><button className="button button-primary" disabled={busy}>Create workspace <ArrowRight size={16} /></button></form>
    {office.error && <button type="button" disabled={busy} onClick={() => void office.reloadWorkspaces()}>Refresh workspaces</button>}
    <button type="button" onClick={() => void supabase?.auth.signOut()}>Sign out</button></WelcomeFrame>
  if (office.localMode) return <WelcomeFrame><h2>Choose a demo workspace member</h2><p>Select a role to open the existing ConnectCosmos workflow directly. No account or password entry is required for this frontend demo.</p>
    <div className="demo-member-picker" ref={memberPickerRef}>
      <label htmlFor="demo-member-trigger">Demo member</label>
      <button id="demo-member-trigger" type="button" className={`demo-member-trigger${memberMenuOpen ? ' is-open' : ''}`} aria-haspopup="listbox" aria-expanded={memberMenuOpen} onClick={() => setMemberMenuOpen(open => !open)}>
        {selectedAccount ? <span className="demo-member-avatar">{selectedAccount.name.split(' ').map(part => part[0]).join('').slice(0, 2)}</span> : <span className="demo-member-avatar demo-member-avatar-empty"><Search size={16} /></span>}
        <span className="demo-member-trigger-copy"><strong>{selectedAccount?.name ?? 'Select a member'}</strong><small>{selectedAccount ? `${selectedAccount.role === 'salesperson' ? 'Salesperson' : selectedAccount.role} - ${selectedAccount.branch}` : 'Choose by role, name, or branch'}</small></span>
        <ChevronDown className="demo-member-chevron" size={18} />
      </button>
      {memberMenuOpen && <div className="demo-member-menu" role="dialog" aria-label="Choose a demo workspace member">
        <div className="demo-member-search"><Search size={16} /><input autoFocus value={memberSearch} onChange={event => setMemberSearch(event.target.value)} placeholder="Search name, email, or branch" /></div>
        <div className="demo-role-tabs" role="tablist" aria-label="Filter members by role">{(['all', 'owner', 'salesperson', 'receptionist'] as const).map(role => <button key={role} type="button" className={memberRole === role ? 'active' : ''} onClick={() => setMemberRole(role)}>{role === 'all' ? 'All' : role === 'salesperson' ? 'Salespeople' : role === 'owner' ? 'Owners' : 'Receptionists'}</button>)}</div>
        <div className="demo-member-list" role="listbox" aria-label="Demo members">{visibleDemoAccounts.length ? visibleDemoAccounts.map(account => <button key={account.email} type="button" role="option" aria-selected={selectedDemoAccount === account.email} className="demo-member-option" onClick={() => { setSelectedDemoAccount(account.email); setMemberMenuOpen(false); const accepted = office.signIn(account.email, 'Demo@123'); if (!accepted) setError('Choose a valid demo member.') }}>
          <span className="demo-member-avatar">{account.name.split(' ').map(part => part[0]).join('').slice(0, 2)}</span><span className="demo-member-option-copy"><strong>{account.name}</strong><span>{account.email} - {account.branch}</span></span><span className={`demo-role-badge ${account.role}`}>{account.role === 'salesperson' ? 'Sales' : account.role}</span>{selectedDemoAccount === account.email && <Check className="demo-member-check" size={17} />}
        </button>) : <div className="demo-member-empty">No demo members match that search.</div>}</div>
        <div className="demo-member-menu-footer">{visibleDemoAccounts.length} member{visibleDemoAccounts.length === 1 ? '' : 's'} available - selecting one opens its role permissions</div>
      </div>}
    </div>
    {error && <p role="alert" className="flow-error">{error}</p>}<small className="muted">The selected member opens with the permissions for that role.</small>
    <div className="welcome-demo-preview"><div className="welcome-demo-preview-heading"><span>Inside the demo</span><em>Session-only data</em></div><div className="welcome-demo-step"><b>01</b><span><strong>Capture the customer moment</strong><small>Walk-in visitor or sales conversation</small></span></div><div className="welcome-demo-step"><b>02</b><span><strong>Turn interest into a quote</strong><small>Products, discounts, approval, and PDF</small></span></div><div className="welcome-demo-step"><b>03</b><span><strong>Close the next action</strong><small>Follow-up, acceptance, and sales order</small></span></div></div></WelcomeFrame>
  return <WelcomeFrame><h2>{mode === 'signup' ? 'Create your account' : mode === 'reset' ? 'Reset your password' : 'Welcome back'}</h2><p>Use your ConnectCosmos workspace account.</p>
    {(error || office.error) && <p role="alert" className="flow-error">{error || office.error}</p>}{notice && <p role="status" className="flow-note">{notice}</p>}
    <form onSubmit={event => void submit(event)}>{mode === 'signup' && <label>Your name<input name="name" autoComplete="name" required minLength={2} maxLength={120} /></label>}
      <label>Email<input name="email" type="email" autoComplete="email" required maxLength={254} /></label>
      {mode !== 'reset' && <label>Password<input name="password" type="password" autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required minLength={mode === 'signup' ? 12 : 1} maxLength={128} /></label>}
      <button className="button button-primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : mode === 'reset' ? 'Send reset link' : 'Sign in'} <ArrowRight size={16} /></button>
    </form><div className="welcome-links">{(['signin', 'signup', 'reset'] as const).filter(item => item !== mode).map(item => <button key={item} type="button" disabled={busy} onClick={() => { setMode(item); setError(''); setNotice('') }}>{item === 'signin' ? 'Sign in' : item === 'signup' ? 'Create account' : 'Forgot password?'}</button>)}</div></WelcomeFrame>
}
