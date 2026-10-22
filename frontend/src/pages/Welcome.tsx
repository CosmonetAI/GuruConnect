import { useState, type FormEvent, type ReactNode } from 'react'
import { AudioLines, ArrowRight, ShieldCheck, Languages } from 'lucide-react'
import { useOffice } from '../contexts/OfficeContext'
import { json, request, supabase, message } from '../services/officeApi'
import PageLoader from '../components/PageLoader'

function WelcomeFrame({ children }: { children: ReactNode }) {
  return <main className="welcome-page"><section className="welcome-story"><div className="welcome-brand"><AudioLines /> GuruConnect</div>
    <h1>Keep the conversation.<br /><span>Understand every detail.</span></h1><p>Capture customer conversations, read them in English, and keep the source audio beside your enquiry.</p>
    <ol className="welcome-steps"><li><AudioLines /> Record or upload audio</li><li><Languages /> Review the English transcript</li><li><ShieldCheck /> Save it in your private workspace</li></ol>
  </section><section className="welcome-card">{children}</section></main>
}
export default function Welcome() {
  const office = useOffice()
  const [mode, setMode] = useState<'signin' | 'signup' | 'reset'>('signin')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [workspaceName, setWorkspaceName] = useState('')
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
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
  if (!office.configured) return <WelcomeFrame><h2>Complete your workspace setup</h2><p>GuruConnect needs a one-time setup before team accounts, conversation capture, and AI processing are available.</p><p>Ask your administrator to complete the setup, then refresh this page.</p><div className="flow-note">Connection details are managed securely by your administrator.</div></WelcomeFrame>
  if (office.loading || (office.session && office.workspacesLoading)) return <WelcomeFrame><PageLoader kind="dashboard" label="Loading your workspace" detail="Preparing your sales workspace and connected services." /></WelcomeFrame>
  if (office.session) return <WelcomeFrame><h2>Your conversations need a workspace</h2><p>Create a workspace to get started. Team members are added automatically when an administrator sends their email invitation.</p>
    {(error || office.error) && <p role="alert" className="flow-error">{error || office.error}</p>}
    <form onSubmit={event => { event.preventDefault(); void createWorkspace() }}><label>Workspace name<input required minLength={2} maxLength={200} value={workspaceName} onChange={event => setWorkspaceName(event.target.value)} /></label><button className="button button-primary" disabled={busy}>Create workspace <ArrowRight size={16} /></button></form>
    {office.error && <button type="button" disabled={busy} onClick={() => void office.reloadWorkspaces()}>Refresh workspaces</button>}
    <button type="button" onClick={() => void supabase?.auth.signOut()}>Sign out</button></WelcomeFrame>
  return <WelcomeFrame><h2>{mode === 'signup' ? 'Create your account' : mode === 'reset' ? 'Reset your password' : 'Welcome back'}</h2><p>Use your GuruConnect workspace account.</p>
    {(error || office.error) && <p role="alert" className="flow-error">{error || office.error}</p>}{notice && <p role="status" className="flow-note">{notice}</p>}
    <form onSubmit={event => void submit(event)}>{mode === 'signup' && <label>Your name<input name="name" autoComplete="name" required minLength={2} maxLength={120} /></label>}
      <label>Email<input name="email" type="email" autoComplete="email" required maxLength={254} /></label>
      {mode !== 'reset' && <label>Password<input name="password" type="password" autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required minLength={mode === 'signup' ? 12 : 1} maxLength={128} /></label>}
      <button className="button button-primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : mode === 'reset' ? 'Send reset link' : 'Sign in'} <ArrowRight size={16} /></button>
    </form><div className="welcome-links">{(['signin', 'signup', 'reset'] as const).filter(item => item !== mode).map(item => <button key={item} type="button" disabled={busy} onClick={() => { setMode(item); setError(''); setNotice('') }}>{item === 'signin' ? 'Sign in' : item === 'signup' ? 'Create account' : 'Forgot password?'}</button>)}</div></WelcomeFrame>
}
