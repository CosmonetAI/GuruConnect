import { useState, type FormEvent } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'
import { json, message, request, type Member } from '../services/officeApi'
import { Link } from 'react-router-dom'
export default function Settings() {
  const { workspace, user, localMode } = useOffice()
  const profile = useScopedResource<{ display_name: string; preferences: Record<string, unknown> }>('/profile')
  const team = useScopedResource<{ items: Member[] }>(!localMode && workspace?.role !== 'salesperson' ? '/team' : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [invite, setInvite] = useState('')
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return
    const name = String(new FormData(event.currentTarget).get('name') ?? '').trim()
    setBusy(true); setError(''); setNotice('')
    try { await request('/profile', workspace?.id, json('PATCH', { display_name: name, preferences: profile.data?.preferences ?? {} })); setNotice('Your display name was saved.'); await profile.reload() } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  async function createInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !workspace) return
    const form = new FormData(event.currentTarget)
    setBusy(true); setError(''); setInvite('')
    try { const result = await request<{ token: string }>('/invites', workspace.id, json('POST', { email: String(form.get('email')), role: String(form.get('role')) })); setInvite(result.token); setNotice('Invitation created. Share its token directly with the intended teammate.'); await team.reload() } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  return <div><header className="flow-page-header"><div><div className="page-kicker">WORKSPACE PREFERENCES</div><h1 className="page-title">Team & account</h1><p>Manage your profile and workspace access.</p></div></header>
    {(error || profile.error || team.error) && <p role="alert" className="flow-error">{error || profile.error || team.error}</p>}{notice && <p role="status" className="flow-note">{notice}</p>}
    {localMode && <p className="flow-note">Local mode opens without sign-in. Enquiries and audio are stored on this computer. Connected transcription and translation providers require internet access. Configure your new Supabase project when you need team accounts.</p>}
    <section className="flow-panel"><h2>Your profile</h2><p>{localMode ? 'Local account' : user?.email} · {workspace?.name} · {workspace?.role}</p>{profile.loading && <p role="status">Loading profile…</p>}<form className="settings-form" key={profile.data?.display_name ?? 'profile'} onSubmit={event => void save(event)}><label>Display name<input name="name" required minLength={2} maxLength={120} defaultValue={profile.data?.display_name ?? user?.user_metadata.display_name ?? ''} /></label><button className="button button-primary" disabled={busy || profile.loading}>Save profile</button></form></section>
    <section className="flow-panel"><h2>Transcription & translation</h2><p>Provider connections and processing policy are managed separately from your personal profile.</p><Link to="/integrations" className="button button-secondary">Manage connections</Link></section>
    {!localMode && workspace?.role !== 'salesperson' && <section className="flow-panel"><h2>Your team</h2>{team.loading && <p role="status">Loading team…</p>}{team.data?.items.map(member => <div className="team-row" key={member.user_id}><span>{member.display_name || member.email || 'Workspace member'}</span><span>{member.role} · {member.status}</span></div>)}<h3>Invite a teammate</h3><form className="settings-form" onSubmit={event => void createInvite(event)}><label>Teammate email<input name="email" type="email" required maxLength={254} /></label><label>Role<select name="role"><option value="salesperson">Salesperson</option>{workspace?.role === 'owner' && <option value="admin">Administrator</option>}</select></label><button className="button button-primary" disabled={busy}>Create invitation</button></form>{invite && <label className="settings-form">One-time invitation token<input aria-label="Invitation token" readOnly value={invite} onFocus={event => event.target.select()} /></label>}</section>}
  </div>
}
