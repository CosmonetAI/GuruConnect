import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'
import { json, message, request, supabase, type Member } from '../services/officeApi'
import ProviderConnections from './Integrations'

type Profile = { display_name: string; phone?: string; avatar_path?: string; availability_status?: string; preferences: Record<string, unknown> }
type ProfileDraft = { name: string; phone: string; availability_status: string }

export default function Settings() {
  const { workspace, user, localMode } = useOffice()
  const isOwner = localMode || workspace?.role === 'owner'
  const profile = useScopedResource<Profile>('/profile')
  const team = useScopedResource<{ items: Member[] }>(isOwner && !localMode ? '/team' : null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<ProfileDraft>({ name: '', phone: '', availability_status: 'active' }), [avatarFile, setAvatarFile] = useState<File | null>(null)
  useEffect(() => { if (profile.data) { setDraft({ name: profile.data.display_name ?? '', phone: profile.data.phone ?? '', availability_status: profile.data.availability_status ?? 'active' }); setAvatarFile(null) } }, [profile.data])
  const profileChanged = useMemo(() => !!avatarFile || draft.name.trim() !== (profile.data?.display_name ?? '') || draft.phone.trim() !== (profile.data?.phone ?? '') || draft.availability_status !== (profile.data?.availability_status ?? 'active'), [avatarFile, draft, profile.data])
  useEffect(() => { if (window.location.hash === '#connections') document.getElementById('connections')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }, [])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !workspace) return
    const name = draft.name.trim(), phone = draft.phone.trim(), file = avatarFile
    setBusy(true); setError(''); setNotice('')
    try {
      let avatar_path = profile.data?.avatar_path ?? null
      if (file && (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024)) throw new Error('Profile picture must be PNG, JPG, or WebP and no larger than 5 MB.')
      if (file && supabase && user) { avatar_path = `${user.id}/avatar.${file.name.split('.').pop() || 'jpg'}`; const upload = await supabase.storage.from('profile-avatars').upload(avatar_path, file, { upsert: true, contentType: file.type }); if (upload.error) throw upload.error }
      await request('/profile', workspace.id, json('PATCH', { display_name: name, phone: phone || null, avatar_path, availability_status: draft.availability_status, preferences: profile.data?.preferences ?? {} }))
      setNotice('Your profile was updated.'); await profile.reload(); window.dispatchEvent(new Event('guruconnect-profile-updated'))
    } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  async function createInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !workspace) return
    const form = new FormData(event.currentTarget); setBusy(true); setError('')
    try { await request('/invites', workspace.id, json('POST', { name: String(form.get('name')), email: String(form.get('email')), role: String(form.get('role')) })); setNotice('Invitation sent. The member will appear in the workspace immediately and can set their password from the email link.'); await team.reload(); event.currentTarget.reset() } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  return <div><header className="flow-page-header"><div><div className="page-kicker">WORKSPACE PREFERENCES</div><h1 className="page-title">Team & account</h1><p>{isOwner ? 'Manage your workspace, team, connections, and profile.' : 'Manage your profile.'}</p></div></header>
    {(error || profile.error || team.error) && <p role="alert" className="flow-error">{error || profile.error || team.error}</p>}{notice && <p role="status" className="flow-note">{notice}</p>}
    {localMode && <p className="flow-note">Local mode opens without sign-in. Configure Supabase when you need team accounts.</p>}
    <section className="flow-panel"><h2>Your profile</h2><p>{localMode ? 'Local account' : user?.email} · {workspace?.name} · {workspace?.role}</p>{profile.loading && <p role="status">Loading profile…</p>}<form className="settings-form" onSubmit={event => void save(event)}><label>Display name<input name="name" required minLength={2} maxLength={120} value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label><label>Phone number<input name="phone" type="tel" pattern="\+?[0-9][0-9 ()-]{7,19}" value={draft.phone} onChange={event => setDraft({ ...draft, phone: event.target.value })} placeholder="+91 98765 43210" /></label><label>Status<select name="availability_status" value={draft.availability_status} onChange={event => setDraft({ ...draft, availability_status: event.target.value })}><option value="active">Active</option><option value="not_active">Not active</option><option value="on_leave">On leave</option><option value="be_right_back">Be right back</option></select></label><label>Profile picture<input name="avatar" type="file" accept="image/png,image/jpeg,image/webp" onChange={event => setAvatarFile(event.target.files?.[0] ?? null)} />{avatarFile && <small>{avatarFile.name}</small>}</label><button className="button button-primary" disabled={busy || profile.loading || !profileChanged}>Update profile</button></form></section>
    {isOwner && <><section id="connections" className="flow-panel"><h2>Transcription & translation</h2><p>Provider connections and processing policy are managed separately from your personal profile.</p><ProviderConnections /></section>{!localMode && <section className="flow-panel"><h2>Your team</h2>{team.loading && <p role="status">Loading team…</p>}{team.data?.items.map(member => <div className="team-row" key={member.user_id}><span>{member.display_name || member.email || 'Workspace member'}</span><span>{member.role} · {member.status}</span></div>)}<h3>Invite a teammate</h3><p>Send an email invitation with the member’s role. They’ll set a password from the link and can sign in directly.</p><form className="settings-form" onSubmit={event => void createInvite(event)}><label>Full name<input name="name" required minLength={2} maxLength={120} /></label><label>Teammate email<input name="email" type="email" required maxLength={254} /></label><label>Role<select name="role"><option value="owner">Owner</option><option value="salesperson">Salesperson</option><option value="receptionist">Receptionist</option></select></label><button className="button button-primary" disabled={busy}>Send invitation</button></form></section>}</>}
  </div>
}
