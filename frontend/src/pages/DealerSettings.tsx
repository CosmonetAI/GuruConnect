import { useEffect, useState, type FormEvent } from 'react'
import { json, message, request, supabase, type Workspace } from '../services/officeApi'
import { useOffice } from '../contexts/OfficeContext'
import { useScopedResource } from '../hooks/useScopedResource'
import '../styles/dealer.css'

type Config = { account_member_id: string; account_name?: string; books_contact_id: string; discount_percent: number; gst_group_id?: string | null }
type Tax = { tax_id: string; tax_name?: string; tax_display_name?: string; tax_percentage?: number }
const call = <T,>(workspace: Workspace, path: string, method = 'GET', body?: unknown) =>
  request<T>(path, workspace.id, { method, body: body === undefined ? undefined : JSON.stringify(body) }, workspace.member_id)

export default function DealerSettings() {
  const { workspace, user } = useOffice()
  const isOperator = !!workspace && (workspace.actor_role === 'owner' || workspace.actor_role === 'salesperson') && workspace.member_id !== user?.id
  const profile = useScopedResource<{display_name?: string; phone?: string; preferences?: Record<string, unknown>}>(
    !isOperator ? '/profile' : null,
  )
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [config, setConfig] = useState<Config | null>(null)
  const [taxes, setTaxes] = useState<Tax[]>([])
  const [discount, setDiscount] = useState('0')
  const [gstId, setGstId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => {
    setName(profile.data?.display_name || user?.user_metadata?.display_name || '')
    setPhone(profile.data?.phone || '')
  }, [profile.data, user?.id])
  useEffect(() => {
    if (!workspace || !isOperator) return
    let active = true
    void call<Config>(workspace, '/dealer/config').then(result => {
      if (active) { setConfig(result); setDiscount(String(result.discount_percent ?? 0)); setGstId(result.gst_group_id || '') }
    }).catch(failure => { if (active) setError(message(failure)) })
    return () => { active = false }
  }, [workspace?.id, workspace?.member_id, isOperator])
  useEffect(() => {
    if (!workspace || !config) return
    let active = true
    void call<{items: Tax[]}>(workspace, `/dealer/accounts/${encodeURIComponent(config.account_member_id)}/gst-groups`)
      .then(result => { if (active) setTaxes(result.items || []) })
      .catch(failure => { if (active) setError(message(failure)) })
    return () => { active = false }
  }, [workspace?.id, workspace?.member_id, config?.account_member_id])
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!workspace || !config || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await call(workspace, `/dealer/${encodeURIComponent(workspace.member_id || workspace.id)}/mapping`, 'PUT',
        {...config, discount_percent: Number(discount), gst_group_id: gstId || null})
      setNotice('Dealer defaults saved.')
    } catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  async function saveProfile(event: FormEvent) {
    event.preventDefault()
    if (!workspace || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await request('/profile', workspace.id, json('PATCH', {
        display_name: name.trim(), phone: phone.trim() || null,
        preferences: profile.data?.preferences || {},
      }), workspace.member_id)
      setNotice('Profile updated.')
      await profile.reload()
    } catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  async function resetPassword() {
    if (!supabase || !user?.email || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await supabase.auth.resetPasswordForEmail(user.email, { redirectTo: `${window.location.origin}/reset` })
      if (result.error) throw result.error
      setNotice('Password reset instructions have been sent to your email.')
    } catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  async function logout() {
    if (!supabase || busy) return
    setBusy(true); setError('')
    try {
      const result = await supabase.auth.signOut()
      if (result.error) throw result.error
    } catch (failure) { setError(message(failure)); setBusy(false) }
  }
  if (!isOperator) return <section className="dealer-page">
    <header className="dealer-header"><div><span className="dealer-kicker">ACCOUNT</span><h1>Settings</h1><p>Manage your profile, password and account access.</p></div></header>
    {(error || profile.error) && <p role="alert" className="dealer-error">{error || profile.error}</p>}
    {notice && <p role="status" className="dealer-success">{notice}</p>}
    <form className="dealer-panel dealer-setup-panel" onSubmit={event => void saveProfile(event)}>
      <label>Name<input value={name} onChange={event => setName(event.target.value)} required maxLength={120}/></label>
      <label>Email address<input value={user?.email || ''} readOnly/></label>
      <label>Phone number<input type="tel" value={phone} onChange={event => setPhone(event.target.value)} maxLength={24}/></label>
      <button className="dealer-primary" disabled={busy || profile.loading}>{busy ? 'Saving…' : 'Save profile'}</button>
      <button type="button" onClick={() => void resetPassword()} disabled={busy}>Reset password</button>
      <button type="button" onClick={() => void logout()} disabled={busy}>Log out</button>
    </form>
  </section>
  return <section className="dealer-page">
    <header className="dealer-header"><div><span className="dealer-kicker">DEALER CONFIGURATION</span><h1>Settings</h1><p>Manage this Dealer’s invoice account, discount and GST defaults.</p></div></header>
    {(error) && <p role="alert" className="dealer-error">{error}</p>}
    {notice && <p role="status" className="dealer-success">{notice}</p>}
    {config && <form className="dealer-panel dealer-setup-panel" onSubmit={event => void save(event)}>
      <label>Zoho Books account<input value={config.account_name || config.account_member_id} readOnly /></label>
      <label>Dealer contact<input value={config.books_contact_id} readOnly /></label>
      <label>Dealer-wide discount (%)<input type="number" min="0" max="100" step="0.01" value={discount} onChange={event => setDiscount(event.target.value)} required /></label>
      <label>GST Group<select value={gstId} onChange={event => setGstId(event.target.value)}><option value="">No GST Group</option>{taxes.map(tax =>
        <option key={tax.tax_id} value={tax.tax_id}>{tax.tax_display_name || tax.tax_name} ({tax.tax_percentage ?? 0}%)</option>)}</select></label>
      <button className="dealer-primary" disabled={busy}>{busy ? 'Saving…' : 'Save invoice defaults'}</button>
    </form>}
  </section>
}
