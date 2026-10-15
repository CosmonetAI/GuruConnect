import { useEffect, useState, type FormEvent } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'
import { json, message, request, supabase, type Member } from '../services/officeApi'
import ProviderConnections from './Integrations'

type Profile = { display_name: string; phone?: string; avatar_path?: string; availability_status?: string; preferences: Record<string, unknown> }

export default function Settings() {
  const { workspace, user, localMode } = useOffice()
  const isOwner = localMode || workspace?.role === 'owner'
  const profile = useScopedResource<Profile>('/profile')
  const team = useScopedResource<{ items: Member[] }>(isOwner && !localMode ? '/team' : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [invite, setInvite] = useState('')
  useEffect(() => { if (window.location.hash === '#connections') document.getElementById('connections')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }, [])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return
    const form = new FormData(event.currentTarget); const name = String(form.get('name') ?? '').trim(); const phone = String(form.get('phone') ?? '').trim(); const availability_status = String(form.get('availability_status') ?? 'active'); const selected = form.get('avatar'); const file = selected instanceof File && selected.size ? selected : null
    setBusy(true); setError(''); setNotice('')
    try { let avatar_path = profile.data?.avatar_path ?? null; if (file && (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024)) throw new Error('Profile picture must be PNG, JPG, or WebP and no larger than 5 MB.'); if (file && supabase && user) { avatar_path = `${user.id}/avatar.${file.name.split('.').pop() || 'jpg'}`; const upload = await supabase.storage.from('profile-avatars').upload(avatar_path, file, { upsert: true, contentType: file.type }); if (upload.error) throw upload.error }; await request('/profile', workspace?.id, json('PATCH', { display_name: name, phone: phone || null, avatar_path, availability_status, preferences: profile.data?.preferences ?? {} })); setNotice('Your profile was saved.'); await profile.reload() } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  async function createInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !workspace) return
    const form = new FormData(event.currentTarget); setBusy(true); setError(''); setInvite('')
    try { const result = await request<{ token: string }>('/invites', workspace.id, json('POST', { email: String(form.get('email')), role: String(form.get('role')) })); setInvite(result.token); setNotice('Invitation created. Share its token with the intended teammate.'); await team.reload() } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  return <div><header className="flow-page-header"><div><div className="page-kicker">WORKSPACE PREFERENCES</div><h1 className="page-title">Team & account</h1><p>{isOwner ? 'Manage your workspace, team, connections, and profile.' : 'Manage your profile.'}</p></div></header>
    {(error || profile.error || team.error) && <p role="alert" className="flow-error">{error || profile.error || team.error}</p>}{notice && <p role="status" className="flow-note">{notice}</p>}
    {localMode && <p className="flow-note">Local mode opens without sign-in. Configure Supabase when you need team accounts.</p>}
    <section className="flow-panel"><h2>Your profile</h2><p>{localMode ? 'Local account' : user?.email} · {workspace?.name} · {workspace?.role}</p>{profile.loading && <p role="status">Loading profile…</p>}<form className="settings-form" key={profile.data?.display_name ?? 'profile'} onSubmit={event => void save(event)}><label>Display name<input name="name" required minLength={2} maxLength={120} defaultValue={profile.data?.display_name ?? user?.user_metadata.display_name ?? ''} /></label><label>Phone number<input name="phone" type="tel" pattern="\+?[0-9][0-9 ()-]{7,19}" defaultValue={profile.data?.phone ?? ''} placeholder="+91 98765 43210" /></label><label>Status<select name="availability_status" defaultValue={profile.data?.availability_status ?? 'active'}><option value="active">Active</option><option value="not_active">Not active</option><option value="on_leave">On leave</option><option value="be_right_back">Be right back</option></select></label><label>Profile picture<input name="avatar" type="file" accept="image/png,image/jpeg,image/webp" /></label><button className="button button-primary" disabled={busy || profile.loading}>Save profile</button></form></section>
    {isOwner && <><section id="connections" className="flow-panel"><h2>Transcription & translation</h2><p>Provider connections and processing policy are managed separately from your personal profile.</p><ProviderConnections /></section>{!localMode && <section className="flow-panel"><h2>Your team</h2>{team.loading && <p role="status">Loading team…</p>}{team.data?.items.map(member => <div className="team-row" key={member.user_id}><span>{member.display_name || member.email || 'Workspace member'}</span><span>{member.role} · {member.status}</span></div>)}<h3>Invite a teammate</h3><form className="settings-form" onSubmit={event => void createInvite(event)}><label>Teammate email<input name="email" type="email" required maxLength={254} /></label><label>Role<select name="role"><option value="salesperson">Salesperson</option><option value="receptionist">Receptionist</option></select></label><button className="button button-primary" disabled={busy}>Invite teammate</button></form>{invite && <label className="settings-form">One-time invitation token<input aria-label="Invitation token" readOnly value={invite} onFocus={event => event.target.select()} /></label>}</section>}</>}
  </div>
}
